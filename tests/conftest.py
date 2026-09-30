import os
import sys
import tempfile
from pathlib import Path

# Isolated data directory for every test run, simulated GPIO, AI off.
_tmp = tempfile.mkdtemp(prefix="medos-test-")
os.environ["MEDOS_DATA"] = _tmp
os.environ["MEDOS_SIMULATE_GPIO"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from medos import db  # noqa: E402


@pytest.fixture()
def fresh_db():
    db.init()
    db.set_setting("llm_enabled", False)
    db.set_setting("auto_dispatch", True)
    db.set_setting("preemption", "emergency")
    for t in ("patients", "alerts", "events"):
        db.execute("DELETE FROM %s" % t)
    db.execute("UPDATE doctors SET status='off_duty', current_patient_id=NULL")
    from medos.scheduler import scheduler
    scheduler.invalidate_conditions()
    scheduler.ai_hook = None
    yield db
