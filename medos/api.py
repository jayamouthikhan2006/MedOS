"""REST + Server-Sent Events API."""
from __future__ import annotations

import csv
import io
import socket
import threading
import time
from functools import wraps
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, jsonify, request, session, stream_with_context

from . import config, db, sysmon, triage
from .events import bus, log, recent
from .llm import LLMError, ai_worker, llm, local_ipv4
from .scheduler import SchedulerError, scheduler
from .voice import VoiceError, parse_checkin, voice

api = Blueprint("api", __name__, url_prefix="/api")
hardware = None   # set by app factory

ROLE_NAMES = {"admin": "Admin", "reception": "Reception", "doctor": "Doctor"}


# ---------------------------------------------------------------- helpers

class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@api.errorhandler(ApiError)
def _api_error(e: ApiError):
    return jsonify({"error": str(e)}), e.status


@api.errorhandler(SchedulerError)
def _sched_error(e: SchedulerError):
    return jsonify({"error": str(e)}), 409


@api.errorhandler(LLMError)
def _llm_error(e: LLMError):
    return jsonify({"error": str(e)}), 503


@api.errorhandler(VoiceError)
def _voice_error(e: VoiceError):
    return jsonify({"error": str(e)}), 503


def body() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def current_role() -> Optional[str]:
    if not db.get_setting("require_pin"):
        return session.get("role") or "admin"
    return session.get("role")


