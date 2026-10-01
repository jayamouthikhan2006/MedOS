"""Ambulance desk: hospital selection and dispatch, SJF-style (Shortest Job First).

Hospital selection
    For each hospital that can treat the case, estimate the "job length" for the patient:
        time to treatment = travel time from the pickup area + expected wait at that hospital
    and pick the hospital with the shortest one. For this hospital the wait comes live from the
    MedOS queue; for the others it comes from the waiting time they report.

Dispatch
    Ambulances are the CPUs, requests are the jobs. Emergencies always go first. Within the
    same class the shortest trip goes first (SJF), which keeps the average waiting time lowest.
    Plain SJF can starve a long trip, so every minute a request waits takes 0.5 min off its
    sort length (aging), and a long trip is eventually dispatched.
"""
from __future__ import annotations

import heapq
import json
import math
import time
from typing import Any, Dict, List, Optional

from . import db, triage
from .events import bus, log
from .scheduler import InstrumentedLock, SchedulerError, scheduler

lock = InstrumentedLock("ambulance-queue")

KINDS = {"emergency": "Emergency", "non_emergency": "Non-emergency"}
AGING_PER_MIN = 0.5      # minutes of trip length forgiven per minute waited (non-emergency)
HANDOVER_MIN = 5         # loading at the pickup and handover at the hospital
ROAD_FACTOR = 1.3        # roads are longer than straight lines

# Pickup areas on a simple km grid with this hospital at (0, 0).
AREAS = {
    "T. Nagar": (1.0, 1.5), "Anna Nagar": (-3.0, 5.0), "Guindy": (0.5, -4.0), "Adyar": (5.0, -2.5),
    "Velachery": (4.0, -6.5), "Porur": (-8.0, 0.0), "Tambaram": (-2.0, -11.0), "Perambur": (-1.0, 9.0),
}

DEFAULT_HOSPITALS = [
    # name, x, y, specialties, beds free, reported wait (min)
    ("City General Hospital", 0.0, 0.0, "Emergency, General Medicine, Paediatrics, Orthopaedics, General Surgery", 8, 25, 1),
    ("Lakeview Heart & Neuro Institute", 6.0, 3.0, "Emergency, Cardiology, Neurology, General Medicine", 4, 35, 0),
    ("Riverside Trauma Centre", -4.0, 7.0, "Emergency, Orthopaedics, Burns, General Surgery", 6, 15, 0),
    ("Green Park Clinic", 2.0, -3.0, "General Medicine, ENT, Dermatology, Gastroenterology", 10, 10, 0),
    ("Sunrise Women & Children Hospital", -5.0, -4.0, "Emergency, Paediatrics, Obstetrics, General Medicine", 5, 30, 0),
]

# A condition that needs a particular specialist, when one is available.
SPECIALIST_FOR = [
    ("chest pain", "Cardiology"), ("heart", "Cardiology"), ("stroke", "Neurology"), ("seizure", "Neurology"),
    ("trauma", "Orthopaedics"), ("fracture", "Orthopaedics"), ("burn", "Burns"), ("pregnan", "Obstetrics"),
]

# Fraction of a hospital's reported wait that a case of this level actually waits:
# critical patients are taken straight in everywhere, low-priority ones wait longest.
LEVEL_WAIT_FACTOR = {"critical": 0.0, "high": 0.3, "medium": 1.0, "low": 1.5}


def seed_defaults() -> None:
    if not db.scalar("SELECT COUNT(*) FROM hospitals"):
        for name, x, y, spec, beds, wait, me in DEFAULT_HOSPITALS:
            db.insert("hospitals", {"name": name, "x_km": x, "y_km": y, "specialties": spec,
                                    "beds_free": beds, "er_wait_min": wait, "is_self": me, "active": 1})
    if not db.scalar("SELECT COUNT(*) FROM ambulances"):
        for n in (1, 2):
            db.insert("ambulances", {"name": "Ambulance %d" % n, "status": "available", "status_since": time.time()})


