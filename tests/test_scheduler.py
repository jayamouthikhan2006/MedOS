import threading
import time

from medos import db


def doctors_on(n):
    ids = [d["id"] for d in db.rows("SELECT id FROM doctors ORDER BY id")][:n]
    for i, did in enumerate(ids):
        db.update("doctors", did, {"status": "available", "status_since": time.time() - 100 + i})
    return ids


def test_priority_order_beats_fcfs(fresh_db):
    from medos.scheduler import scheduler
    t = time.time()
    a = scheduler.register({"symptoms": "cold and cough"}, arrived_at=t - 300, dispatch=False)
    b = scheduler.register({"symptoms": "fracture of the arm"}, arrived_at=t - 60, dispatch=False)
    c = scheduler.register({"symptoms": "chest pain"}, arrived_at=t, dispatch=False)
    order = [p["id"] for p in scheduler.ready_queue()]
    assert order == [c["id"], b["id"], a["id"]]


def test_aging_prevents_starvation(fresh_db):
    from medos.scheduler import scheduler
    t = time.time()
    old_low = scheduler.register({"symptoms": "back pain"}, arrived_at=t - 90 * 60, dispatch=False)
    new_med = scheduler.register({"symptoms": "migraine"}, arrived_at=t, dispatch=False)
    order = [p["id"] for p in scheduler.ready_queue()]
    assert order[0] == old_low["id"], "a low case waiting 90 min should overtake a fresh medium case"
    crit = scheduler.register({"symptoms": "seizure"}, arrived_at=t, dispatch=False)
    assert scheduler.ready_queue()[0]["id"] == crit["id"], "aging must never beat critical"
    assert new_med  # silence unused


def test_dispatch_to_free_doctor(fresh_db):
    from medos.scheduler import scheduler
    doctors_on(1)
    scheduler.register({"symptoms": "cold"}, dispatch=False)
    hi = scheduler.register({"symptoms": "severe abdominal pain"})
    assert scheduler.get(hi["id"])["status"] == "in_consultation"
    assert len(scheduler.ready_queue()) == 1


def test_finish_pulls_next(fresh_db):
    from medos.scheduler import scheduler
    (did,) = doctors_on(1)
    p1 = scheduler.register({"symptoms": "fracture"})
    p2 = scheduler.register({"symptoms": "sore throat"})
    assert scheduler.get(p2["id"])["status"] == "waiting"
    scheduler.finish(did, "Treated")
    assert scheduler.get(p1["id"])["status"] == "completed"
    assert scheduler.get(p2["id"])["status"] == "in_consultation"


def test_emergency_preempts_lowest(fresh_db):
    from medos.scheduler import scheduler
    doctors_on(2)
    low = scheduler.register({"symptoms": "routine check-up"})
    med = scheduler.register({"symptoms": "migraine"})
    assert scheduler.get(low["id"])["status"] == "in_consultation"
    em = scheduler.raise_emergency("gpio")
    assert scheduler.get(em["id"])["status"] == "in_consultation"
    lowp = scheduler.get(low["id"])
    assert lowp["status"] == "waiting" and lowp["preempted"] == 1
    assert scheduler.get(med["id"])["status"] == "in_consultation"
    assert scheduler.ready_queue()[0]["id"] == low["id"]
    assert db.scalar("SELECT COUNT(*) FROM alerts WHERE acknowledged_at IS NULL") == 1
    scheduler.acknowledge(None, "test")
    assert db.scalar("SELECT COUNT(*) FROM alerts WHERE acknowledged_at IS NULL") == 0


def test_eta_increases_down_the_queue(fresh_db):
    from medos.scheduler import scheduler
    doctors_on(1)
    scheduler.register({"symptoms": "fracture"})
    for s in ("cold", "rash", "ear pain"):
        scheduler.register({"symptoms": s})
    q = scheduler.ready_queue()
    eta = scheduler.estimates(q)
    vals = [eta[p["id"]] for p in q]
    assert vals == sorted(vals) and vals[0] > 0


def test_concurrent_doctors_never_share_a_patient(fresh_db):
    from medos.scheduler import scheduler
    db.set_setting("auto_dispatch", False)
    ids = doctors_on(4)
    for _ in range(3):
        scheduler.register({"symptoms": "cold"})
    results, errors = [], []

    def grab(did):
        try:
            results.append(scheduler.call_next(did)["patient_id"])
        except Exception as e:  # the fourth doctor finds the queue empty
            errors.append(str(e))
    threads = [threading.Thread(target=grab, args=(d,)) for d in ids]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    db.set_setting("auto_dispatch", True)
    assert len(results) == len(set(results)) == 3
    assert len(errors) == 1


def test_aging_daemon_logs_overtake(fresh_db):
    from medos.scheduler import scheduler
    t = time.time()
    low = scheduler.register({"symptoms": "back pain"}, arrived_at=t - 60 * 60, dispatch=False)
    med = scheduler.register({"symptoms": "migraine"}, arrived_at=t, dispatch=False)
    scheduler._last_order = []
    scheduler.tick()                       # medium first: 420 vs 80 + 300
    assert [p["id"] for p in scheduler.ready_queue()][0] == med["id"]
    db.update("patients", low["id"], {"arrived_at": t - 80 * 60})   # time passes for the low case
    scheduler.tick()
    assert [p["id"] for p in scheduler.ready_queue()][0] == low["id"]
    lines = db.rows("SELECT message FROM events WHERE kind='AGING'")
    assert any("Token %s" % low["token"] in r["message"] and "overtook" in r["message"] for r in lines)
