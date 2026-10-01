"""SQLite storage: one connection per thread, WAL journaling, typed settings."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from werkzeug.security import check_password_hash, generate_password_hash

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rooms (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'consultation',
    status     TEXT NOT NULL DEFAULT 'open',
    created_at REAL
);
CREATE TABLE IF NOT EXISTS doctors (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL,
    specialty          TEXT NOT NULL DEFAULT 'General Medicine',
    room_id            INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
    status             TEXT NOT NULL DEFAULT 'off_duty',
    current_patient_id INTEGER,
    status_since       REAL,
    created_at         REAL
);
CREATE TABLE IF NOT EXISTS patients (
    id              INTEGER PRIMARY KEY,
    token           TEXT NOT NULL,
    code            TEXT NOT NULL UNIQUE,
    day             TEXT NOT NULL,
    name            TEXT,
    age             INTEGER,
    sex             TEXT,
    phone           TEXT,
    symptoms        TEXT,
    vitals          TEXT,
    pregnant        INTEGER NOT NULL DEFAULT 0,
    level           TEXT NOT NULL,
    rank            INTEGER NOT NULL,
    base_score      REAL NOT NULL,
    primary_condition TEXT,
    conditions      TEXT,
    red_flags       TEXT,
    reasons         TEXT,
    department      TEXT,
    rules_level     TEXT,
    level_source    TEXT NOT NULL DEFAULT 'rules',
    ai_status       TEXT NOT NULL DEFAULT 'off',
    ai_level        TEXT,
    ai_summary      TEXT,
    ai_reasoning    TEXT,
    ai_department   TEXT,
    ai_brief        TEXT,
    source          TEXT NOT NULL DEFAULT 'reception',
    emergency       INTEGER NOT NULL DEFAULT 0,
    preempted       INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'waiting',
    doctor_id       INTEGER,
    room_id         INTEGER,
    arrived_at      REAL NOT NULL,
    called_at       REAL,
    completed_at    REAL,
    wait_seconds    REAL,
    consult_seconds REAL NOT NULL DEFAULT 0,
    recalls         INTEGER NOT NULL DEFAULT 0,
    outcome         TEXT,
    doctor_notes    TEXT
);
CREATE INDEX IF NOT EXISTS idx_patients_status ON patients(status);
CREATE INDEX IF NOT EXISTS idx_patients_day ON patients(day);
CREATE TABLE IF NOT EXISTS conditions (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    rank       INTEGER NOT NULL,
    level      TEXT NOT NULL,
    reason     TEXT,
    keywords   TEXT NOT NULL DEFAULT '',
    red_flag   INTEGER NOT NULL DEFAULT 0,
    department TEXT,
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY,
    ts              REAL NOT NULL,
    source          TEXT NOT NULL,
    message         TEXT NOT NULL,
    patient_id      INTEGER,
    acknowledged_at REAL,
    acknowledged_by TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    ts         REAL NOT NULL,
    kind       TEXT NOT NULL,
    message    TEXT NOT NULL,
    patient_id INTEGER,
    data       TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS pharmacy_orders (
    id           INTEGER PRIMARY KEY,
    day          TEXT NOT NULL,
    patient_id   INTEGER,
    token        TEXT,
    patient_name TEXT,
    items        TEXT NOT NULL,
    prescribed_by TEXT,
    source       TEXT NOT NULL DEFAULT 'doctor',
    status       TEXT NOT NULL DEFAULT 'waiting',
    created_at   REAL NOT NULL,
    started_at   REAL,
    ready_at     REAL,
    collected_at REAL
);
CREATE INDEX IF NOT EXISTS idx_pharmacy_status ON pharmacy_orders(status);
CREATE TABLE IF NOT EXISTS hospitals (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    x_km        REAL NOT NULL DEFAULT 0,
    y_km        REAL NOT NULL DEFAULT 0,
    specialties TEXT NOT NULL DEFAULT '',
    beds_free   INTEGER NOT NULL DEFAULT 5,
    er_wait_min REAL NOT NULL DEFAULT 20,
    is_self     INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ambulances (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'available',
    request_id   INTEGER,
    status_since REAL
);
CREATE TABLE IF NOT EXISTS ambulance_requests (
    id            INTEGER PRIMARY KEY,
    day           TEXT NOT NULL,
    kind          TEXT NOT NULL,
    patient_name  TEXT,
    age           INTEGER,
    condition     TEXT NOT NULL,
    level         TEXT NOT NULL,
    needs         TEXT,
    pickup        TEXT NOT NULL,
    pickup_min    REAL NOT NULL,
    hospital_id   INTEGER,
    hospital_name TEXT,
    travel_min    REAL,
    wait_min      REAL,
    total_min     REAL,
    ranking       TEXT,
    status        TEXT NOT NULL DEFAULT 'waiting',
    ambulance_id  INTEGER,
    patient_id    INTEGER,
    created_at    REAL NOT NULL,
    dispatched_at REAL,
    arrived_at    REAL
);
"""

