"""Qwen 3 through Ollama — second-opinion triage, voice-to-form extraction, doctor briefs, shift reports.

The Pi reaches the laptop's Ollama over the network (set OLLAMA_HOST=0.0.0.0 on the laptop,
see laptop/enable-ollama-lan.ps1). Every AI feature is optional: the rules engine keeps
the hospital running when the model is unreachable.
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from . import db, triage


class LLMError(Exception):
    pass


def _base_url() -> str:
    return (db.get_setting("llm_url") or "http://localhost:11434").rstrip("/")


def _http(url: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 10) -> Dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            msg = json.loads(body).get("error", body)
        except ValueError:
            msg = body
        raise LLMError("Ollama returned %s: %s" % (e.code, msg[:200]))
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise LLMError("Can't reach Ollama at %s (%s)" % (url.split("/api")[0], reason))


def _clip(text: Any, n: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


class LLM:
    def __init__(self):
        self._status: Dict[str, Any] = {}
        self._status_at = 0.0
        self.calls = 0
        self.failures = 0
        self.last_latency: Optional[float] = None
        self.busy = 0

    # ------------------------------------------------------------ status
    def enabled(self) -> bool:
        return bool(db.get_setting("llm_enabled"))

    def status(self, force: bool = False) -> Dict[str, Any]:
        if not force and time.time() - self._status_at < 10 and self._status:
            return self._status
        url = _base_url()
        model = db.get_setting("llm_model")
        st: Dict[str, Any] = {"enabled": self.enabled(), "url": url, "model": model, "online": False,
                              "models": [], "model_ready": False, "error": None,
                              "calls": self.calls, "failures": self.failures,
                              "last_latency": self.last_latency, "busy": self.busy}
        if self.enabled():
            try:
                tags = _http(url + "/api/tags", timeout=2.5)
                names = [m.get("name") for m in tags.get("models", [])]
                st.update(online=True, models=names,
                          model_ready=model in names or (model + ":latest") in names)
                if not st["model_ready"]:
                    st["error"] = "Model %s is not pulled on that machine (ollama pull %s)" % (model, model)
            except LLMError as e:
                st["error"] = str(e)
        self._status, self._status_at = st, time.time()
        return st

    # ------------------------------------------------------------ core call
    def chat(self, messages: List[Dict[str, str]], schema: Optional[Dict[str, Any]] = None,
             timeout: Optional[float] = None, temperature: float = 0.2) -> str:
        if not self.enabled():
            raise LLMError("AI is turned off in Admin → AI assistant")
        payload: Dict[str, Any] = {
            "model": db.get_setting("llm_model"), "messages": messages, "stream": False,
            "think": False, "keep_alive": "30m",
            "options": {"temperature": temperature, "num_ctx": 4096},
        }
        if schema:
            payload["format"] = schema
        timeout = timeout or float(db.get_setting("llm_timeout") or 120)
        t0 = time.time()
        self.calls += 1
        self.busy += 1
        try:
            try:
                res = _http(_base_url() + "/api/chat", payload, timeout)
            except LLMError as e:
                if "think" in str(e).lower():
                    payload.pop("think", None)
                    res = _http(_base_url() + "/api/chat", payload, timeout)
                else:
                    raise
        except LLMError:
            self.failures += 1
            raise
        finally:
            self.busy -= 1
        self.last_latency = round(time.time() - t0, 2)
        content = (res.get("message") or {}).get("content", "")
        return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()

    def chat_json(self, messages: List[Dict[str, str]], schema: Dict[str, Any],
                  timeout: Optional[float] = None) -> Dict[str, Any]:
        text = self.chat(messages, schema=schema, timeout=timeout, temperature=0.1)
        try:
            return json.loads(text)
        except ValueError:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                return json.loads(m.group(0))
            raise LLMError("The model did not return JSON")

    # ------------------------------------------------------------ tasks
    def triage(self, p: Dict[str, Any], protocol: List[Dict[str, Any]]) -> Dict[str, Any]:
        table = "\n".join("- %s → %s%s" % (c["name"], c["level"], " (red flag)" if c.get("red_flag") else "")
                          for c in protocol)
        vitals = p.get("vitals") or {}
        vit = ", ".join("%s %s" % (k, v) for k, v in vitals.items()) or "not recorded"
        system = (
            "You are the triage assistant of a hospital emergency department in India. "
            "Classify how urgently the patient must see a doctor, using exactly one level:\n"
            "critical = life-threatening now (seen immediately)\n"
            "high = urgent, could deteriorate (within ~15 min)\n"
            "medium = needs attention but stable (within ~60 min)\n"
            "low = non-urgent, can wait.\n"
            "Hospital protocol:\n%s\n"
            "Rules: be conservative — if torn between two levels pick the more urgent one. "
            "Consider age, pregnancy and vital signs. Never invent symptoms. Answer in JSON: a summary of at "
            "most 18 words, red flags, reasoning of at most 35 words, the department, then the level that "
            "your reasoning supports." % table)
        user = ("Patient: age %s, sex %s%s.\nVital signs: %s.\nComplaint (may be a voice transcript): \"%s\"\n"
                "The rules engine chose: %s (%s)." % (
                    p.get("age") or "unknown", p.get("sex") or "unknown",
                    ", pregnant" if p.get("pregnant") else "", vit, p.get("symptoms") or "",
                    p.get("rules_level") or p.get("level"), p.get("primary_condition") or "no match"))
        # Reasoning comes before the level so the model thinks first, then commits.
        schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "One-line clinical summary, max 18 words"},
                "red_flags": {"type": "array", "items": {"type": "string"}},
                "reasoning": {"type": "string", "description": "Why this level, max 35 words"},
                "department": {"type": "string"},
                "level": {"type": "string", "enum": list(triage.LEVELS)},
            },
            "required": ["summary", "red_flags", "reasoning", "department", "level"],
        }
        out = self.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}], schema)
        lvl = str(out.get("level", "")).lower().strip()
        out["level"] = lvl if lvl in triage.LEVELS else None
        out["summary"] = _clip(out.get("summary"), 160)
        out["reasoning"] = _clip(out.get("reasoning"), 320)
        out["department"] = _clip(out.get("department"), 40).title() if out.get("department") else None
        return out

    def extract_registration(self, transcript: str) -> Dict[str, Any]:
        system = (
            "You turn a spoken hospital check-in into a registration form. The speaker may be the patient "
            "or a relative, may mix English with Tamil or Hindi, and speech recognition may contain mistakes. "
            "Return JSON. Use an empty string, 0 or 'unknown' when a field was not said. 'symptoms' must be a "
            "short English description of the complaint, including duration and severity words that were "
            "said. Keep names as spoken, with capital letters.")
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
                "sex": {"type": "string", "enum": ["male", "female", "other", "unknown"]},
                "phone": {"type": "string"},
                "symptoms": {"type": "string"},
                "pregnant": {"type": "boolean"},
            },
            "required": ["name", "age", "sex", "phone", "symptoms", "pregnant"],
        }
        out = self.chat_json([{"role": "system", "content": system},
                              {"role": "user", "content": transcript}], schema, timeout=60)
        return {
            "name": (out.get("name") or "").strip() or None,
            "age": out.get("age") if isinstance(out.get("age"), int) and 0 < out["age"] < 120 else None,
            "sex": out.get("sex") if out.get("sex") in ("male", "female", "other") else None,
            "phone": re.sub(r"[^\d+]", "", out.get("phone") or "") or None,
            "symptoms": (out.get("symptoms") or "").strip(),
            "pregnant": bool(out.get("pregnant")),
        }

    # Free-text replies from small Qwen 3 models tend to "think aloud" even with thinking off,
    # so every task asks for JSON and the text is assembled here.
    @staticmethod
    def _sections(out: Dict[str, Any], names: List[tuple]) -> str:
        parts = []
        for key, title in names:
            items = out.get(key) or []
            if isinstance(items, str):
                items = [items]
            items = [_clip(i, 200) for i in items if str(i).strip()][:4]
            if items:
                parts.append(title + "\n" + "\n".join("- " + i for i in items))
        return "\n".join(parts)

    @staticmethod
    def _list_schema(keys: List[str]) -> Dict[str, Any]:
        return {"type": "object",
                "properties": {k: {"type": "array", "items": {"type": "string"}, "maxItems": 4} for k in keys},
                "required": keys}

    def doctor_brief(self, p: Dict[str, Any]) -> str:
        vit = ", ".join("%s %s" % (k, v) for k, v in (p.get("vitals") or {}).items()) or "not recorded"
        system = (
            "You help an emergency doctor prepare for a consultation. You are decision support, not a "
            "diagnosis. Answer in JSON with 2 to 4 short bullet phrases (under 14 words each) for: summary, "
            "ask_about (questions for the patient), rule_out (dangerous causes to exclude), first_checks "
            "(examinations or tests to start with).")
        user = ("Age %s, sex %s%s. Triage level %s (%s). Vitals: %s.\nComplaint: %s\nTriage notes: %s" % (
            p.get("age") or "?", p.get("sex") or "?", ", pregnant" if p.get("pregnant") else "",
            p.get("level"), p.get("primary_condition"), vit, p.get("symptoms"),
            "; ".join(p.get("reasons") or [])))
        keys = ["summary", "ask_about", "rule_out", "first_checks"]
        out = self.chat_json([{"role": "system", "content": system}, {"role": "user", "content": user}],
                             self._list_schema(keys))
        return self._sections(out, [("summary", "Summary"), ("ask_about", "Ask about"),
                                    ("rule_out", "Rule out"), ("first_checks", "First checks")])

    def shift_report(self, stats: Dict[str, Any]) -> str:
        system = (
            "You write the end-of-shift handover note for a hospital emergency department manager. Use the "
            "JSON statistics only and never invent numbers. Answer in JSON with 2 to 4 short bullet sentences "
            "for: overview, bottlenecks, emergencies, suggestions.")
        keys = ["overview", "bottlenecks", "emergencies", "suggestions"]
        out = self.chat_json([{"role": "system", "content": system},
                              {"role": "user", "content": json.dumps(stats, default=str)}],
                             self._list_schema(keys))
        return self._sections(out, [("overview", "Overview"), ("bottlenecks", "Bottlenecks"),
                                    ("emergencies", "Emergencies"), ("suggestions", "Suggestions")])

    def ask(self, question: str, stats: Dict[str, Any]) -> str:
        system = ("You answer questions from hospital staff about today's emergency department using only the "
                  "JSON data provided. Be brief and specific, at most three sentences. If the data doesn't "
                  "answer it, say so. Answer in JSON.")
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
        out = self.chat_json([{"role": "system", "content": system},
                              {"role": "user", "content": "Data: %s\n\nQuestion: %s" % (
                                  json.dumps(stats, default=str), question)}], schema)
        return _clip(out.get("answer"), 700)

    def ping(self) -> str:
        schema = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}
        out = self.chat_json([{"role": "user", "content": "Set reply to exactly: MedOS link OK"}], schema, timeout=90)
        return _clip(out.get("reply"), 60)

    # ------------------------------------------------------------ discovery
    def discover(self) -> List[Dict[str, Any]]:
        """Find Ollama servers on the local network (ARP neighbours first, then each /24)."""
        candidates: List[str] = ["127.0.0.1"]
        try:
            with open("/proc/net/arp") as f:
                for line in f.readlines()[1:]:
                    ip = line.split()[0]
                    candidates.append(ip)
        except OSError:
            pass
        for ip in local_ipv4():
            net = ipaddress.ip_network(ip + "/24", strict=False)
            if net.is_loopback or ip.startswith("169.254"):
                continue
            candidates.extend(str(h) for h in net.hosts() if str(h) != ip)
        seen, ordered = set(), []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                ordered.append(c)

        def probe(host: str) -> Optional[str]:
            try:
                with socket.create_connection((host, 11434), timeout=0.4):
                    return host
            except OSError:
                return None

        found = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=64, thread_name_prefix="ollama-scan") as ex:
            for host in ex.map(probe, ordered):
                if host:
                    found.append(host)
        out = []
        for host in found:
            url = "http://%s:11434" % host
            try:
                tags = _http(url + "/api/tags", timeout=2)
                out.append({"url": url, "models": [m.get("name") for m in tags.get("models", [])]})
            except LLMError:
                pass
        return out


def local_ipv4() -> List[str]:
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        import psutil  # type: ignore
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith("127."):
                    ips.append(a.address)
    except Exception:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ips.append(info[4][0])
        except OSError:
            pass
    seen, out = set(), []
    for ip in ips:
        if ip not in seen and not ip.startswith("127."):
            seen.add(ip)
            out.append(ip)
    return out


llm = LLM()


class AIWorker:
    """Producer/consumer: registrations enqueue patient ids, one worker thread asks the model."""

    def __init__(self):
        self.q: "queue.Queue[int]" = queue.Queue(maxsize=200)
        self.processed = 0
        self.failed = 0
        self.current: Optional[int] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="ai-triage-worker", daemon=True)
        self._thread.start()

    def submit(self, pid: int) -> None:
        try:
            self.q.put_nowait(pid)
        except queue.Full:
            pass

    def _run(self) -> None:
        from .scheduler import scheduler
        while True:
            pid = self.q.get()
            self.current = pid
            try:
                p = scheduler.get(pid)
                if not p or p["status"] not in ("waiting", "in_consultation"):
                    continue
                result = llm.triage(p, scheduler.conditions())
                scheduler.apply_ai(pid, result)
                self.processed += 1
            except Exception as exc:
                self.failed += 1
                try:
                    scheduler.ai_failed(pid, str(exc))
                except Exception:
                    pass
            finally:
                self.current = None
                self.q.task_done()

    def stats(self) -> Dict[str, Any]:
        return {"queued": self.q.qsize(), "processed": self.processed, "failed": self.failed,
                "current": self.current}


ai_worker = AIWorker()
