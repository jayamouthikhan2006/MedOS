"""Runtime configuration. Everything can be overridden with MEDOS_* environment variables."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MEDOS_DATA", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("MEDOS_DB", DATA_DIR / "medos.db"))
VOSK_MODEL_PATH = Path(os.environ.get("MEDOS_VOSK_MODEL", DATA_DIR / "vosk-model"))
CERT_DIR = DATA_DIR / "certs"

HOST = os.environ.get("MEDOS_HOST", "0.0.0.0")
PORT = int(os.environ.get("MEDOS_PORT", "8080"))
HTTPS_PORT = int(os.environ.get("MEDOS_HTTPS_PORT", "8443"))

# GPIO (BCM numbering). Button to GND, LED via 330 ohm resistor, active buzzer.
PIN_BUTTON = int(os.environ.get("MEDOS_PIN_BUTTON", "17"))
PIN_LED = int(os.environ.get("MEDOS_PIN_LED", "27"))
PIN_BUZZER = int(os.environ.get("MEDOS_PIN_BUZZER", "22"))

# Load demo patients on the very first start (set MEDOS_DEMO=0 for a real deployment).
SEED_DEMO = os.environ.get("MEDOS_DEMO", "1") != "0"

# Force simulated hardware even on a Pi (useful for testing).
SIMULATE_GPIO = os.environ.get("MEDOS_SIMULATE_GPIO", "0") == "1"


def secret_key() -> str:
    """A stable session secret stored in the data directory."""
    path = DATA_DIR / ".secret"
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_hex(32)
    path.write_text(key)
    return key