def hospitals() -> List[Dict[str, Any]]:
    return db.rows("SELECT * FROM hospitals ORDER BY is_self DESC, name")


def _specs(h: Dict[str, Any]) -> List[str]:
    return [s.strip().lower() for s in (h["specialties"] or "").split(",") if s.strip()]


def drive_min(ax: float, ay: float, bx: float, by: float) -> float:
    km = math.hypot(ax - bx, ay - by) * ROAD_FACTOR
    speed = float(db.get_setting("ambulance_speed_kmh") or 30)
    return round(km / speed * 60 + 1, 1)


def self_wait_min(level: str, rank: int) -> Optional[float]:
    """Live wait at this hospital for a new case of this level, from the MedOS ready queue."""
    if level == "critical" and db.get_setting("preemption") != "off":
        return 0.0
    base = triage.base_score(level, rank)
    ahead = [p for p in scheduler.ready_queue() if p["score"]["total"] >= base]
    eta = scheduler.estimates(ahead + [{"id": -1, "level": level}]).get(-1)
    return None if eta is None else round(eta / 60.0, 1)


def needs_for(t: Dict[str, Any], kind: str) -> Dict[str, Any]:
    text = ((t.get("primary_condition") or "") + " " + " ".join(t.get("red_flags") or [])).lower()
    specialist = next((sp for key, sp in SPECIALIST_FOR if key in text), None)
    if not specialist and t.get("department") not in (None, "Emergency", "General Medicine"):
        specialist = t["department"]
    er = t["level"] in ("critical", "high") or kind == "emergency"
    return {"er": er, "specialist": specialist}


def recommend(condition: str, kind: str = "emergency", area: str = "T. Nagar",
              age: Any = None) -> Dict[str, Any]:
    """Rank every hospital by time to treatment and pick the shortest (SJF-style)."""
    if area not in AREAS:
        raise SchedulerError("Choose a pickup area from the list")
    t = triage.analyse(condition or "", age, None, {}, False, scheduler.conditions())
    if kind == "emergency" and t["level"] in ("low", "medium"):
        t["level"] = "high"     # an emergency call is at least High until a doctor says otherwise
    need = needs_for(t, kind)
    ax, ay = AREAS[area]
    hs = [h for h in hospitals() if h["active"]]
    me = next((h for h in hs if h["is_self"]), None)
    pickup_min = drive_min(me["x_km"], me["y_km"], ax, ay) if me else 0.0

    rows = []
    for h in hs:
        specs = _specs(h)
        reason = None
        if need["er"] and "emergency" not in specs:
            reason = "No emergency department"
        elif h["beds_free"] <= 0:
            reason = "No free beds"
        travel = drive_min(ax, ay, h["x_km"], h["y_km"])
        if h["is_self"]:
            wait = self_wait_min(t["level"], t["rank"])
            wait_src = "live from MedOS"
            if wait is None:
                wait, wait_src = float(h["er_wait_min"]) * LEVEL_WAIT_FACTOR[t["level"]], "no doctor on duty, using reported wait"
        else:
            wait = round(float(h["er_wait_min"]) * LEVEL_WAIT_FACTOR[t["level"]], 1)
            wait_src = "reported"
        rows.append({"id": h["id"], "name": h["name"], "is_self": bool(h["is_self"]), "travel_min": travel,
                     "wait_min": round(wait, 1), "wait_source": wait_src,
                     "total_min": round(travel + wait, 1), "has_specialist": bool(need["specialist"] and need["specialist"].lower() in specs),
                     "eligible": reason is None, "reason": reason, "beds_free": h["beds_free"]})

    eligible = [r for r in rows if r["eligible"]]
    note = None
    if need["specialist"]:
        with_spec = [r for r in eligible if r["has_specialist"]]
        if with_spec:
            for r in eligible:
                if not r["has_specialist"]:
                    r["eligible"], r["reason"] = False, "No %s" % need["specialist"]
            eligible = with_spec
        else:
            note = "No hospital lists %s, so any suitable hospital is considered." % need["specialist"]
    # SJF: the shortest time to treatment wins; ties go to the shorter drive.
    rows.sort(key=lambda r: (not r["eligible"], r["total_min"], r["travel_min"]))
    best = rows[0] if rows and rows[0]["eligible"] else None
    return {"level": t["level"], "primary_condition": t["primary_condition"], "kind": kind, "area": area,
            "needs": ("Emergency department" if need["er"] else "Outpatient care") +
                     (" + %s" % need["specialist"] if need["specialist"] else ""),
            "pickup_min": pickup_min, "ranking": rows, "best": best, "note": note}


