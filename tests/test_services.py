import time

import pytest

from medos import db


@pytest.fixture()
def services(fresh_db):
    for t in ("pharmacy_orders", "ambulance_requests"):
        db.execute("DELETE FROM %s" % t)
    db.execute("UPDATE ambulances SET status='available', request_id=NULL")
    db.execute("UPDATE hospitals SET active=1")
    from medos import ambulance, pharmacy
    return pharmacy, ambulance


# ------------------------------------------------------------------ pharmacy: FCFS
def test_pharmacy_is_first_come_first_served(services):
    pharmacy, _ = services
    t = time.time()
    a = pharmacy.create_order("Paracetamol", token="010", created_at=t - 300)
    b = pharmacy.create_order("Cetirizine", token="002", created_at=t - 200)   # lower token, but later
    c = pharmacy.create_order("ORS", token="030", created_at=t - 100)
    assert [o["id"] for o in pharmacy.queue()] == [a["id"], b["id"], c["id"]]
    assert pharmacy.start_next()["id"] == a["id"]          # only the head can be started
    assert pharmacy.start_next()["id"] == b["id"]
    etas = [o["eta_min"] for o in pharmacy.state()["waiting"]]
    assert etas == sorted(etas)


def test_pharmacy_status_flow(services):
    pharmacy, _ = services
    o = pharmacy.create_order("Amoxicillin", token="011")
    with pytest.raises(Exception):
        pharmacy.set_status(o["id"], "ready")              # can't be ready before it's prepared
    pharmacy.start_next()
    pharmacy.set_status(o["id"], "ready")
    assert pharmacy.ready_tokens() == ["011"]
    pharmacy.set_status(o["id"], "collected")
    assert pharmacy.ready_tokens() == []


def test_prescription_on_finish_goes_to_pharmacy(services):
    pharmacy, _ = services
    from medos.scheduler import scheduler
    did = db.scalar("SELECT id FROM doctors ORDER BY id")
    db.update("doctors", did, {"status": "available", "status_since": time.time()})
    p = scheduler.register({"symptoms": "sore throat"})
    scheduler.finish(did, "Prescribed", "", "available", medicines="Azithromycin 500 mg for 3 days")
    q = pharmacy.queue()
    assert q and q[-1]["patient_id"] == p["id"] and q[-1]["token"] == p["token"]


# ------------------------------------------------------------------ ambulance: SJF hospital choice and dispatch
def test_hospital_choice_is_shortest_time_to_treatment(services):
    _, ambulance = services
    rec = ambulance.recommend("road accident, head injury, bleeding", "emergency", "Anna Nagar")
    eligible = [r for r in rec["ranking"] if r["eligible"]]
    assert rec["best"]["id"] == eligible[0]["id"]
    assert rec["best"]["total_min"] == min(r["total_min"] for r in eligible)
    assert all("emergency" in (db.row("SELECT specialties FROM hospitals WHERE id=?", (r["id"],))["specialties"].lower())
               for r in eligible)


def test_specialist_is_required_when_available(services):
    _, ambulance = services
    rec = ambulance.recommend("crushing chest pain and sweating", "emergency", "Porur")
    assert "Cardiology" in rec["needs"]
    assert "Cardiology" in db.row("SELECT specialties FROM hospitals WHERE id=?", (rec["best"]["id"],))["specialties"]


def test_hospital_without_beds_is_skipped(services):
    _, ambulance = services
    first = ambulance.recommend("high fever", "non_emergency", "Guindy")["best"]
    db.update("hospitals", first["id"], {"beds_free": 0})
    again = ambulance.recommend("high fever", "non_emergency", "Guindy")
    assert again["best"]["id"] != first["id"]
    assert next(r for r in again["ranking"] if r["id"] == first["id"])["reason"] == "No free beds"


def test_dispatch_emergency_first_then_shortest_trip(services):
    _, ambulance = services
    db.execute("UPDATE ambulances SET status='on_trip'")          # hold dispatch so the queue builds
    far = ambulance.create_request({"kind": "non_emergency", "area": "Tambaram", "condition": "routine transfer"})
    near = ambulance.create_request({"kind": "non_emergency", "area": "T. Nagar", "condition": "routine transfer"})
    em = ambulance.create_request({"kind": "emergency", "area": "Tambaram", "condition": "unconscious, not breathing"})
    order = [r["id"] for r in ambulance.ordered_waiting()]
    assert order == [em["id"], near["id"], far["id"]]
    # Aging: a long trip that has waited long enough overtakes a fresh short one.
    db.update("ambulance_requests", far["id"], {"created_at": time.time() - 7200})
    order = [r["id"] for r in ambulance.ordered_waiting()]
    assert order.index(far["id"]) < order.index(near["id"])