def need(*roles: str):
    """Allow the listed roles; admin may do everything."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            role = current_role()
            if role is None:
                raise ApiError("Sign in to continue", 401)
            if role != "admin" and role not in roles:
                raise ApiError("Only %s can do that" % (" or ".join(ROLE_NAMES[r] for r in roles) or "Admin"), 403)
            return fn(*a, **kw)
        return wrapper
    return deco


STAFF = ("reception", "doctor")


def room_names() -> Dict[int, str]:
    return {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM rooms")}


def patient_view(p: Dict[str, Any], rooms: Dict[int, str], doctors: Dict[int, str]) -> Dict[str, Any]:
    keep = ("id", "token", "code", "name", "age", "sex", "phone", "symptoms", "vitals", "pregnant", "level",
            "rank", "base_score", "primary_condition", "conditions", "red_flags", "reasons", "department",
            "rules_level", "level_source", "ai_status", "ai_level", "ai_summary", "ai_reasoning",
            "ai_department", "ai_brief", "source", "emergency", "preempted", "status", "doctor_id", "room_id",
            "arrived_at", "called_at", "completed_at", "wait_seconds", "consult_seconds", "recalls", "outcome",
            "doctor_notes", "score", "position", "eta")
    out = {k: p.get(k) for k in keep if k in p}
    out["room"] = rooms.get(p.get("room_id"))
    out["doctor"] = doctors.get(p.get("doctor_id"))
    return out


# ---------------------------------------------------------------- state

def kpis(t: float, queue: List[Dict[str, Any]], docs: List[Dict[str, Any]],
         rooms: List[Dict[str, Any]]) -> Dict[str, Any]:
    day = db.today()
    called = db.row("SELECT COUNT(*) n, AVG(wait_seconds) a, MAX(wait_seconds) m FROM patients "
                    "WHERE day=? AND wait_seconds IS NOT NULL", (day,))
    waits_now = [t - p["arrived_at"] for p in queue]
    counts = {r["status"]: r["n"] for r in db.rows(
        "SELECT status, COUNT(*) n FROM patients WHERE day=? GROUP BY status", (day,))}
    by_level = {lvl: 0 for lvl in triage.LEVELS}
    for p in queue:
        by_level[p["level"]] += 1
    active_alerts = db.scalar("SELECT COUNT(*) FROM alerts WHERE acknowledged_at IS NULL") or 0
    return {
        "doctors_total": len(docs),
        "doctors_on_duty": sum(1 for d in docs if d["status"] in ("available", "busy")),
        "doctors_available": sum(1 for d in docs if d["status"] == "available"),
        "doctors_busy": sum(1 for d in docs if d["status"] == "busy"),
        "rooms_total": sum(1 for r in rooms if r["status"] == "open"),
        "rooms_available": sum(1 for r in rooms if r["status"] == "open" and not r["occupied"]),
        "queue_length": len(queue),
        "in_consultation": counts.get("in_consultation", 0),
        "avg_wait_today": called["a"] if called and called["n"] else None,
        "avg_wait_now": sum(waits_now) / len(waits_now) if waits_now else None,
        "longest_wait_now": max(waits_now) if waits_now else None,
        "called_today": called["n"] if called else 0,
        "registered_today": sum(counts.values()),
        "completed_today": counts.get("completed", 0),
        "left_today": counts.get("cancelled", 0) + counts.get("no_show", 0),
        "emergencies_today": db.scalar("SELECT COUNT(*) FROM alerts WHERE ts>=?", (db.day_start(),)) or 0,
        "alerts_active": active_alerts,
        "waiting_by_level": by_level,
    }


def build_state(t: Optional[float] = None) -> Dict[str, Any]:
    t = t or time.time()
    with scheduler.lock:
        queue = scheduler.ready_queue(t)
        eta = scheduler.estimates(queue, t)
        docs = db.rows("SELECT * FROM doctors ORDER BY id")
        rooms = db.rows("SELECT * FROM rooms ORDER BY id")
        consulting = [db.decode_patient(p) for p in db.rows(
            "SELECT * FROM patients WHERE status='in_consultation' ORDER BY called_at")]
        alerts = db.rows("SELECT * FROM alerts WHERE acknowledged_at IS NULL ORDER BY ts DESC")
        calls = db.rows("SELECT id, token, level, emergency, room_id, doctor_id, called_at, status FROM patients "
                        "WHERE called_at IS NOT NULL AND day=? ORDER BY called_at DESC LIMIT 8", (db.today(),))
        seen = {r["doctor_id"]: r for r in db.rows(
            "SELECT doctor_id, COUNT(*) n, AVG(consult_seconds) a FROM patients WHERE status='completed' "
            "AND day=? GROUP BY doctor_id", (db.today(),))}
    rnames = {r["id"]: r["name"] for r in rooms}
    dnames = {d["id"]: d["name"] for d in docs}
    for p in queue:
        p["eta"] = eta.get(p["id"])
    by_id = {p["id"]: p for p in consulting}
    doctors_out = []
    for d in docs:
        cur = by_id.get(d["current_patient_id"])
        doctors_out.append({
            **d, "room": rnames.get(d["room_id"]),
            "current": {"id": cur["id"], "token": cur["token"], "name": cur["name"], "level": cur["level"],
                        "called_at": cur["called_at"], "emergency": cur["emergency"]} if cur else None,
            "seen_today": seen.get(d["id"], {}).get("n", 0),
            "avg_consult": seen.get(d["id"], {}).get("a"),
        })
    occupied = {p["room_id"]: p for p in consulting if p.get("room_id")}
    rooms_out = []
    for r in rooms:
        occ = occupied.get(r["id"])
        owner = next((d for d in docs if d["room_id"] == r["id"]), None)
        rooms_out.append({**r, "occupied": {"token": occ["token"], "level": occ["level"],
                                            "doctor": dnames.get(occ["doctor_id"]), "since": occ["called_at"]}
                          if occ else None,
                          "doctor": owner["name"] if owner else None,
                          "doctor_status": owner["status"] if owner else None})
    return {
        "server_time": t,
        "version": bus.last_id,
        "queue": [patient_view(p, rnames, dnames) for p in queue],
        "consulting": [patient_view(p, rnames, dnames) for p in consulting],
        "doctors": doctors_out,
        "rooms": rooms_out,
        "alerts": alerts,
        "recent_calls": [{**c, "room": rnames.get(c["room_id"]), "doctor": dnames.get(c["doctor_id"])}
                         for c in calls],
        "stats": kpis(t, queue, docs, rooms_out),
        "durations": scheduler.durations(),
        "settings": db.settings(public=True),
        "role": current_role(),
    }


def public_state(t: Optional[float] = None) -> Dict[str, Any]:
    s = build_state(t)
    st = s["stats"]
    return {
        "server_time": s["server_time"], "version": s["version"],
        "queue": [{"token": p["token"], "level": p["level"], "position": p["position"], "eta": p["eta"],
                   "emergency": p["emergency"], "arrived_at": p["arrived_at"]} for p in s["queue"]],
        "serving": [{"token": p["token"], "level": p["level"], "room": p["room"], "doctor": p["doctor"],
                     "called_at": p["called_at"], "emergency": p["emergency"]} for p in s["consulting"]],
        "recent_calls": [{"token": c["token"], "room": c["room"], "doctor": c["doctor"], "level": c["level"],
                          "called_at": c["called_at"], "status": c["status"]} for c in s["recent_calls"]],
        "alert_active": bool(s["alerts"]),
        "stats": {"waiting": st["queue_length"], "avg_wait_today": st["avg_wait_today"],
                  "avg_wait_now": st["avg_wait_now"], "doctors_on_duty": st["doctors_on_duty"],
                  "waiting_by_level": st["waiting_by_level"], "completed_today": st["completed_today"]},
        "settings": {k: s["settings"][k] for k in ("hospital_name", "display_message", "announce",
                                                   "notify_ahead", "voice_lang")},
    }


@api.get("/state")
@need(*STAFF)
def state():
    return jsonify(build_state())


@api.get("/public/state")
def state_public():
    return jsonify(public_state())


@api.get("/stream")
def stream():
    last = request.headers.get("Last-Event-ID")
    last_id = int(last) if last and last.isdigit() else None
    gen = bus.stream(last_id)
    return Response(stream_with_context(gen), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- auth

@api.post("/login")
def login():
    b = body()
    role = b.get("role")
    if role not in ROLE_NAMES:
        raise ApiError("Choose a role")
    pin = str(b.get("pin") or "")
    ok = not db.get_setting("require_pin") or db.check_pin("admin", pin) or \
        (role != "admin" and db.check_pin("staff", pin))
    if not ok:
        time.sleep(0.4)
        raise ApiError("That PIN didn't match. Try again.", 401)
    session["role"] = role
    session.permanent = True
    return jsonify({"role": role})


@api.post("/logout")
def logout():
    session.pop("role", None)
    return jsonify({"ok": True})


@api.get("/me")
def me():
    return jsonify({"role": current_role(), "require_pin": db.get_setting("require_pin"),
                    "pins_default": db.get_setting("pins_default")})


# ---------------------------------------------------------------- patients

@api.post("/patients")
@need("reception")
def register():
    b = body()
    if not (b.get("symptoms") or "").strip():
        raise ApiError("Describe the symptoms so the patient can be prioritised")
    p = scheduler.register(b, source=b.get("source") if b.get("source") in ("voice", "reception") else "reception")
    return jsonify(patient_ticket(p))


def patient_ticket(p: Dict[str, Any]) -> Dict[str, Any]:
    s = build_state()
    me_ = next((q for q in s["queue"] if q["id"] == p["id"]), None)
    cons = next((q for q in s["consulting"] if q["id"] == p["id"]), None)
    return {"patient": me_ or cons or patient_view(p, room_names(), {}),
            "position": me_["position"] if me_ else 0, "eta": me_["eta"] if me_ else 0,
            "status": (me_ or cons or p)["status"], "room": cons["room"] if cons else None,
            "doctor": cons["doctor"] if cons else None, "queue_length": len(s["queue"])}


@api.patch("/patients/<int:pid>")
@need("reception", "doctor")
def edit_patient(pid: int):
    return jsonify(scheduler.update_patient(pid, body()))


@api.get("/patients/<int:pid>")
@need(*STAFF)
def get_patient(pid: int):
    p = scheduler.get(pid)
    if not p:
        raise ApiError("Patient not found", 404)
    return jsonify(patient_view(p, room_names(), {d["id"]: d["name"] for d in db.rows("SELECT id,name FROM doctors")}))


@api.post("/patients/<int:pid>/emergency")
@need("reception", "doctor")
def override(pid: int):
    return jsonify(scheduler.raise_emergency(current_role() or "reception", patient_id=pid))


@api.post("/patients/<int:pid>/cancel")
@need("reception")
def cancel(pid: int):
    scheduler.cancel(pid, body().get("reason") or "Left without being seen")
    return jsonify({"ok": True})


@api.post("/patients/<int:pid>/ai")
@need(*STAFF)
def reanalyse(pid: int):
    db.update("patients", pid, {"ai_status": "pending"})
    ai_worker.submit(pid)
    bus.publish("queue", {"reason": "ai", "patient_id": pid})
    return jsonify({"ok": True})


@api.post("/patients/<int:pid>/brief")
@need("doctor")
def brief(pid: int):
    p = scheduler.get(pid)
    if not p:
        raise ApiError("Patient not found", 404)
    text = llm.doctor_brief(p)
    db.update("patients", pid, {"ai_brief": text})
    return jsonify({"brief": text})


@api.get("/patients")
@need(*STAFF)
def history():
    day = request.args.get("day") or db.today()
    status = request.args.get("status")
    sql = "SELECT * FROM patients WHERE day=?"
    params: List[Any] = [day]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY arrived_at DESC LIMIT 500"
    rn = room_names()
    dn = {d["id"]: d["name"] for d in db.rows("SELECT id,name FROM doctors")}
    return jsonify([patient_view(db.decode_patient(p), rn, dn) for p in db.rows(sql, params)])


@api.get("/p/<code>")
def patient_status(code: str):
    p = db.row("SELECT * FROM patients WHERE code=?", (code.lower(),))
    if not p:
        raise ApiError("We couldn't find that token. Check the code on your slip.", 404)
    return jsonify(patient_public(p))


@api.get("/token/<token>")
def token_lookup(token: str):
    token = token.strip().zfill(3)
    p = db.row("SELECT * FROM patients WHERE token=? AND day=? ", (token, db.today()))
    if not p:
        raise ApiError("No token %s today. Check the number on your slip." % token, 404)
    return jsonify({"code": p["code"]})


def patient_public(p: Dict[str, Any]) -> Dict[str, Any]:
    s = public_state()
    pos = next((q for q in s["queue"] if q["token"] == p["token"]), None) if p["status"] == "waiting" else None
    room = db.row("SELECT name FROM rooms WHERE id=?", (p["room_id"],)) if p["room_id"] else None
    doc = db.row("SELECT name FROM doctors WHERE id=?", (p["doctor_id"],)) if p["doctor_id"] else None
    return {
        "token": p["token"], "code": p["code"], "level": p["level"], "status": p["status"],
        "emergency": bool(p["emergency"]), "preempted": bool(p["preempted"]),
        "position": pos["position"] if pos else None, "ahead": (pos["position"] - 1) if pos else None,
        "eta": pos["eta"] if pos else None, "arrived_at": p["arrived_at"], "called_at": p["called_at"],
        "completed_at": p["completed_at"], "outcome": p["outcome"],
        "room": room["name"] if room else None, "doctor": doc["name"] if doc else None,
        "server_time": s["server_time"], "serving": s["serving"], "waiting": s["stats"]["waiting"],
        "settings": s["settings"],
    }


# ---------------------------------------------------------------- triage

@api.post("/triage/preview")
def triage_preview():
    b = body()
    res = triage.analyse(b.get("symptoms") or "", b.get("age"), b.get("sex"), b.get("vitals") or {},
                         bool(b.get("pregnant")), scheduler.conditions())
    return jsonify(res)


@api.post("/triage/ai")
@need(*STAFF)
def triage_ai():
    b = body()
    rules = triage.analyse(b.get("symptoms") or "", b.get("age"), b.get("sex"), b.get("vitals") or {},
                           bool(b.get("pregnant")), scheduler.conditions())
    p = {"symptoms": b.get("symptoms"), "age": b.get("age"), "sex": b.get("sex"),
         "vitals": b.get("vitals") or {}, "pregnant": b.get("pregnant"),
         "rules_level": rules["level"], "primary_condition": rules["primary_condition"]}
    t0 = time.time()
    out = llm.triage(p, scheduler.conditions())
    out["latency"] = round(time.time() - t0, 1)
    return jsonify({"rules": rules, "ai": out})


# ---------------------------------------------------------------- doctors

@api.get("/doctors")
def doctors_list():
    rn = room_names()
    return jsonify([{**d, "room": rn.get(d["room_id"])} for d in db.rows("SELECT * FROM doctors ORDER BY id")])


@api.post("/doctors")
@need()
def doctor_add():
    b = body()
    name = (b.get("name") or "").strip()
    if not name:
        raise ApiError("Enter the doctor's name")
    did = db.insert("doctors", {"name": name, "specialty": (b.get("specialty") or "General Medicine").strip(),
                                "room_id": b.get("room_id") or None, "status": "off_duty",
                                "status_since": time.time(), "created_at": time.time()})
    log("ADMIN", "Doctor added: %s" % name)
    bus.publish("doctors", {"doctor_id": did})
    return jsonify(db.row("SELECT * FROM doctors WHERE id=?", (did,)))


@api.patch("/doctors/<int:did>")
@need()
def doctor_edit(did: int):
    b = body()
    fields = {k: b[k] for k in ("name", "specialty", "room_id") if k in b}
    if "room_id" in fields and not fields["room_id"]:
        fields["room_id"] = None
    db.update("doctors", did, fields)
    bus.publish("doctors", {"doctor_id": did})
    return jsonify(db.row("SELECT * FROM doctors WHERE id=?", (did,)))


@api.delete("/doctors/<int:did>")
@need()
def doctor_delete(did: int):
    d = scheduler.doctor(did)
    if d["current_patient_id"]:
        raise ApiError("This doctor is with a patient. Finish the consultation first.", 409)
    db.execute("DELETE FROM doctors WHERE id=?", (did,))
    log("ADMIN", "Doctor removed: %s" % d["name"])
    bus.publish("doctors", {"doctor_id": did})
    return jsonify({"ok": True})


@api.post("/doctors/<int:did>/status")
@need("doctor")
def doctor_status(did: int):
    return jsonify(scheduler.set_doctor_status(did, body().get("status")))


@api.post("/doctors/<int:did>/finish")
@need("doctor")
def doctor_finish(did: int):
    b = body()
    scheduler.finish(did, b.get("outcome") or "", b.get("notes") or "", b.get("next_status") or "available")
    return jsonify({"ok": True})


@api.post("/doctors/<int:did>/no_show")
@need("doctor")
def doctor_no_show(did: int):
    scheduler.no_show(did)
    return jsonify({"ok": True})


@api.post("/doctors/<int:did>/recall")
@need("doctor")
def doctor_recall(did: int):
    scheduler.recall(did)
    return jsonify({"ok": True})


@api.post("/doctors/<int:did>/call_next")
@need("doctor")
def doctor_call_next(did: int):
    return jsonify(scheduler.call_next(did))


# ---------------------------------------------------------------- rooms

@api.post("/rooms")
@need()
def room_add():
    b = body()
    name = (b.get("name") or "").strip()
    if not name:
        raise ApiError("Enter a room name")
    rid = db.insert("rooms", {"name": name, "kind": b.get("kind") or "consultation", "status": "open",
                              "created_at": time.time()})
    bus.publish("rooms", {"room_id": rid})
    return jsonify(db.row("SELECT * FROM rooms WHERE id=?", (rid,)))


@api.patch("/rooms/<int:rid>")
@need()
def room_edit(rid: int):
    b = body()
    fields = {k: b[k] for k in ("name", "kind", "status") if k in b}
    if fields.get("status") and fields["status"] not in ("open", "cleaning", "closed"):
        raise ApiError("Unknown room status")
    db.update("rooms", rid, fields)
    bus.publish("rooms", {"room_id": rid})
    return jsonify(db.row("SELECT * FROM rooms WHERE id=?", (rid,)))


@api.delete("/rooms/<int:rid>")
@need()
def room_delete(rid: int):
    if db.scalar("SELECT COUNT(*) FROM patients WHERE room_id=? AND status='in_consultation'", (rid,)):
        raise ApiError("A consultation is running in this room", 409)
    db.execute("UPDATE doctors SET room_id=NULL WHERE room_id=?", (rid,))
    db.execute("DELETE FROM rooms WHERE id=?", (rid,))
    bus.publish("rooms", {"room_id": rid})
    return jsonify({"ok": True})


# ---------------------------------------------------------------- triage protocol

@api.get("/conditions")
def conditions():
    return jsonify(db.rows("SELECT * FROM conditions ORDER BY rank, id"))


def _condition_fields(b: Dict[str, Any]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    if "name" in b:
        fields["name"] = (b["name"] or "").strip()
        if not fields["name"]:
            raise ApiError("Enter a condition name")
    if "rank" in b:
        try:
            fields["rank"] = max(0, min(15, int(b["rank"])))
        except (TypeError, ValueError):
            raise ApiError("Rank must be a number from 0 to 15")
    if "level" in b:
        if b["level"] not in triage.LEVELS:
            raise ApiError("Unknown level")
        fields["level"] = b["level"]
    for k in ("reason", "keywords", "department"):
        if k in b:
            fields[k] = (b[k] or "").strip()
    for k in ("red_flag", "active"):
        if k in b:
            fields[k] = 1 if b[k] else 0
    return fields


@api.post("/conditions")
@need()
def condition_add():
    f = _condition_fields(body())
    f.setdefault("rank", 15)
    f.setdefault("level", "low")
    if not f.get("name"):
        raise ApiError("Enter a condition name")
    cid = db.insert("conditions", f)
    scheduler.invalidate_conditions()
    log("ADMIN", "Triage protocol: added %s" % f["name"])
    return jsonify(db.row("SELECT * FROM conditions WHERE id=?", (cid,)))


@api.patch("/conditions/<int:cid>")
@need()
def condition_edit(cid: int):
    db.update("conditions", cid, _condition_fields(body()))
    scheduler.invalidate_conditions()
    return jsonify(db.row("SELECT * FROM conditions WHERE id=?", (cid,)))


@api.post("/conditions/reset")
@need()
def conditions_reset():
    db.seed_conditions()
    scheduler.invalidate_conditions()
    log("ADMIN", "Triage protocol restored to the defaults")
    return jsonify({"ok": True})


@api.delete("/conditions/<int:cid>")
@need()
def condition_delete(cid: int):
    db.execute("DELETE FROM conditions WHERE id=?", (cid,))
    scheduler.invalidate_conditions()
    return jsonify({"ok": True})


# ---------------------------------------------------------------- emergency

@api.post("/emergency")
@need("reception", "doctor")
def emergency():
    b = body()
    p = scheduler.raise_emergency(b.get("source") or current_role() or "reception",
                                  name=(b.get("name") or "").strip() or None,
                                  complaint=(b.get("complaint") or "").strip() or None)
    return jsonify(p)


@api.post("/alerts/<int:aid>/ack")
@need(*STAFF)
def ack(aid: int):
    scheduler.acknowledge(aid, ROLE_NAMES.get(current_role() or "", "staff"))
    return jsonify({"ok": True})


@api.post("/alerts/ack_all")
@need(*STAFF)
def ack_all():
    scheduler.acknowledge(None, ROLE_NAMES.get(current_role() or "", "staff"))
    return jsonify({"ok": True})


@api.get("/alerts")
@need(*STAFF)
def alerts():
    return jsonify(db.rows("SELECT * FROM alerts ORDER BY ts DESC LIMIT 50"))


# ---------------------------------------------------------------- kiosk (public)

_kiosk_last: Dict[str, float] = {}


def _kiosk_guard() -> None:
    ip = request.remote_addr or "?"
    if time.time() - _kiosk_last.get(ip, 0) < 8:
        raise ApiError("Please wait a moment before registering again", 429)
    _kiosk_last[ip] = time.time()


@api.post("/kiosk/register")
def kiosk_register():
    b = body()
    if not (b.get("symptoms") or "").strip():
        raise ApiError("Tell us what's wrong so we can prioritise you")
    _kiosk_guard()
    data = {k: b.get(k) for k in ("name", "age", "sex", "phone", "symptoms", "pregnant")}
    p = scheduler.register(data, source="kiosk")
    t = patient_ticket(p)
    return jsonify({"token": p["token"], "code": p["code"], "level": p["level"], "position": t["position"],
                    "eta": t["eta"], "status": t["status"], "room": t["room"]})


@api.post("/kiosk/emergency")
def kiosk_emergency():
    _kiosk_guard()
    p = scheduler.raise_emergency("kiosk", complaint=(body().get("complaint") or "").strip() or None)
    return jsonify({"token": p["token"], "code": p["code"], "status": p["status"]})


# ---------------------------------------------------------------- voice

@api.get("/voice/status")
def voice_status():
    return jsonify(voice.status())


def _parse(text: str) -> Dict[str, Any]:
    rules = parse_checkin(text)
    if llm.enabled() and llm.status().get("model_ready"):
        try:
            ai = llm.extract_registration(text)
            merged = {k: (ai.get(k) if ai.get(k) not in (None, "", 0) else rules.get(k)) for k in rules}
            merged["pregnant"] = bool(ai.get("pregnant") or rules.get("pregnant"))
            return {"fields": merged, "parser": "ai"}
        except LLMError:
            pass
    return {"fields": rules, "parser": "rules"}


@api.post("/voice/listen")
def voice_listen():
    sid = str(body().get("session") or "")
    text = voice.listen(max_seconds=float(body().get("seconds") or 12),
                        on_partial=lambda s: bus.publish("voice", {"session": sid, "text": s}))
    if not text:
        raise ApiError("We didn't catch that. Speak closer to the microphone and try again.", 422)
    return jsonify({"text": text, **_parse(text)})


@api.post("/voice/transcribe")
def voice_transcribe():
    data = request.get_data()
    if not data:
        raise ApiError("No audio received")
    text = voice.transcribe_wav(data)
    if not text:
        raise ApiError("We didn't catch that. Try again a little closer to the microphone.", 422)
    return jsonify({"text": text, **_parse(text)})


@api.post("/voice/parse")
def voice_parse():
    text = (body().get("text") or "").strip()
    if not text:
        raise ApiError("Nothing to read")
    return jsonify({"text": text, **_parse(text)})


# ---------------------------------------------------------------- AI

@api.get("/llm/status")
def llm_status():
    return jsonify({**llm.status(force=request.args.get("force") == "1"), "worker": ai_worker.stats()})


@api.post("/llm/discover")
@need()
def llm_discover():
    return jsonify({"servers": llm.discover()})


@api.post("/llm/test")
@need()
def llm_test():
    t0 = time.time()
    return jsonify({"reply": llm.ping(), "latency": round(time.time() - t0, 2)})


@api.post("/llm/report")
@need()
def llm_report():
    return jsonify({"report": llm.shift_report(analytics_data())})


@api.post("/llm/ask")
@need()
def llm_ask():
    q = (body().get("question") or "").strip()
    if not q:
        raise ApiError("Type a question")
    return jsonify({"answer": llm.ask(q, analytics_data())})


# ---------------------------------------------------------------- analytics

def analytics_data() -> Dict[str, Any]:
    day = db.today()
    start = db.day_start()
    pts = [db.decode_patient(p) for p in db.rows("SELECT * FROM patients WHERE day=?", (day,))]
    hourly = [0] * 24
    for p in pts:
        h = time.localtime(p["arrived_at"]).tm_hour if p["arrived_at"] >= start else 0
        hourly[h] += 1
    levels = {lvl: 0 for lvl in triage.LEVELS}
    waits: Dict[str, List[float]] = {lvl: [] for lvl in triage.LEVELS}
    sources: Dict[str, int] = {}
    outcomes: Dict[str, int] = {}
    conditions: Dict[str, int] = {}
    for p in pts:
        levels[p["level"]] = levels.get(p["level"], 0) + 1
        if p["wait_seconds"] is not None:
            waits[p["level"]].append(p["wait_seconds"] / 60)
        src = (p["source"] or "reception").replace("emergency-", "emergency ")
        sources[src] = sources.get(src, 0) + 1
        if p["outcome"]:
            outcomes[p["outcome"]] = outcomes.get(p["outcome"], 0) + 1
        if p["primary_condition"]:
            conditions[p["primary_condition"]] = conditions.get(p["primary_condition"], 0) + 1
    docs = []
    for d in db.rows("SELECT * FROM doctors ORDER BY id"):
        r = db.row("SELECT COUNT(*) n, AVG(consult_seconds) a FROM patients WHERE doctor_id=? AND day=? "
                   "AND status='completed'", (d["id"], day))
        docs.append({"name": d["name"], "status": d["status"], "seen": r["n"],
                     "avg_consult_min": round(r["a"] / 60, 1) if r["a"] else None})
    s = build_state()
    return {
        "date": day, "hospital": db.get_setting("hospital_name"),
        "registered": len(pts), "waiting_now": s["stats"]["queue_length"],
        "in_consultation": s["stats"]["in_consultation"], "completed": s["stats"]["completed_today"],
        "left_without_being_seen": s["stats"]["left_today"], "emergency_alerts": s["stats"]["emergencies_today"],
        "avg_wait_min": round(s["stats"]["avg_wait_today"] / 60, 1) if s["stats"]["avg_wait_today"] else None,
        "longest_current_wait_min": round(s["stats"]["longest_wait_now"] / 60, 1)
        if s["stats"]["longest_wait_now"] else None,
        "arrivals_by_hour": hourly, "patients_by_level": levels,
        "avg_wait_by_level_min": {k: round(sum(v) / len(v), 1) if v else None for k, v in waits.items()},
        "max_wait_by_level_min": {k: round(max(v), 1) if v else None for k, v in waits.items()},
        "sources": sources, "outcomes": outcomes,
        "top_conditions": sorted(conditions.items(), key=lambda kv: -kv[1])[:8],
        "doctors": docs, "preemptions": scheduler.preemptions, "dispatches": scheduler.dispatches,
    }


@api.get("/analytics")
@need(*STAFF)
def analytics():
    return jsonify(analytics_data())


@api.get("/lab/today")
def lab_today():
    """Today's arrivals (anonymous) for replay in the Scheduler Lab."""
    start = db.day_start()
    out = []
    for p in db.rows("SELECT token, level, rank, base_score, arrived_at, consult_seconds, status, emergency "
                     "FROM patients WHERE day=? ORDER BY arrived_at", (db.today(),)):
        out.append({"token": p["token"], "level": p["level"], "rank": p["rank"], "base": p["base_score"],
                    "arrival": round((p["arrived_at"] - start) / 60, 2),
                    "duration": round(p["consult_seconds"] / 60, 1) if p["consult_seconds"] else None,
                    "emergency": bool(p["emergency"])})
    return jsonify({"patients": out, "aging_rate": db.get_setting("aging_rate"),
                    "aging_cap": db.get_setting("aging_cap"), "durations": scheduler.durations()})


