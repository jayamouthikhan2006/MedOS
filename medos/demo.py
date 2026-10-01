"""Demo data so every dashboard has something real to show on first boot."""
from __future__ import annotations

import random
import time

from . import db
from .events import bus, log
from .scheduler import scheduler

WAITING = [
    ("Lakshmi Narayanan", 67, "female", "Severe abdominal pain since morning, vomited twice", {}, 38),
    ("Rahul Verma", 24, "male", "Fell from bike, swelling and suspected fracture in left wrist", {"pain": 7}, 25),
    ("Fathima Begum", 34, "female", "Fever with chills for two days, feels very weak", {"temp": 103.1}, 31),
    ("Suresh Kumar", 45, "male", "Lower back pain after lifting a heavy box", {}, 52),
    ("Meena Iyer", 29, "female", "Sore throat and slight fever since yesterday", {"temp": 99.8}, 47),
    ("Arun Prakash", 8, "male", "Ear pain since last night, crying a lot", {}, 20),
    ("Divya Subramanian", 31, "female", "Itchy skin rash on both arms", {}, 58),
    ("Mohammed Irfan", 52, "male", "Routine check-up, blood pressure review", {}, 44),
    ("Kavya Reddy", 19, "female", "Mild asthma, wheezing since evening, inhaler helped a little", {"spo2": 95}, 12),
    ("Gopal Krishnan", 71, "male", "Cold and cough for four days", {}, 35),
    ("Priyanka Das", 27, "female", "Persistent vomiting since yesterday, feeling dizzy", {}, 9),
    ("Venkatesh Raman", 40, "male", "Migraine with nausea and light sensitivity", {}, 15),
]

EARLIER = [
    ("Chest pain radiating to left arm with sweating", {"pulse": 118}, "Admitted"),
    ("Cold and cough", {}, "Prescribed"),
    ("Stomach ache after food", {}, "Prescribed"),
    ("Fracture of right ankle after a fall", {"pain": 8}, "Referred"),
    ("Routine check-up", {}, "Discharged"),
    ("Sore throat", {}, "Prescribed"),
    ("High fever 102.8 for three days", {"temp": 102.8}, "Prescribed"),
    ("Back pain", {}, "Discharged"),
    ("Skin allergy after new soap", {}, "Prescribed"),
    ("Mild fever and body ache", {"temp": 100.2}, "Prescribed"),
    ("Severe headache since morning", {}, "Prescribed"),
    ("Ear pain", {}, "Prescribed"),
    ("Shortness of breath while walking", {"spo2": 91}, "Admitted"),
    ("Persistent vomiting and loose motion", {}, "Follow-up"),
    ("Follow up visit for diabetes", {}, "Discharged"),
    ("Cough with phlegm, suspected chest infection", {"temp": 100.9}, "Prescribed"),
]

NAMES = ["Anitha", "Balaji", "Charulatha", "Deepak", "Esther", "Farhan", "Geetha", "Harish", "Indira",
         "Jagan", "Kamala", "Lokesh", "Malini", "Naveen", "Oviya", "Pradeep", "Revathi", "Sanjay"]


def clear() -> None:
    with scheduler.lock:
        db.execute("DELETE FROM patients")
        db.execute("DELETE FROM alerts")
        db.execute("DELETE FROM events")
        db.execute("DELETE FROM pharmacy_orders")
        db.execute("DELETE FROM ambulance_requests")
        db.execute("UPDATE ambulances SET status='available', request_id=NULL")
        db.execute("UPDATE doctors SET current_patient_id=NULL, "
                   "status=CASE WHEN status='busy' THEN 'available' ELSE status END")
    if scheduler.hardware:
        scheduler.hardware.alarm_off()
    log("SYSTEM", "Patient data cleared")
    bus.publish("queue", {"reason": "clear"})