DEFAULT_SETTINGS: Dict[str, Any] = {
    "hospital_name": "City General Hospital",
    # Scheduler
    "aging_rate": 5.0,          # priority points gained per minute of waiting
    "aging_cap": 450.0,         # aging can lift a case at most this far (keeps Critical untouchable)
    "tick_seconds": 15,
    "preemption": "emergency",  # off | emergency | critical
    "auto_dispatch": True,
    "notify_ahead": 2,
    "dur_critical": 25,
    "dur_high": 15,
    "dur_medium": 10,
    "dur_low": 7,
    # Access
    "require_pin": True,
    "admin_pin": generate_password_hash("1234"),
    "staff_pin": generate_password_hash("1111"),
    "pins_default": True,
    # AI (Qwen 3 through Ollama)
    "llm_enabled": True,
    "llm_url": "http://localhost:11434",
    "llm_model": "qwen3:4b",
    "llm_timeout": 120,
    "ai_triage": True,
    # Voice + display
    "voice_lang": "en-IN",
    "announce": True,
    "buzz_on_call": False,
    "alarm_buzzer_seconds": 20,
    "display_message": "Keep your token slip with you. Emergency cases are always seen first.",
    # Pharmacy (FCFS) and ambulance desk (SJF)
    "pharmacy_prep_min": 4,
    "ambulance_speed_kmh": 30,
    "demo_seeded": False,
}

PUBLIC_SETTINGS = (
    "hospital_name", "aging_rate", "aging_cap", "tick_seconds", "preemption", "auto_dispatch",
    "notify_ahead", "dur_critical", "dur_high", "dur_medium", "dur_low", "require_pin",
    "pins_default", "llm_enabled", "llm_url", "llm_model", "llm_timeout", "ai_triage",
    "voice_lang", "announce", "buzz_on_call", "alarm_buzzer_seconds", "display_message",
    "pharmacy_prep_min", "ambulance_speed_kmh",
)

_local = threading.local()
_write_lock = threading.RLock()


def now() -> float:
    return time.time()


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def day_start(day: Optional[str] = None) -> float:
    d = datetime.strptime(day or today(), "%Y-%m-%d")
    return d.timestamp()


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(str(config.DB_PATH), timeout=10, isolation_level=None,
                            check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=5000")
        _local.conn = c
    return c


def rows(sql: str, params: Iterable = ()) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn().execute(sql, tuple(params)).fetchall()]


def row(sql: str, params: Iterable = ()) -> Optional[Dict[str, Any]]:
    r = conn().execute(sql, tuple(params)).fetchone()
    return dict(r) if r else None


def scalar(sql: str, params: Iterable = ()) -> Any:
    r = conn().execute(sql, tuple(params)).fetchone()
    return r[0] if r else None


def execute(sql: str, params: Iterable = ()) -> int:
    with _write_lock:
        cur = conn().execute(sql, tuple(params))
        return cur.lastrowid


class transaction:
    """BEGIN IMMEDIATE … COMMIT, serialised across threads."""

    def __enter__(self):
        _write_lock.acquire()
        conn().execute("BEGIN IMMEDIATE")
        return conn()

    def __exit__(self, exc_type, exc, tb):
        try:
            conn().execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            _write_lock.release()
        return False


