"""Event bus (Server-Sent Events) and the scheduler's kernel log.

publish() wakes every connected dashboard; log() also writes a persistent line to the
`events` table that the System monitor shows as the kernel log.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Iterator, List, Optional, Tuple

from . import db


class EventBus:
    def __init__(self, capacity: int = 500):
        self._cond = threading.Condition()
        self._events: Deque[Tuple[int, str, Dict[str, Any]]] = deque(maxlen=capacity)
        self._next_id = 1
        self.published = 0
        self.clients = 0

    def publish(self, kind: str, data: Optional[Dict[str, Any]] = None) -> int:
        with self._cond:
            eid = self._next_id
            self._next_id += 1
            self._events.append((eid, kind, data or {}))
            self.published += 1
            self._cond.notify_all()
            return eid

    @property
    def last_id(self) -> int:
        return self._next_id - 1

    def _after(self, last_id: int) -> List[Tuple[int, str, Dict[str, Any]]]:
        return [e for e in self._events if e[0] > last_id]

    def stream(self, last_id: Optional[int] = None, heartbeat: float = 15.0) -> Iterator[str]:
        """Generator of SSE frames. Blocks on a condition variable between events."""
        if last_id is None:
            last_id = self.last_id
        with self._cond:
            self.clients += 1
        try:
            yield "retry: 2000\n\n"
            yield "event: hello\ndata: %s\n\n" % json.dumps({"id": last_id, "ts": time.time()})
            while True:
                with self._cond:
                    pending = self._after(last_id)
                    if not pending:
                        self._cond.wait(timeout=heartbeat)
                        pending = self._after(last_id)
                if not pending:
                    yield ": ping %d\n\n" % int(time.time())
                    continue
                for eid, kind, data in pending:
                    last_id = eid
                    yield "id: %d\nevent: %s\ndata: %s\n\n" % (eid, kind, json.dumps(data, default=str))
        finally:
            with self._cond:
                self.clients -= 1


bus = EventBus()


def log(kind: str, message: str, patient_id: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None, publish: bool = True) -> None:
    """Write a kernel-log line and broadcast it."""
    ts = time.time()
    db.insert("events", {"ts": ts, "kind": kind, "message": message, "patient_id": patient_id,
                         "data": json.dumps(data) if data else None})
    if publish:
        bus.publish("log", {"ts": ts, "kind": kind, "message": message, "patient_id": patient_id})


def recent(limit: int = 100, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    if kind:
        return db.rows("SELECT * FROM events WHERE kind=? ORDER BY id DESC LIMIT ?", (kind, limit))
    return db.rows("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
