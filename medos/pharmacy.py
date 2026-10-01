"""Pharmacy orders: First Come, First Served (FCFS).

Every prescription joins one FIFO queue in arrival order. Medicines are prepared strictly
in that order: the pharmacist can only start the order at the head of the queue, so nobody
is skipped. It is the simplest, fairest scheduler, and fine here because preparing
medicines takes about the same time for everyone and no order is more urgent than another
(urgent drugs are given inside the consultation room, not at the pharmacy counter).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import db
from .events import bus, log
from .scheduler import InstrumentedLock, SchedulerError

lock = InstrumentedLock("pharmacy-queue")
ACTIVE = ("waiting", "preparing", "ready")


def create_order(items: str, patient_id: Optional[int] = None, token: Optional[str] = None,
                 patient_name: Optional[str] = None, prescribed_by: Optional[str] = None,
                 source: str = "doctor", created_at: Optional[float] = None) -> Dict[str, Any]:
    items = (items or "").strip()
    if not items:
        raise SchedulerError("List the medicines to dispense")
    with lock:
        oid = db.insert("pharmacy_orders", {
            "day": db.today(), "patient_id": patient_id, "token": token, "patient_name": patient_name,
            "items": items, "prescribed_by": prescribed_by, "source": source, "status": "waiting",
            "created_at": created_at or time.time()})
        pos = len(queue())
    log("PHARMACY", "Order #%d%s joined the pharmacy queue at position %d (FCFS)" % (
        oid, " for token %s" % token if token else "", pos), patient_id)
    bus.publish("pharmacy", {"order_id": oid})
    return get(oid)


def get(oid: int) -> Optional[Dict[str, Any]]:
    return db.row("SELECT * FROM pharmacy_orders WHERE id=?", (oid,))


def queue() -> List[Dict[str, Any]]:
    """Orders not yet prepared, oldest first: the FCFS ready queue."""
    return db.rows("SELECT * FROM pharmacy_orders WHERE status='waiting' ORDER BY created_at, id")


def prep_minutes() -> float:
    """Average preparation time: learned from today's orders, the setting otherwise."""
    r = db.row("SELECT COUNT(*) n, AVG(ready_at - started_at) a FROM pharmacy_orders "
               "WHERE day=? AND ready_at IS NOT NULL AND started_at IS NOT NULL", (db.today(),))
    default = float(db.get_setting("pharmacy_prep_min") or 4)
    if r and r["n"] and r["n"] >= 3 and r["a"]:
        return round((r["a"] / 60.0 * r["n"] + default * 3) / (r["n"] + 3), 1)
    return default


def state() -> Dict[str, Any]:
    t = time.time()
    prep = prep_minutes()
    waiting = queue()
    preparing = db.rows("SELECT * FROM pharmacy_orders WHERE status='preparing' ORDER BY started_at")
    # The counter finishes the order in hand first, then works down the queue one by one.
    busy_left = sum(max(0.5, prep - (t - (o["started_at"] or t)) / 60.0) for o in preparing)
    for i, o in enumerate(waiting):
        o["position"] = i + 1
        o["eta_min"] = round(busy_left + i * prep, 1)
        o["waited_min"] = round((t - o["created_at"]) / 60.0, 1)
    ready = db.rows("SELECT * FROM pharmacy_orders WHERE status='ready' ORDER BY ready_at")
    done = db.rows("SELECT * FROM pharmacy_orders WHERE day=? AND status IN ('collected','cancelled') "
                   "ORDER BY COALESCE(collected_at, created_at) DESC LIMIT 30", (db.today(),))
    stats = db.row("SELECT COUNT(*) n, AVG(started_at - created_at) w FROM pharmacy_orders "
                   "WHERE day=? AND started_at IS NOT NULL", (db.today(),))
    return {"server_time": t, "waiting": waiting, "preparing": preparing, "ready": ready, "done": done,
            "prep_min": prep, "avg_wait_min": round(stats["w"] / 60.0, 1) if stats and stats["w"] else None,
            "served_today": stats["n"] if stats else 0}


def start_next() -> Dict[str, Any]:
    """Begin the order at the head of the queue. FCFS: there is no way to pick another one."""
    with lock:
        q = queue()
        if not q:
            raise SchedulerError("No orders are waiting")
        o = q[0]
        db.update("pharmacy_orders", o["id"], {"status": "preparing", "started_at": time.time()})
    log("PHARMACY", "Preparing order #%d%s (first in line, waited %d min)" % (
        o["id"], " for token %s" % o["token"] if o["token"] else "", (time.time() - o["created_at"]) // 60),
        o["patient_id"])
    bus.publish("pharmacy", {"order_id": o["id"]})
    return get(o["id"])


def set_status(oid: int, status: str) -> Dict[str, Any]:
    allowed = {"ready": ("preparing",), "collected": ("ready",), "cancelled": ("waiting", "preparing", "ready")}
    if status not in allowed:
        raise SchedulerError("Unknown pharmacy status")
    with lock:
        o = get(oid)
        if not o:
            raise SchedulerError("Order not found")
        if o["status"] not in allowed[status]:
            raise SchedulerError("Order #%d is %s, so it can't be marked %s" % (oid, o["status"], status))
        field = {"ready": "ready_at", "collected": "collected_at", "cancelled": "collected_at"}[status]
        db.update("pharmacy_orders", oid, {"status": status, field: time.time()})
    label = {"ready": "is ready for collection", "collected": "was collected", "cancelled": "was cancelled"}[status]
    log("PHARMACY", "Order #%d%s %s" % (oid, " for token %s" % o["token"] if o["token"] else "", label),
        o["patient_id"])
    bus.publish("pharmacy", {"order_id": oid, "status": status, "token": o["token"]})
    return get(oid)


def for_patient(patient_id: int) -> Optional[Dict[str, Any]]:
    """The latest order of one patient, with its place in the FCFS queue."""
    o = db.row("SELECT * FROM pharmacy_orders WHERE patient_id=? ORDER BY id DESC LIMIT 1", (patient_id,))
    if not o:
        return None
    if o["status"] == "waiting":
        st = state()
        mine = next((x for x in st["waiting"] if x["id"] == o["id"]), None)
        if mine:
            o["position"], o["eta_min"] = mine["position"], mine["eta_min"]
    return {k: o.get(k) for k in ("id", "status", "items", "position", "eta_min", "created_at", "ready_at")}


def ready_tokens() -> List[str]:
    return [r["token"] for r in db.rows("SELECT token FROM pharmacy_orders WHERE status='ready' AND token IS NOT NULL "
                                        "ORDER BY ready_at")]