# ---------------------------------------------------------------- system

@api.get("/events")
@need(*STAFF)
def events():
    limit = min(500, int(request.args.get("limit") or 100))
    return jsonify(recent(limit, request.args.get("kind") or None))


@api.get("/system")
@need(*STAFF)
def system():
    return jsonify({
        "system": sysmon.snapshot(), "threads": sysmon.threads(),
        "lock": scheduler.lock.stats(),
        "scheduler": {"dispatches": scheduler.dispatches, "preemptions": scheduler.preemptions,
                      "interrupts": scheduler.interrupts, "ticks": scheduler.ticks,
                      "started_at": scheduler.started_at, "tick_seconds": db.get_setting("tick_seconds"),
                      "aging_rate": db.get_setting("aging_rate"), "aging_cap": db.get_setting("aging_cap"),
                      "preemption": db.get_setting("preemption")},
        "bus": {"clients": bus.clients, "published": bus.published, "last_id": bus.last_id},
        "ai": {**ai_worker.stats(), "calls": llm.calls, "failures": llm.failures,
               "last_latency": llm.last_latency},
        "voice": voice.status(),
        "hardware": hardware.state() if hardware else None,
    })


@api.get("/hardware")
def hardware_state():
    return jsonify(hardware.state() if hardware else {"mode": "none"})


