// Voice capture: the Pi's USB microphone (offline Vosk), this device's microphone recorded and
// transcribed on the Pi, or the browser's own speech recognition. All three end in the same
// {text, fields, parser} result.
import { api } from "./core.js";

export async function voiceCapabilities() {
  let st = {};
  try { st = await api("voice/status"); } catch { /* offline */ }
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const secure = window.isSecureContext;
  const media = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  return {
    pi: !!st.pi_mic,
    record: !!st.server_stt && secure && media,
    speech: !!SR && secure,
    secure,
  };
}

export const MODE_LABEL = { pi: "Pi microphone", record: "This mic, offline", speech: "This mic, browser" };

// Pi microphone first, then this device transcribed offline on the server, then the browser's own
// recogniser (often more accurate, but Chrome sends the audio to Google and needs internet).
export function preferredModes(caps) {
  const out = [];
  if (caps.pi) out.push("pi");
  if (caps.record) out.push("record");
  if (caps.speech) out.push("speech");
  return out;
}

export function listen({ mode, lang = "en-IN", onPartial = () => {}, maxSeconds = 12 }) {
  if (mode === "pi") return listenPi({ onPartial, maxSeconds });
  if (mode === "record") return listenRecord({ onPartial, maxSeconds });
  return listenSpeech({ lang, onPartial, maxSeconds });
}

// ---------------------------------------------------------------- Pi microphone
function listenPi({ onPartial, maxSeconds }) {
  const session = Math.random().toString(36).slice(2);
  const onEv = (e) => {
    const { kind, data } = e.detail;
    if (kind === "voice" && data.session === session) onPartial(data.text);
  };
  document.addEventListener("medos:event", onEv);
  const promise = api("voice/listen", { method: "POST", body: { session, seconds: maxSeconds } })
    .finally(() => document.removeEventListener("medos:event", onEv));
  return { promise, stop() { /* the Pi stops by itself on silence */ } };
}

// ---------------------------------------------------------------- browser speech recognition
function listenSpeech({ lang, onPartial, maxSeconds }) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const rec = new SR();
  rec.lang = lang; rec.interimResults = true; rec.continuous = true; rec.maxAlternatives = 1;
  let finalText = "", interim = "", silence = null, hardStop = null, stopped = false;
  const promise = new Promise((resolve, reject) => {
    const finish = async () => {
      clearTimeout(silence); clearTimeout(hardStop);
      const text = (finalText + " " + interim).trim();
      if (!text) return reject(new Error("We didn't catch that. Try again and speak a little closer."));
      try { resolve(await api("voice/parse", { method: "POST", body: { text } })); } catch (e) { reject(e); }
    };
    rec.onresult = (ev) => {
      interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) finalText += " " + r[0].transcript; else interim += r[0].transcript;
      }
      onPartial((finalText + " " + interim).trim());
      clearTimeout(silence);
      silence = setTimeout(() => rec.stop(), 2200);
    };
    rec.onerror = (e) => {
      stopped = true;
      const msg = { "not-allowed": "Microphone access was blocked. Allow it in the browser's site settings.",
        "network": "This browser's speech service needs internet. Switch to the Pi microphone instead.",
        "no-speech": "We didn't hear anything. Try again.", "audio-capture": "No microphone was found on this device." }[e.error];
      reject(new Error(msg || "Speech recognition stopped (" + e.error + ")"));
    };
    rec.onend = () => { if (!stopped) { stopped = true; finish(); } };
    hardStop = setTimeout(() => rec.stop(), maxSeconds * 1000);
    rec.start();
  });
  return { promise, stop() { rec.stop(); } };
}

// ---------------------------------------------------------------- record here, transcribe on the Pi
function listenRecord({ onPartial, maxSeconds }) {
  let stopFn = () => {};
  const promise = (async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = ctx.createMediaStreamSource(stream);
    const node = ctx.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    // Adaptive voice detection: learn the room's background level first, then treat
    // anything clearly louder as speech. Quiet laptop mics work too.
    let heard = 0, start = performance.now(), done, noise = null, calib = [];
    const finished = new Promise((r) => (done = r));
    node.onaudioprocess = (e) => {
      const data = e.inputBuffer.getChannelData(0);
      chunks.push(new Float32Array(data));
      let sum = 0; for (let i = 0; i < data.length; i += 4) sum += data[i] * data[i];
      const rms = Math.sqrt(sum / (data.length / 4));
      const now = performance.now();
      if (noise === null) { calib.push(rms); if (now - start > 400) noise = Math.min(...calib); }
      const threshold = Math.max(0.004, (noise || 0.004) * 2.5);
      if (noise !== null && rms > threshold) heard = now;
      const bar = "▮".repeat(Math.min(24, Math.round((rms / threshold) * 4)));
      onPartial(`${heard ? "Hearing you" : "Listening, start speaking"}  ${bar}`);
      if ((heard && now - heard > 2200) || now - start > maxSeconds * 1000 || (!heard && now - start > 10000)) done();
    };
    src.connect(node); node.connect(ctx.destination);
    stopFn = done;
    await finished;
    node.disconnect(); src.disconnect(); stream.getTracks().forEach((t) => t.stop());
    const rate = ctx.sampleRate; await ctx.close();
    onPartial("Transcribing on the Pi…");
    const pcm = downsample(concat(chunks), rate, 16000);
    // Boost quiet recordings so the speech model can hear them.
    let peak = 0; for (let i = 0; i < pcm.length; i++) peak = Math.max(peak, Math.abs(pcm[i]));
    if (peak > 0.001 && peak < 0.8) { const g = Math.min(20, 0.8 / peak); for (let i = 0; i < pcm.length; i++) pcm[i] *= g; }
    const wav = encodeWav(pcm, 16000);
    return api("voice/transcribe", { method: "POST", body: wav, headers: { "Content-Type": "audio/wav" } });
  })();
  return { promise, stop() { stopFn(); } };
}

function concat(chunks) {
  const len = chunks.reduce((a, c) => a + c.length, 0);
  const out = new Float32Array(len); let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out;
}
function downsample(buf, from, to) {
  if (from === to) return buf;
  const ratio = from / to, len = Math.floor(buf.length / ratio), out = new Float32Array(len);
  for (let i = 0; i < len; i++) {
    const s = Math.floor(i * ratio), e = Math.min(buf.length, Math.floor((i + 1) * ratio));
    let sum = 0; for (let j = s; j < e; j++) sum += buf[j];
    out[i] = sum / Math.max(1, e - s);
  }
  return out;
}
function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) { const s = Math.max(-1, Math.min(1, samples[i])); v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true); }
  return new Blob([buf], { type: "audio/wav" });
}