# ------------------------------------------------------------------ dispatch
def _job_min(r: Dict[str, Any]) -> float:
    return float(r["pickup_min"]) + float(r["travel_min"] or 0) + HANDOVER_MIN


def _sort_key(r: Dict[str, Any], t: float):
    waited = (t - r["created_at"]) / 60.0
    if r["kind"] == "emergency":
        return (0, triage.LEVEL_ORDER.get(r["level"], 9), _job_min(r), r["created_at"])
    return (1, 0, _job_min(r) - AGING_PER_MIN * waited, r["created_at"])


def ordered_waiting(t: Optional[float] = None) -> List[Dict[str, Any]]:
    t = t or time.time()
    reqs = db.rows("SELECT * FROM ambulance_requests WHERE status='waiting'")
    reqs.sort(key=lambda r: _sort_key(r, t))
    free_at = []
    for a in db.rows("SELECT a.*, r.pickup_min, r.travel_min, r.dispatched_at FROM ambulances a "
                     "LEFT JOIN ambulance_requests r ON r.id = a.request_id"):
        if a["status"] == "on_trip" and a["dispatched_at"]:
            left = (float(a["pickup_min"]) + float(a["travel_min"] or 0) + HANDOVER_MIN) - (t - a["dispatched_at"]) / 60
            free_at.append(max(1.0, left))
        elif a["status"] == "available":
            free_at.append(0.0)
    heapq.heapify(free_at)
    for i, r in enumerate(reqs):
        r["position"] = i + 1
        r["job_min"] = round(_job_min(r), 1)
        r["waited_min"] = round((t - r["created_at"]) / 60.0, 1)
        if free_at:
            f = heapq.heappop(free_at)
            r["dispatch_in_min"] = round(f, 1)
            heapq.heappush(free_at, f + _job_min(r))
        else:
            r["dispatch_in_min"] = None
    return reqs


def dispatch() -> List[Dict[str, Any]]:
    out = []
    with lock:
        free = db.rows("SELECT * FROM ambulances WHERE status='available' ORDER BY status_since, id")
        queue = ordered_waiting()
        for a in free:
            if not queue:
                break
            r = queue.pop(0)
            t = time.time()
            db.update("ambulance_requests", r["id"], {"status": "dispatched", "ambulance_id": a["id"], "dispatched_at": t})
            db.update("ambulances", a["id"], {"status": "on_trip", "request_id": r["id"], "status_since": t})
            out.append({"request_id": r["id"], "ambulance": a["name"]})
            log("AMBULANCE", "%s sent to %s for request #%d (%s, trip %d min) → %s" % (
                a["name"], r["pickup"], r["id"], KINDS[r["kind"]].lower(), _job_min(r), r["hospital_name"]))
    if out:
        bus.publish("ambulance", {"dispatched": out})
    return out