@api.post("/hardware/test")
@need()
def hardware_test():
    what = body().get("what")
    if what not in ("led", "buzzer", "button"):
        raise ApiError("Choose led, buzzer or button")
    hardware.test(what)
    if what != "button":
        log("HARDWARE", "Self-test: %s" % what)
    return jsonify(hardware.state())


@api.get("/network")
def network():
    ips = local_ipv4()
    port = request.host.split(":")[1] if ":" in request.host else ("443" if request.scheme == "https" else "80")
    host = socket.gethostname()
    suffix = "" if port in ("80", "443") else ":" + port
    return jsonify({"hostname": host, "mdns": "%s.local%s" % (host.lower(), suffix), "ips": ips,
                    "urls": ["%s://%s%s" % (request.scheme, ip, suffix) for ip in ips],
                    "current": request.host_url.rstrip("/")})


@api.get("/qr")
def qr():
    data = request.args.get("data") or ""
    if not data or len(data) > 300:
        raise ApiError("Nothing to encode")
    try:
        import segno  # type: ignore
    except ImportError:
        raise ApiError("QR support is not installed (pip install segno)", 501)
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=1, border=0, dark="#000", light=None,
                                     xmldecl=False, svgns=True, nl=False)
    return Response(buf.getvalue(), mimetype="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------- settings & data

INT_SETTINGS = ("tick_seconds", "notify_ahead", "dur_critical", "dur_high", "dur_medium", "dur_low",
                "llm_timeout", "alarm_buzzer_seconds")
FLOAT_SETTINGS = ("aging_rate", "aging_cap")
BOOL_SETTINGS = ("auto_dispatch", "require_pin", "llm_enabled", "ai_triage", "announce", "buzz_on_call")
STR_SETTINGS = ("hospital_name", "llm_url", "llm_model", "voice_lang", "display_message")


@api.get("/settings")
@need()
def settings_get():
    return jsonify(db.settings(public=True))


@api.patch("/settings")
@need()
def settings_patch():
    b = body()
    changed = []
    for k, v in b.items():
        if k in INT_SETTINGS:
            try:
                v = max(0, int(v))
            except (TypeError, ValueError):
                raise ApiError("%s must be a whole number" % k.replace("_", " "))
        elif k in FLOAT_SETTINGS:
            try:
                v = max(0.0, float(v))
            except (TypeError, ValueError):
                raise ApiError("%s must be a number" % k.replace("_", " "))
        elif k in BOOL_SETTINGS:
            v = bool(v)
        elif k in STR_SETTINGS:
            v = str(v or "").strip()
            if k == "llm_url" and v and not v.startswith("http"):
                v = "http://" + v
        elif k == "preemption":
            if v not in ("off", "emergency", "critical"):
                raise ApiError("Unknown pre-emption mode")
        else:
            continue
        db.set_setting(k, v)
        changed.append(k)
    if changed:
        if any(k.startswith("llm") for k in changed):
            llm.status(force=True)
        scheduler._durations_cache = None
        log("ADMIN", "Settings changed: %s" % ", ".join(changed))
        bus.publish("settings", {"changed": changed})
        if "auto_dispatch" in changed or "aging_rate" in changed or "aging_cap" in changed:
            threading.Thread(target=scheduler.tick, daemon=True).start()
    return jsonify(db.settings(public=True))


@api.post("/settings/pin")
@need()
def settings_pin():
    b = body()
    role = b.get("role")
    pin = str(b.get("pin") or "")
    if role not in ("admin", "staff"):
        raise ApiError("Choose admin or staff")
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise ApiError("Use 4 to 8 digits")
    db.set_pin(role, pin)
    log("ADMIN", "%s PIN changed" % role.capitalize())
    return jsonify({"ok": True})


@api.get("/export.csv")
@need(*STAFF)
def export_csv():
    day = request.args.get("day")
    sql = "SELECT * FROM patients" + (" WHERE day=?" if day else "") + " ORDER BY arrived_at"
    pts = db.rows(sql, (day,) if day else ())
    rn = room_names()
    dn = {d["id"]: d["name"] for d in db.rows("SELECT id,name FROM doctors")}
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["day", "token", "name", "age", "sex", "phone", "symptoms", "level", "level_source",
            "primary_condition", "department", "source", "emergency", "status", "arrived", "called",
            "completed", "wait_min", "consult_min", "doctor", "room", "outcome", "doctor_notes"]
    w.writerow(cols)

    def ts(v):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v)) if v else ""
    for p in pts:
        w.writerow([p["day"], p["token"], p["name"], p["age"], p["sex"], p["phone"], p["symptoms"], p["level"],
                    p["level_source"], p["primary_condition"], p["department"], p["source"], p["emergency"],
                    p["status"], ts(p["arrived_at"]), ts(p["called_at"]), ts(p["completed_at"]),
                    round(p["wait_seconds"] / 60, 1) if p["wait_seconds"] is not None else "",
                    round(p["consult_seconds"] / 60, 1) if p["consult_seconds"] else "",
                    dn.get(p["doctor_id"], ""), rn.get(p["room_id"], ""), p["outcome"], p["doctor_notes"]])
    name = "medos-patients-%s.csv" % (day or "all")
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=%s" % name})


@api.post("/demo/seed")
@need()
def demo_seed():
    from . import demo
    demo.seed()
    return jsonify({"ok": True})


@api.post("/demo/clear")
@need()
def demo_clear():
    from . import demo
    demo.clear()
    return jsonify({"ok": True})
