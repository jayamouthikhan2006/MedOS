"""Dynamic Priority Scheduler.

Patients are processes, doctors are CPUs.

    effective = base_score (level + protocol rank + modifiers)
              + min(aging_rate * minutes_waiting, aging_cap)     # aging → no starvation
              → lifted to PREEMPT_FLOOR if the patient was pre-empted (just below Critical)
              + EMERGENCY_BONUS if an emergency override (interrupt) was raised

The ready queue is a binary heap rebuilt from live scores. Every mutation happens
under one instrumented re-entrant lock so two receptionists or two doctors can never
grab the same patient (process synchronisation).
"""
from __future__ import annotations

import heapq
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import db, triage
from .events import bus, log

PREEMPT_FLOOR = 1450.0     # pre-empted patients resume before every non-critical case
EMERGENCY_BONUS = 5000.0
CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class SchedulerError(Exception):
    pass


class InstrumentedLock:
    """Re-entrant lock that records how often threads had to wait for it."""

    def __init__(self, name: str):
        self.name = name
        self._lock = threading.RLock()
        self.acquisitions = 0
        self.contended = 0
        self.total_wait = 0.0
        self.max_wait = 0.0
        self.owner: Optional[str] = None
        self._depth = 0

    def acquire(self) -> None:
        if not self._lock.acquire(blocking=False):
            t0 = time.perf_counter()
            self._lock.acquire()
            waited = time.perf_counter() - t0
            self.contended += 1
            self.total_wait += waited
            self.max_wait = max(self.max_wait, waited)
        self.acquisitions += 1
        self._depth += 1
        self.owner = threading.current_thread().name

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            self.owner = None
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def stats(self) -> Dict[str, Any]:
        return {"name": self.name, "acquisitions": self.acquisitions, "contended": self.contended,
                "total_wait_ms": round(self.total_wait * 1000, 3),
                "max_wait_ms": round(self.max_wait * 1000, 3), "owner": self.owner}


def _code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