def create_request(data: Dict[str, Any]) -> Dict[str, Any]:
    kind = data.get("kind") if data.get("kind") in KINDS else "emergency"
    condition = (data.get("condition") or "").strip()
    if not condition:
        raise SchedulerError("Describe what happened so the right hospital can be chosen")
    area = data.get("area") or ""
    rec = recommend(condition, kind, area, data.get("age"))
    choice = rec["best"]
    if data.get("hospital_id"):
        choice = next((r for r in rec["ranking"] if r["id"] == int(data["hospital_id"])), choice)
    if not choice:
        raise SchedulerError("No hospital can take this case right now. Check beds and specialties in the hospital list.")
    age = data.get("age")
    try:
        age = int(age) if age not in (None, "") else None
    except (TypeError, ValueError):
        age = None
    with lock:
        rid = db.insert("ambulance_requests", {
            "day": db.today(), "kind": kind, "patient_name": (data.get("name") or "").strip() or None, "age": age,
            "condition": condition, "level": rec["level"], "needs": rec["needs"], "pickup": area,
            "pickup_min": rec["pickup_min"], "hospital_id": choice["id"], "hospital_name": choice["name"],
            "travel_min": choice["travel_min"], "wait_min": choice["wait_min"], "total_min": choice["total_min"],
            "ranking": json.dumps(rec["ranking"]), "status": "waiting", "created_at": time.time()})
    log("AMBULANCE", "Request #%d (%s, %s) from %s: %s chosen, %d min to treatment (shortest of %d)" % (
        rid, KINDS[kind].lower(), triage.LEVEL_LABEL[rec["level"]], area, choice["name"], choice["total_min"],
        sum(1 for r in rec["ranking"] if r["eligible"])))
    bus.publish("ambulance", {"request_id": rid})
    dispatch()
    return get(rid)


def get(rid: int) -> Optional[Dict[str, Any]]:
    return db.row("SELECT * FROM ambulance_requests WHERE id=?", (rid,))


def arrived(rid: int) -> Dict[str, Any]:
    """The ambulance delivered the patient. If it came here, the patient joins the MedOS queue."""
    with lock:
        r = get(rid)
        if not r or r["status"] != "dispatched":
            raise SchedulerError("Only a dispatched ambulance can arrive")
        t = time.time()
        db.update("ambulance_requests", rid, {"status": "arrived", "arrived_at": t})
        db.update("ambulances", r["ambulance_id"], {"status": "available", "request_id": None, "status_since": t})
        h = db.row("SELECT * FROM hospitals WHERE id=?", (r["hospital_id"],))
    log("AMBULANCE", "Request #%d arrived at %s" % (rid, r["hospital_name"]))
    if h and h["is_self"]:
        p = scheduler.register({"name": r["patient_name"], "age": r["age"], "symptoms": r["condition"],
                                "level_override": r["level"] if r["kind"] == "emergency" else None},
                               source="ambulance")
        db.update("ambulance_requests", rid, {"patient_id": p["id"]})
    bus.publish("ambulance", {"request_id": rid})
    dispatch()
    return get(rid)


def cancel(rid: int) -> None:
    with lock:
        r = get(rid)
        if not r or r["status"] not in ("waiting", "dispatched"):
            raise SchedulerError("This request can't be cancelled")
        db.update("ambulance_requests", rid, {"status": "cancelled", "arrived_at": time.time()})
        if r["ambulance_id"] and r["status"] == "dispatched":
            db.update("ambulances", r["ambulance_id"], {"status": "available", "request_id": None, "status_since": time.time()})
    log("AMBULANCE", "Request #%d cancelled" % rid)
    bus.publish("ambulance", {"request_id": rid})
    dispatch()


def state() -> Dict[str, Any]:
    t = time.time()
    on_trip = db.rows("SELECT r.*, a.name ambulance FROM ambulance_requests r JOIN ambulances a ON a.id=r.ambulance_id "
                      "WHERE r.status='dispatched' ORDER BY r.dispatched_at")
    for r in on_trip:
        r["elapsed_min"] = round((t - r["dispatched_at"]) / 60.0, 1)
        r["job_min"] = round(_job_min(r), 1)
    done = db.rows("SELECT * FROM ambulance_requests WHERE day=? AND status IN ('arrived','cancelled') "
                   "ORDER BY COALESCE(arrived_at, created_at) DESC LIMIT 20", (db.today(),))
    return {"server_time": t, "waiting": ordered_waiting(t), "on_trip": on_trip, "done": done,
            "ambulances": db.rows("SELECT * FROM ambulances ORDER BY id"), "hospitals": hospitals(),
            "areas": list(AREAS.keys()), "kinds": KINDS}