def insert(table: str, data: Dict[str, Any]) -> int:
    keys = list(data.keys())
    sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, ",".join(keys), ",".join("?" * len(keys)))
    return execute(sql, [data[k] for k in keys])


def update(table: str, id_: int, data: Dict[str, Any]) -> None:
    if not data:
        return
    keys = list(data.keys())
    sql = "UPDATE %s SET %s WHERE id=?" % (table, ",".join("%s=?" % k for k in keys))
    execute(sql, [data[k] for k in keys] + [id_])


# ---------------------------------------------------------------- settings

_settings_cache: Dict[str, Any] = {}


def get_setting(key: str) -> Any:
    if key in _settings_cache:
        return _settings_cache[key]
    r = row("SELECT value FROM settings WHERE key=?", (key,))
    val = json.loads(r["value"]) if r else DEFAULT_SETTINGS.get(key)
    _settings_cache[key] = val
    return val


def set_setting(key: str, value: Any) -> None:
    execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)))
    _settings_cache[key] = value


def settings(public: bool = True) -> Dict[str, Any]:
    keys = PUBLIC_SETTINGS if public else DEFAULT_SETTINGS.keys()
    return {k: get_setting(k) for k in keys}


def check_pin(role: str, pin: str) -> bool:
    key = "admin_pin" if role == "admin" else "staff_pin"
    stored = get_setting(key)
    return bool(stored) and check_password_hash(stored, pin or "")


def set_pin(role: str, pin: str) -> None:
    set_setting("admin_pin" if role == "admin" else "staff_pin", generate_password_hash(pin))
    set_setting("pins_default", False)


# ---------------------------------------------------------------- JSON helpers

JSON_FIELDS = ("vitals", "conditions", "red_flags", "reasons")


def decode_patient(p: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if p is None:
        return None
    for f in JSON_FIELDS:
        v = p.get(f)
        if isinstance(v, str):
            try:
                p[f] = json.loads(v)
            except ValueError:
                p[f] = None
    return p


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------- bootstrap

def seed_conditions() -> None:
    """(Re)load the default triage protocol: ten red flags plus the 15-row priority table."""
    from .triage import DEFAULT_CONDITIONS
    with transaction():
        execute("DELETE FROM conditions")
        for cond in DEFAULT_CONDITIONS:
            insert("conditions", {
                "name": cond["name"], "rank": cond["rank"], "level": cond["level"],
                "reason": cond["reason"], "keywords": ", ".join(cond["keywords"]),
                "red_flag": 1 if cond.get("red_flag") else 0, "department": cond.get("department"),
                "active": 1,
            })


def init() -> None:
    c = conn()
    c.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, json.dumps(v)))
    _settings_cache.clear()
    if scalar("SELECT COUNT(*) FROM conditions") == 0:
        seed_conditions()
    if scalar("SELECT COUNT(*) FROM rooms") == 0:
        t = now()
        for name, kind in (("ER Bay 1", "emergency"), ("Room 1", "consultation"),
                           ("Room 2", "consultation"), ("Room 3", "consultation"),
                           ("Procedure Room", "procedure")):
            insert("rooms", {"name": name, "kind": kind, "status": "open", "created_at": t})
    if scalar("SELECT COUNT(*) FROM doctors") == 0:
        t = now()
        rooms_by_name = {r["name"]: r["id"] for r in rows("SELECT id,name FROM rooms")}
        for name, spec, room in (("Dr. Ananya Rao", "Emergency Medicine", "ER Bay 1"),
                                 ("Dr. Karthik Menon", "General Medicine", "Room 1"),
                                 ("Dr. Priya Sharma", "Paediatrics", "Room 2"),
                                 ("Dr. Arjun Iyer", "Orthopaedics", "Room 3")):
            insert("doctors", {"name": name, "specialty": spec, "room_id": rooms_by_name.get(room),
                               "status": "off_duty", "status_since": t, "created_at": t})
    from .ambulance import seed_defaults
    seed_defaults()