def seed() -> None:
    rnd = random.Random(7)
    t = time.time()
    floor = db.day_start() + 60
    hook = scheduler.ai_hook
    scheduler.ai_hook = None
    try:
        doctors = db.rows("SELECT * FROM doctors ORDER BY id")
        # Earlier today: completed visits for the analytics.
        span = min(4.5 * 3600, max(0.0, t - floor - 3600))
        for i, (symptoms, vitals, outcome) in enumerate(EARLIER):
            arrived = t - 3600 - span + span * i / len(EARLIER)
            arrived = max(floor, arrived)
            p = scheduler.register({"name": "%s %s" % (NAMES[i % len(NAMES)], rnd.choice("KMRSPV")),
                                    "age": rnd.randint(6, 78), "sex": rnd.choice(["male", "female"]),
                                    "symptoms": symptoms, "vitals": vitals},
                                   source=rnd.choice(["reception", "reception", "voice", "kiosk"]),
                                   arrived_at=arrived, dispatch=False)
            wait = {"critical": 1, "high": 6, "medium": 14, "low": 24}[p["level"]] * 60 * rnd.uniform(0.6, 1.4)
            consult = {"critical": 24, "high": 14, "medium": 10, "low": 7}[p["level"]] * 60 * rnd.uniform(0.7, 1.3)
            d = doctors[i % len(doctors)] if doctors else None
            db.update("patients", p["id"], {
                "status": "completed", "called_at": arrived + wait, "completed_at": arrived + wait + consult,
                "wait_seconds": wait, "consult_seconds": consult, "outcome": outcome,
                "doctor_id": d["id"] if d else None, "room_id": d["room_id"] if d else None,
                "ai_status": "off"})
        # Waiting now.
        for name, age, sex, symptoms, vitals, mins in WAITING:
            scheduler.register({"name": name, "age": age, "sex": sex, "symptoms": symptoms, "vitals": vitals},
                               source=rnd.choice(["reception", "voice", "kiosk"]),
                               arrived_at=max(floor, t - mins * 60), dispatch=False)
        # Three doctors on duty, one on a break.
        for i, d in enumerate(doctors):
            status = "break" if i == len(doctors) - 1 and len(doctors) > 2 else "available"
            db.update("doctors", d["id"], {"status": status, "status_since": t - 600 + i})
    finally:
        scheduler.ai_hook = hook
    assigned = scheduler.dispatch(reason="demo")
    for i, a in enumerate(assigned):
        back = (4 + 3 * i) * 60
        p = scheduler.get(a["patient_id"])
        db.update("patients", p["id"], {"called_at": t - back,
                                        "wait_seconds": max(0.0, (t - back) - p["arrived_at"])})
    # Prescriptions from earlier visits: a few collected, then the FCFS pharmacy queue.
    meds = ["Paracetamol 500 mg, 1 tablet 3 times a day for 3 days", "Cetirizine 10 mg at night for 5 days",
            "ORS sachets, 1 after every loose motion; Ondansetron 4 mg if vomiting",
            "Amoxicillin 500 mg 3 times a day for 5 days", "Pantoprazole 40 mg before breakfast for 7 days",
            "Ibuprofen 400 mg twice a day after food for 3 days"]
    done = db.rows("SELECT * FROM patients WHERE status='completed' ORDER BY completed_at")
    for i, p in enumerate(done[-7:]):
        made = (p["completed_at"] or t) + 60
        oid = db.insert("pharmacy_orders", {"day": db.today(), "patient_id": p["id"], "token": p["token"],
                                            "patient_name": p["name"], "items": meds[i % len(meds)],
                                            "prescribed_by": "Dr. Karthik Menon", "source": "doctor",
                                            "status": "waiting", "created_at": min(made, t - 60 * (7 - i))})
        o = db.row("SELECT * FROM pharmacy_orders WHERE id=?", (oid,))
        if i < 3:
            db.update("pharmacy_orders", oid, {"status": "collected", "started_at": o["created_at"] + 120,
                                               "ready_at": o["created_at"] + 360, "collected_at": o["created_at"] + 600})
        elif i == 3:
            db.update("pharmacy_orders", oid, {"status": "ready", "started_at": o["created_at"] + 90,
                                               "ready_at": o["created_at"] + 300})
        elif i == 4:
            db.update("pharmacy_orders", oid, {"status": "preparing", "started_at": t - 120})
    db.set_setting("demo_seeded", True)
    log("SYSTEM", "Demo patients loaded: %d waiting, %d earlier today" % (len(WAITING), len(EARLIER)))
    bus.publish("queue", {"reason": "demo"})
    if hook:
        for p in db.rows("SELECT id FROM patients WHERE status IN ('waiting','in_consultation') ORDER BY id"):
            db.update("patients", p["id"], {"ai_status": "pending"})
            hook(p["id"])