class Scheduler:
    def __init__(self):
        self.lock = InstrumentedLock("ready-queue")
        self.hardware = None                       # set by app (hardware.Hardware)
        self.ai_hook: Optional[Callable[[int], None]] = None
        self.dispatches = 0
        self.preemptions = 0
        self.interrupts = 0
        self.ticks = 0
        self.started_at = time.time()
        self._last_order: List[int] = []
        self._last_fixed: Dict[int, float] = {}
        self._conditions_cache: Optional[List[Dict[str, Any]]] = None
        self._durations_cache: Optional[Dict[str, float]] = None
        self._durations_at = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------ protocol
    def conditions(self) -> List[Dict[str, Any]]:
        if self._conditions_cache is None:
            self._conditions_cache = db.rows("SELECT * FROM conditions WHERE active=1 ORDER BY rank, id")
        return self._conditions_cache

    def invalidate_conditions(self) -> None:
        self._conditions_cache = None

    # ------------------------------------------------------------ scoring
    @staticmethod
    def score_parts(p: Dict[str, Any], t: Optional[float] = None) -> Dict[str, float]:
        t = t or time.time()
        wait_min = max(0.0, (t - p["arrived_at"]) / 60.0)
        rate = float(db.get_setting("aging_rate") or 0)
        cap = float(db.get_setting("aging_cap") or 0)
        aging = min(rate * wait_min, cap)
        base = float(p["base_score"])
        pre = max(0.0, PREEMPT_FLOOR - base - aging) if p.get("preempted") else 0.0
        em = EMERGENCY_BONUS if p.get("emergency") else 0.0
        return {"base": round(base, 1), "aging": round(aging, 1), "preempt": pre, "emergency": em,
                "total": round(base + aging + pre + em, 1)}

    def ready_queue(self, t: Optional[float] = None) -> List[Dict[str, Any]]:
        """Waiting patients in dispatch order (heap of -score, arrival, id)."""
        t = t or time.time()
        heap = []
        for p in db.rows("SELECT * FROM patients WHERE status='waiting'"):
            db.decode_patient(p)
            parts = self.score_parts(p, t)
            p["score"] = parts
            heap.append((-parts["total"], p["arrived_at"], p["id"], p))
        heapq.heapify(heap)
        ordered = []
        while heap:
            ordered.append(heapq.heappop(heap)[3])
        for i, p in enumerate(ordered):
            p["position"] = i + 1
        return ordered

    # ------------------------------------------------------------ durations & ETA
    def durations(self) -> Dict[str, float]:
        """Expected consultation minutes per level: learned from today, defaults otherwise."""
        if self._durations_cache and time.time() - self._durations_at < 30:
            return self._durations_cache
        out = {}
        for lvl in triage.LEVELS:
            default = float(db.get_setting("dur_" + lvl) or 10)
            r = db.row("SELECT COUNT(*) n, AVG(consult_seconds) a FROM patients WHERE status='completed' "
                       "AND level=? AND day=? AND consult_seconds > 30", (lvl, db.today()))
            if r and r["n"] and r["n"] >= 3:
                learned = r["a"] / 60.0
                out[lvl] = round((learned * r["n"] + default * 3) / (r["n"] + 3), 1)
            else:
                out[lvl] = default
        self._durations_cache, self._durations_at = out, time.time()
        return out

    def estimates(self, queue: List[Dict[str, Any]], t: Optional[float] = None) -> Dict[int, Optional[float]]:
        """Multi-server simulation: seconds until each waiting patient reaches a doctor."""
        t = t or time.time()
        durs = self.durations()
        docs = db.rows("SELECT d.status, p.level cur_level, p.called_at cur_called FROM doctors d "
                       "LEFT JOIN patients p ON p.id = d.current_patient_id "
                       "WHERE d.status IN ('available','busy')")
        if not docs:
            return {p["id"]: None for p in queue}
        free_at = []
        for d in docs:
            if d["status"] == "busy" and d["cur_called"]:
                expected = durs.get(d["cur_level"] or "low", 10) * 60
                free_at.append(t + max(60.0, expected - (t - d["cur_called"])))
            else:
                free_at.append(t)
        heapq.heapify(free_at)
        eta = {}
        for p in queue:
            f = heapq.heappop(free_at)
            eta[p["id"]] = max(0.0, f - t)
            heapq.heappush(free_at, f + durs.get(p["level"], 10) * 60)
        return eta

    # ------------------------------------------------------------ registration
    def register(self, data: Dict[str, Any], source: str = "reception",
                 arrived_at: Optional[float] = None, dispatch: bool = True) -> Dict[str, Any]:
        symptoms = (data.get("symptoms") or "").strip()
        vitals = {k: v for k, v in (data.get("vitals") or {}).items() if v not in (None, "")}
        pregnant = bool(data.get("pregnant"))
        age = data.get("age")
        try:
            age = int(age) if age not in (None, "") else None
        except (TypeError, ValueError):
            age = None
        result = triage.analyse(symptoms, age, data.get("sex"), vitals, pregnant, self.conditions())
        level, rank, base = result["level"], result["rank"], result["base_score"]
        reasons = result["reasons"]
        level_source = "rules"
        override = data.get("level_override")
        if override in triage.LEVELS and override != level:
            level_source = "manual"
            reasons = reasons + ["Level set to %s by staff" % triage.LEVEL_LABEL[override]]
            rank = 0 if override == "critical" else rank
            level = override
            base = triage.base_score(level, rank, result["bonus"])
        t = arrived_at or time.time()
        with self.lock:
            seq = (db.scalar("SELECT COUNT(*) FROM patients WHERE day=?", (db.today(),)) or 0) + 1
            token = "%03d" % seq
            code = _code()
            while db.scalar("SELECT 1 FROM patients WHERE code=?", (code,)):
                code = _code()
            pid = db.insert("patients", {
                "token": token, "code": code, "day": db.today(),
                "name": (data.get("name") or "").strip() or None, "age": age,
                "sex": data.get("sex") or None, "phone": (data.get("phone") or "").strip() or None,
                "symptoms": symptoms, "vitals": db.encode(vitals), "pregnant": 1 if pregnant else 0,
                "level": level, "rank": rank, "base_score": base, "rules_level": result["level"],
                "level_source": level_source,
                "primary_condition": result["primary_condition"],
                "conditions": db.encode(result["conditions"]), "red_flags": db.encode(result["red_flags"]),
                "reasons": db.encode(reasons), "department": result["department"],
                "ai_status": "pending" if self._ai_on() and not data.get("emergency") else "off",
                "source": source, "emergency": 1 if data.get("emergency") else 0,
                "status": "waiting", "arrived_at": t,
            })
            p = self.get(pid)
            log("ARRIVE", "Token %s registered via %s — %s (%s), base priority %d" % (
                token, source, triage.LEVEL_LABEL[level], result["primary_condition"], base), pid,
                {"level": level, "score": base})
            if dispatch:
                self.dispatch(reason="arrival")
                if level == "critical" and db.get_setting("preemption") == "critical":
                    p = self.get(pid)
                    if p["status"] == "waiting":
                        self._preempt_for(p)
        bus.publish("queue", {"reason": "arrival", "patient_id": pid, "token": token})
        if self.ai_hook and self._ai_on() and symptoms and not data.get("emergency"):
            self.ai_hook(pid)
        return self.get(pid)

    def _ai_on(self) -> bool:
        return bool(db.get_setting("llm_enabled")) and bool(db.get_setting("ai_triage"))

    def get(self, pid: int) -> Optional[Dict[str, Any]]:
        return db.decode_patient(db.row("SELECT * FROM patients WHERE id=?", (pid,)))

    def update_patient(self, pid: int, data: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            p = self.get(pid)
            if not p:
                raise SchedulerError("Patient not found")
            fields: Dict[str, Any] = {}
            for k in ("name", "sex", "phone", "symptoms"):
                if k in data:
                    fields[k] = (data[k] or "").strip() or None
            if "age" in data:
                try:
                    fields["age"] = int(data["age"]) if data["age"] not in (None, "") else None
                except (TypeError, ValueError):
                    fields["age"] = None
            if "vitals" in data:
                fields["vitals"] = db.encode({k: v for k, v in (data["vitals"] or {}).items() if v not in (None, "")})
            if "pregnant" in data:
                fields["pregnant"] = 1 if data["pregnant"] else 0
            retriage = any(k in data for k in ("symptoms", "age", "vitals", "pregnant", "level_override"))
            if retriage:
                merged = dict(p)
                merged.update(fields)
                vit = merged["vitals"]
                vit = db.decode_patient({"vitals": vit})["vitals"] if isinstance(vit, str) else vit
                res = triage.analyse(merged.get("symptoms") or "", merged.get("age"), merged.get("sex"),
                                     vit or {}, bool(merged.get("pregnant")), self.conditions())
                level, rank, base, reasons = res["level"], res["rank"], res["base_score"], res["reasons"]
                source = "rules"
                # Keep an earlier AI upgrade unless staff override.
                if p.get("ai_level") in triage.LEVELS and triage.LEVEL_ORDER[p["ai_level"]] < triage.LEVEL_ORDER[level]:
                    level = p["ai_level"]
                    rank = 0 if level == "critical" else rank
                    source = "ai"
                    reasons = reasons + ["AI second opinion: %s" % triage.LEVEL_LABEL[level]]
                ov = data.get("level_override")
                if ov in triage.LEVELS:
                    level, source = ov, "manual"
                    rank = 0 if ov == "critical" else rank
                    reasons = reasons + ["Level set to %s by staff" % triage.LEVEL_LABEL[ov]]
                if p["emergency"]:
                    level, rank = "critical", 0
                fields.update({
                    "level": level, "rank": rank, "base_score": triage.base_score(level, rank, res["bonus"]),
                    "rules_level": res["level"], "level_source": source,
                    "primary_condition": res["primary_condition"], "department": res["department"],
                    "conditions": db.encode(res["conditions"]), "red_flags": db.encode(res["red_flags"]),
                    "reasons": db.encode(reasons),
                })
            db.update("patients", pid, fields)
            p2 = self.get(pid)
            if retriage and p2["level"] != p["level"]:
                log("RETRIAGE", "Token %s re-triaged %s → %s" % (
                    p["token"], triage.LEVEL_LABEL[p["level"]], triage.LEVEL_LABEL[p2["level"]]), pid)
            self.dispatch(reason="update")
        bus.publish("queue", {"reason": "update", "patient_id": pid})
        if retriage and "symptoms" in data and self.ai_hook and self._ai_on():
            db.update("patients", pid, {"ai_status": "pending"})
            self.ai_hook(pid)
        return self.get(pid)

    def cancel(self, pid: int, reason: str = "left") -> None:
        with self.lock:
            p = self.get(pid)
            if not p or p["status"] != "waiting":
                raise SchedulerError("Only waiting patients can be removed from the queue")
            db.update("patients", pid, {"status": "cancelled", "completed_at": time.time(), "outcome": reason})
            log("CANCEL", "Token %s left the queue (%s)" % (p["token"], reason), pid)
        bus.publish("queue", {"reason": "cancel", "patient_id": pid})

    # ------------------------------------------------------------ dispatch
    def dispatch(self, reason: str = "") -> List[Dict[str, Any]]:
        """Give the head of the ready queue to each free doctor (longest idle first)."""
        if not db.get_setting("auto_dispatch"):
            return []
        out = []
        with self.lock:
            doctors = db.rows("SELECT * FROM doctors WHERE status='available' AND current_patient_id IS NULL "
                              "ORDER BY status_since, id")
            if not doctors:
                return []
            queue = self.ready_queue()
            for d in doctors:
                if not queue:
                    break
                out.append(self._assign(d, queue.pop(0)))
        return out

    def call_next(self, doctor_id: int) -> Dict[str, Any]:
        with self.lock:
            d = self.doctor(doctor_id)
            if d["current_patient_id"]:
                raise SchedulerError("Finish the current consultation first")
            queue = self.ready_queue()
            if not queue:
                raise SchedulerError("No one is waiting")
            if d["status"] != "available":
                db.update("doctors", doctor_id, {"status": "available", "status_since": time.time()})
                d = self.doctor(doctor_id)
            return self._assign(d, queue[0])

    def _assign(self, d: Dict[str, Any], p: Dict[str, Any], preempt: bool = False) -> Dict[str, Any]:
        t = time.time()
        wait = p["wait_seconds"] if p.get("wait_seconds") is not None else t - p["arrived_at"]
        db.update("patients", p["id"], {"status": "in_consultation", "doctor_id": d["id"],
                                        "room_id": d["room_id"], "called_at": t, "wait_seconds": wait})
        db.update("doctors", d["id"], {"status": "busy", "current_patient_id": p["id"], "status_since": t})
        self.dispatches += 1
        room = db.row("SELECT name FROM rooms WHERE id=?", (d["room_id"],)) if d["room_id"] else None
        room_name = room["name"] if room else "the consultation desk"
        score = p.get("score", {}).get("total") if isinstance(p.get("score"), dict) else None
        log("DISPATCH", "Token %s (%s%s) → %s · %s, waited %d min" % (
            p["token"], triage.LEVEL_LABEL[p["level"]], ", score %d" % score if score else "",
            d["name"], room_name, wait // 60), p["id"], {"doctor_id": d["id"]})
        info = {"patient_id": p["id"], "token": p["token"], "level": p["level"], "doctor_id": d["id"],
                "doctor": d["name"], "room": room_name, "emergency": bool(p["emergency"]),
                "preempt": preempt}
        bus.publish("called", info)
        if self.hardware and db.get_setting("buzz_on_call"):
            self.hardware.chime()
        return info

    # ------------------------------------------------------------ doctors
    def doctor(self, doctor_id: int) -> Dict[str, Any]:
        d = db.row("SELECT * FROM doctors WHERE id=?", (doctor_id,))
        if not d:
            raise SchedulerError("Doctor not found")
        return d

    def set_doctor_status(self, doctor_id: int, status: str) -> Dict[str, Any]:
        if status not in ("available", "break", "off_duty"):
            raise SchedulerError("Unknown status")
        with self.lock:
            d = self.doctor(doctor_id)
            if d["current_patient_id"] and status != "available":
                raise SchedulerError("Finish the current consultation first")
            if d["current_patient_id"]:
                return d
            db.update("doctors", doctor_id, {"status": status, "status_since": time.time()})
            log("DOCTOR", "%s is now %s" % (d["name"], status.replace("_", " ")))
            self.dispatch(reason="doctor")
        bus.publish("doctors", {"doctor_id": doctor_id, "status": status})
        return self.doctor(doctor_id)

    def finish(self, doctor_id: int, outcome: str = "", notes: str = "",
               next_status: str = "available", medicines: str = "") -> Dict[str, Any]:
        if next_status not in ("available", "break", "off_duty"):
            next_status = "available"
        with self.lock:
            d = self.doctor(doctor_id)
            if not d["current_patient_id"]:
                raise SchedulerError("No patient in consultation")
            p = self.get(d["current_patient_id"])
            t = time.time()
            consult = (p["consult_seconds"] or 0) + (t - (p["called_at"] or t))
            db.update("patients", p["id"], {"status": "completed", "completed_at": t,
                                            "consult_seconds": consult, "outcome": outcome or "Treated",
                                            "doctor_notes": notes or None})
            db.update("doctors", doctor_id, {"status": next_status, "current_patient_id": None,
                                             "status_since": t})
            log("COMPLETE", "Token %s finished with %s after %d min — %s" % (
                p["token"], d["name"], consult // 60, outcome or "Treated"), p["id"])
            self._durations_cache = None
            self.dispatch(reason="finish")
        if (medicines or "").strip():
            from . import pharmacy   # prescriptions go to the FCFS pharmacy queue
            pharmacy.create_order(medicines, p["id"], p["token"], p["name"], d["name"])
        bus.publish("queue", {"reason": "finish", "patient_id": p["id"]})
        bus.publish("doctors", {"doctor_id": doctor_id})
        return p

    def no_show(self, doctor_id: int) -> None:
        with self.lock:
            d = self.doctor(doctor_id)
            if not d["current_patient_id"]:
                raise SchedulerError("No patient in consultation")
            p = self.get(d["current_patient_id"])
            db.update("patients", p["id"], {"status": "no_show", "completed_at": time.time(),
                                            "outcome": "Did not attend"})
            db.update("doctors", doctor_id, {"status": "available", "current_patient_id": None,
                                             "status_since": time.time()})
            log("NO_SHOW", "Token %s did not come to %s" % (p["token"], d["name"]), p["id"])
            self.dispatch(reason="no-show")
        bus.publish("queue", {"reason": "no_show"})

    def recall(self, doctor_id: int) -> None:
        d = self.doctor(doctor_id)
        if not d["current_patient_id"]:
            raise SchedulerError("No patient to call")
        p = self.get(d["current_patient_id"])
        db.update("patients", p["id"], {"recalls": (p["recalls"] or 0) + 1})
        room = db.row("SELECT name FROM rooms WHERE id=?", (d["room_id"],)) if d["room_id"] else None
        bus.publish("called", {"patient_id": p["id"], "token": p["token"], "level": p["level"],
                               "doctor_id": d["id"], "doctor": d["name"],
                               "room": room["name"] if room else "the consultation desk",
                               "recall": True, "emergency": bool(p["emergency"])})

    # ------------------------------------------------------------ interrupts
    def raise_emergency(self, source: str, name: Optional[str] = None, complaint: Optional[str] = None,
                        patient_id: Optional[int] = None) -> Dict[str, Any]:
        """Interrupt: push an emergency to the front, pre-empting a doctor if needed."""
        t = time.time()
        with self.lock:
            self.interrupts += 1
            if patient_id:
                p = self.get(patient_id)
                if not p:
                    raise SchedulerError("Patient not found")
                if p["status"] == "waiting":
                    reasons = (p["reasons"] or []) + ["Emergency override (%s)" % source]
                    db.update("patients", p["id"], {"emergency": 1, "level": "critical", "rank": 0,
                                                    "level_source": "manual",
                                                    "base_score": triage.base_score("critical", 0),
                                                    "reasons": db.encode(reasons)})
                    p = self.get(p["id"])
            else:
                where = {"gpio": "hardware button", "reception": "reception desk", "kiosk": "kiosk",
                         "doctor": "doctor's room"}.get(source, source)
                p = self.register({"name": name or "Emergency patient",
                                   "symptoms": complaint or "Emergency alert raised from the %s" % where,
                                   "emergency": True, "level_override": "critical"},
                                  source="emergency-" + source, arrived_at=t, dispatch=False)
            label = "Token %s" % p["token"]
            msg = "Emergency — %s" % label
            alert_id = db.insert("alerts", {"ts": t, "source": source, "message": msg, "patient_id": p["id"]})
            irq = "GPIO%s edge interrupt" % getattr(self.hardware, "pin_button", "?") if source == "gpio" \
                else "software interrupt from %s" % source
            log("INTERRUPT", "%s → emergency override for %s, queue pre-empted" % (irq, label), p["id"])
            if p["status"] == "waiting":
                self.dispatch(reason="emergency")
                p = self.get(p["id"])
                if p["status"] == "waiting" and db.get_setting("preemption") != "off":
                    self._preempt_for(p)
                    p = self.get(p["id"])
        if self.hardware:
            self.hardware.alarm_on()
        bus.publish("emergency", {"alert_id": alert_id, "patient_id": p["id"], "token": p["token"],
                                  "message": msg, "source": source, "status": p["status"]})
        bus.publish("queue", {"reason": "emergency", "patient_id": p["id"]})
        return p

    def _preempt_for(self, p: Dict[str, Any]) -> bool:
        busy = db.rows("SELECT d.*, p.id pid FROM doctors d JOIN patients p ON p.id=d.current_patient_id "
                       "WHERE d.status='busy'")
        victims = []
        for d in busy:
            cur = self.get(d["pid"])
            if cur["emergency"] or cur["level"] == "critical":
                continue
            victims.append((self.score_parts(cur)["total"], cur, d))
        if not victims:
            log("PREEMPT", "No doctor can be pre-empted — all are treating critical cases. Token %s waits at the head."
                % p["token"], p["id"])
            return False
        victims.sort(key=lambda v: v[0])
        _, cur, d = victims[0]
        t = time.time()
        consult = (cur["consult_seconds"] or 0) + (t - (cur["called_at"] or t))
        db.update("patients", cur["id"], {"status": "waiting", "preempted": 1, "doctor_id": None,
                                          "room_id": None, "consult_seconds": consult})
        db.update("doctors", d["id"], {"status": "available", "current_patient_id": None, "status_since": t})
        self.preemptions += 1
        log("PREEMPT", "%s pre-empted: Token %s (%s) returned to queue head, Token %s takes the room" % (
            d["name"], cur["token"], triage.LEVEL_LABEL[cur["level"]], p["token"]), cur["id"])
        fresh = self.get(p["id"])
        fresh["score"] = self.score_parts(fresh)
        self._assign(self.doctor(d["id"]), fresh, preempt=True)
        bus.publish("preempted", {"doctor_id": d["id"], "doctor": d["name"], "victim_token": cur["token"],
                                  "victim_id": cur["id"], "token": p["token"]})
        return True

    def acknowledge(self, alert_id: Optional[int], by: str) -> None:
        t = time.time()
        if alert_id:
            db.execute("UPDATE alerts SET acknowledged_at=?, acknowledged_by=? WHERE id=? AND acknowledged_at IS NULL",
                       (t, by, alert_id))
        else:
            db.execute("UPDATE alerts SET acknowledged_at=?, acknowledged_by=? WHERE acknowledged_at IS NULL", (t, by))
        remaining = db.scalar("SELECT COUNT(*) FROM alerts WHERE acknowledged_at IS NULL")
        if not remaining and self.hardware:
            self.hardware.alarm_off()
        log("ALERT", "Emergency alert %s acknowledged by %s" % ("#%d" % alert_id if alert_id else "(all)", by))
        bus.publish("alert_ack", {"alert_id": alert_id, "remaining": remaining})

    # ------------------------------------------------------------ AI results
    def apply_ai(self, pid: int, result: Dict[str, Any]) -> None:
        with self.lock:
            p = self.get(pid)
            if not p:
                return
            ai_level = result.get("level")
            fields = {"ai_status": "done", "ai_level": ai_level if ai_level in triage.LEVELS else None,
                      "ai_summary": result.get("summary"), "ai_reasoning": result.get("reasoning"),
                      "ai_department": result.get("department")}
            upgraded = False
            if (ai_level in triage.LEVELS and p["status"] == "waiting" and p["level_source"] != "manual"
                    and triage.LEVEL_ORDER[ai_level] < triage.LEVEL_ORDER[p["level"]]):
                bonus = p["base_score"] - triage.base_score(p["level"], p["rank"])
                rank = 0 if ai_level == "critical" else p["rank"]
                reasons = (p["reasons"] or []) + ["AI second opinion raised to %s: %s" % (
                    triage.LEVEL_LABEL[ai_level], (result.get("reasoning") or "")[:140])]
                fields.update({"level": ai_level, "rank": rank, "level_source": "ai",
                               "base_score": triage.base_score(ai_level, rank, max(0.0, bonus)),
                               "reasons": db.encode(reasons)})
                upgraded = True
            db.update("patients", pid, fields)
            if upgraded:
                log("AI", "Qwen raised Token %s %s → %s (%s)" % (
                    p["token"], triage.LEVEL_LABEL[p["level"]], triage.LEVEL_LABEL[ai_level],
                    (result.get("reasoning") or "")[:90]), pid)
                self.dispatch(reason="ai")
                if ai_level == "critical" and db.get_setting("preemption") == "critical":
                    p2 = self.get(pid)
                    if p2["status"] == "waiting":
                        self._preempt_for(p2)
            else:
                log("AI", "Qwen reviewed Token %s: %s — agrees or lower, rules level kept" % (
                    p["token"], triage.LEVEL_LABEL.get(ai_level, "no opinion")), pid, publish=False)
        bus.publish("queue", {"reason": "ai", "patient_id": pid})

    def ai_failed(self, pid: int, error: str) -> None:
        db.update("patients", pid, {"ai_status": "error", "ai_reasoning": error[:300]})
        bus.publish("queue", {"reason": "ai", "patient_id": pid})

    # ------------------------------------------------------------ aging daemon
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="aging-daemon", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # keep the daemon alive
                try:
                    log("ERROR", "Aging daemon: %s" % exc, publish=False)
                except Exception:
                    pass
            self._stop.wait(max(3, int(db.get_setting("tick_seconds") or 15)))

    def tick(self) -> None:
        with self.lock:
            self.ticks += 1
            queue = self.ready_queue()
            order = [p["id"] for p in queue]
            fixed = {p["id"]: p["score"]["total"] - p["score"]["aging"] for p in queue}
            prev_index = {pid: i for i, pid in enumerate(self._last_order)}
            by_id = {p["id"]: p for p in queue}
            for i in range(len(order) - 1):
                a, b = order[i], order[i + 1]
                if a in prev_index and b in prev_index and prev_index[a] > prev_index[b] \
                        and self._last_fixed.get(a) == fixed[a] and self._last_fixed.get(b) == fixed[b]:
                    pa, pb = by_id[a], by_id[b]
                    log("AGING", "Token %s (%s, waited %d min, +%d aging) overtook Token %s (%s)" % (
                        pa["token"], triage.LEVEL_LABEL[pa["level"]], (time.time() - pa["arrived_at"]) // 60,
                        pa["score"]["aging"], pb["token"], triage.LEVEL_LABEL[pb["level"]]), a)
            self._last_order, self._last_fixed = order, fixed
            self.dispatch(reason="tick")
        bus.publish("tick", {"n": self.ticks})


scheduler = Scheduler()
