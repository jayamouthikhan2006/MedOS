"""Offline speech recognition (Vosk) from the Pi's USB microphone or an uploaded WAV,
plus a rules-based parser that turns a check-in sentence into form fields.

Vosk is optional: without it, browsers that support the Web Speech API still work.
"""
from __future__ import annotations

import array
import io
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from typing import Any, Callable, Dict, Optional

from . import config

SAMPLE_RATE = 16000


class VoiceError(Exception):
    pass


class Voice:
    def __init__(self):
        self._model = None
        self._whisper = None
        self.whisper_name = None
        self._model_lock = threading.Lock()
        self.mic_lock = threading.Lock()   # mutual exclusion: one recording at a time
        self.listening = False
        self.transcriptions = 0
        self.model_loaded_at: Optional[float] = None

    # ------------------------------------------------------------ capability
    def vosk_available(self) -> bool:
        try:
            import vosk  # noqa: F401  type: ignore
        except Exception:
            return False
        return config.VOSK_MODEL_PATH.exists()

    def mic_available(self) -> bool:
        return shutil.which("arecord") is not None and self._has_capture_device()

    @staticmethod
    def _has_capture_device() -> bool:
        try:
            out = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=3).stdout
            return "card" in out
        except Exception:
            return False

    def status(self) -> Dict[str, Any]:
        stt = self.vosk_available() or self.whisper_available()
        return {"server_stt": stt, "pi_mic": stt and self.mic_available(),
                "engine": "whisper" if self.whisper_available() else ("vosk" if stt else None),
                "model_loaded": self._model is not None or self._whisper is not None, "model_path": str(config.VOSK_MODEL_PATH),
                "listening": self.listening, "transcriptions": self.transcriptions}

    def _get_model(self):
        with self._model_lock:
            if self._model is None:
                if not self.vosk_available():
                    raise VoiceError("Offline speech model is not installed on this device")
                import vosk  # type: ignore
                vosk.SetLogLevel(-1)
                self._model = vosk.Model(str(config.VOSK_MODEL_PATH))   # lazy: ~60 MB RAM once loaded
                self.model_loaded_at = time.time()
            return self._model

    # ------------------------------------------------------------ Whisper (accurate, preferred)
    def whisper_available(self) -> bool:
        if os.environ.get("MEDOS_WHISPER", "1") == "0":
            return False
        try:
            import faster_whisper  # noqa: F401  type: ignore
            return True
        except Exception:
            return False

    def _get_whisper(self):
        with self._model_lock:
            if self._whisper is None:
                from faster_whisper import WhisperModel  # type: ignore
                on_pi = os.path.exists("/proc/device-tree/model")
                name = os.environ.get("MEDOS_WHISPER_MODEL", "base.en" if on_pi else "small.en")
                self._whisper = WhisperModel(name, device="cpu", compute_type="int8",
                                             download_root=str(config.DATA_DIR / "whisper"))
                self.whisper_name = name
                self.model_loaded_at = time.time()
            return self._whisper

    PROMPT = ("Patient check-in at a hospital in India. My name is Ravi Kumar, I am 45 years old. "
              "Symptoms: fever, headache, stomach pain, vomiting, chest pain, breathing difficulty, fracture.")

    def whisper_pcm(self, pcm16: bytes, rate: int = SAMPLE_RATE) -> str:
        import numpy as np  # installed with faster-whisper
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if rate != SAMPLE_RATE:
            idx = (np.arange(int(len(audio) * SAMPLE_RATE / rate)) * rate / SAMPLE_RATE).astype(int)
            audio = audio[idx]
        segments, _ = self._get_whisper().transcribe(
            audio, language="en", beam_size=5, vad_filter=True, initial_prompt=self.PROMPT,
            condition_on_previous_text=False)
        self.transcriptions += 1
        return " ".join(s.text.strip() for s in segments).strip()

    def unload(self) -> None:
        with self._model_lock:
            self._model = None
            self.model_loaded_at = None

    # ------------------------------------------------------------ recognition
    def listen(self, max_seconds: float = 12, on_partial: Optional[Callable[[str], None]] = None) -> str:
        """Record from the USB mic until ~1.6 s of silence after speech, then return the text."""
        if not self.mic_lock.acquire(blocking=False):
            raise VoiceError("The microphone is already in use")
        proc = None
        try:
            if self.whisper_available():
                return self._listen_whisper(max_seconds, on_partial)
            import vosk  # type: ignore
            rec = vosk.KaldiRecognizer(self._get_model(), SAMPLE_RATE)
            proc = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1",
                                     "-t", "raw", "-d", str(int(max_seconds) + 1)],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.listening = True
            start = time.time()
            heard_at = None
            last_partial = ""
            parts = []
            while time.time() - start < max_seconds:
                chunk = proc.stdout.read(3200)          # 0.1 s
                if not chunk:
                    break
                loud = rms(chunk) > 500
                if loud:
                    heard_at = time.time()
                if rec.AcceptWaveform(chunk):
                    text = json.loads(rec.Result()).get("text", "")
                    if text:
                        parts.append(text)
                        if on_partial:
                            on_partial(" ".join(parts))
                else:
                    partial = json.loads(rec.PartialResult()).get("partial", "")
                    if partial and partial != last_partial:
                        last_partial = partial
                        if on_partial:
                            on_partial(" ".join(parts + [partial]))
                if heard_at and time.time() - heard_at > 1.6 and (parts or last_partial):
                    break
                if not heard_at and time.time() - start > 6:
                    break
            final = json.loads(rec.FinalResult()).get("text", "")
            if final:
                parts.append(final)
            self.transcriptions += 1
            return " ".join(p for p in parts if p).strip()
        finally:
            self.listening = False
            if proc:
                proc.kill()
            self.mic_lock.release()

    def _listen_whisper(self, max_seconds: float, on_partial) -> str:
        proc = subprocess.Popen(["arecord", "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1",
                                 "-t", "raw", "-d", str(int(max_seconds) + 1)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.listening = True
        try:
            start, heard_at, buf, noise = time.time(), None, bytearray(), []
            while time.time() - start < max_seconds:
                chunk = proc.stdout.read(3200)
                if not chunk:
                    break
                buf += chunk
                level = rms(chunk)
                if len(noise) < 4:
                    noise.append(level)
                    continue
                if level > max(300.0, min(noise) * 2.5):
                    heard_at = time.time()
                    if on_partial:
                        on_partial("Hearing you…")
                if heard_at and time.time() - heard_at > 2.0:
                    break
                if not heard_at and time.time() - start > 10:
                    break
        finally:
            proc.kill()
            self.listening = False
        if on_partial:
            on_partial("Turning speech into text…")
        return self.whisper_pcm(bytes(buf))

    def transcribe_wav(self, data: bytes) -> str:
        try:
            wf = wave.open(io.BytesIO(data), "rb")
        except (wave.Error, EOFError) as e:
            raise VoiceError("Audio must be a 16-bit mono WAV file (%s)" % e)
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise VoiceError("Audio must be 16-bit mono")
        if self.whisper_available():
            return self.whisper_pcm(wf.readframes(wf.getnframes()), wf.getframerate())
        import vosk  # type: ignore
        rec = vosk.KaldiRecognizer(self._get_model(), wf.getframerate())
        parts = []
        while True:
            chunk = wf.readframes(4000)
            if not chunk:
                break
            if rec.AcceptWaveform(chunk):
                parts.append(json.loads(rec.Result()).get("text", ""))
        parts.append(json.loads(rec.FinalResult()).get("text", ""))
        self.transcriptions += 1
        return " ".join(p for p in parts if p).strip()


def rms(chunk: bytes) -> float:
    a = array.array("h", chunk[: len(chunk) - len(chunk) % 2])
    if not a:
        return 0.0
    return math.sqrt(sum(s * s for s in a) / len(a))


# ---------------------------------------------------------------- text → fields

UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
         "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90}


def words_to_digits(text: str) -> str:
    """'forty five years' → '45 years'; 'one hundred and two' → '102'. Offline STT speaks in words."""
    tokens = re.findall(r"[a-zA-Z]+|\d+|[^\sa-zA-Z\d]+|\s+", text)
    out, i = [], 0

    def is_num(tok: str) -> bool:
        t = tok.lower()
        return t in UNITS or t in TENS or t == "hundred"

    while i < len(tokens):
        tok = tokens[i]
        if not is_num(tok):
            out.append(tok)
            i += 1
            continue
        total, current, j, last_j = 0, 0, i, i
        while j < len(tokens):
            t = tokens[j].lower()
            if t.isspace() or (t == "and" and current):
                j += 1
                continue
            if t in UNITS:
                current += UNITS[t]
            elif t in TENS:
                current += TENS[t]
            elif t == "hundred":
                current = max(current, 1) * 100
            else:
                break
            last_j = j
            j += 1
        total += current
        # Digit-by-digit phone numbers: "nine eight four ..." → keep concatenated
        seq = [tokens[k].lower() for k in range(i, last_j + 1) if tokens[k].strip() and tokens[k].lower() != "and"]
        if len(seq) >= 6 and all(s in UNITS for s in seq):
            out.append("".join(str(UNITS[s]) for s in seq))
        else:
            out.append(str(total))
        i = last_j + 1
    return "".join(out)


NAME_STOP = {"having", "suffering", "feeling", "not", "very", "a", "an", "the", "sick", "in", "here",
             "with", "experiencing", "getting", "unable", "so", "really", "from", "coming", "bleeding",
             "pregnant", "male", "female", "okay", "fine", "going", "also", "age", "aged"}


def parse_checkin(text: str) -> Dict[str, Any]:
    """Best-effort field extraction without a model."""
    raw = words_to_digits(text or "")
    low = raw.lower()
    out: Dict[str, Any] = {"name": None, "age": None, "sex": None, "phone": None, "symptoms": raw.strip(),
                           "pregnant": False}

    name_span = None
    m = re.search(r"\b(?:my name is|name is|this is|i am|i'm|im|call me)\s+([a-z][a-z.]*(?:\s+[a-z][a-z.]*){0,2})", low)
    if m:
        kept = []
        end = m.start(1)
        for w in re.finditer(r"[a-z.]+", m.group(1)):
            if w.group(0) in NAME_STOP or w.group(0) in ("and", "i", "my", "years", "year"):
                break
            kept.append(w.group(0))
            end = m.start(1) + w.end()
        if kept:
            out["name"] = " ".join(w.capitalize() for w in kept)
            name_span = (m.start(), end)

    m = re.search(r"\b(\d{1,3})\s*(?:years?|yrs?|yr)(?:\s*old)?\b", low) or \
        re.search(r"\b(?:age|aged)\s*(?:is\s*)?(\d{1,3})\b", low)
    if m and 0 < int(m.group(1)) < 120:
        out["age"] = int(m.group(1))

    if re.search(r"\b(female|woman|lady|girl|she|her|mother|wife|daughter|pregnant)\b", low):
        out["sex"] = "female"
    elif re.search(r"\b(male|man|boy|he|his|father|husband|son)\b", low):
        out["sex"] = "male"
    if re.search(r"\bpregnan", low):
        out["pregnant"] = True

    m = re.search(r"(\+?\d[\d\s-]{8,14}\d)", raw)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if 10 <= len(digits) <= 13:
            out["phone"] = digits

    # Symptoms: drop the identity clauses so the complaint reads cleanly.
    sym = raw
    if name_span:   # raw and low have the same length, so the span cuts the same words
        sym = sym[:name_span[0]] + " " + sym[name_span[1]:]
    sym = re.sub(r"(?i)\b(?:i am|i'm)\s+\d{1,3}\s*(?:years?|yrs?)(?:\s*old)?", "", sym)
    sym = re.sub(r"(?i)\b(?:my )?(?:phone|mobile|number)\s*(?:number)?\s*(?:is)?\s*\+?[\d\s-]{8,16}", "", sym)
    sym = re.sub(r"\s{2,}", " ", sym).strip(" ,.")
    if sym:
        out["symptoms"] = sym[0].upper() + sym[1:]
    return out


voice = Voice()
