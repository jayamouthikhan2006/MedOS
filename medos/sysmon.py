"""System monitor: CPU, memory, temperature, threads — the OS side of MedOS made visible."""
from __future__ import annotations

import os
import platform
import threading
import time
from typing import Any, Dict, List

from . import config

try:
    import psutil  # type: ignore
except Exception:  # psutil is optional on the Pi
    psutil = None

BOOT = time.time()

THREAD_ROLES = {
    "MainThread": "Server main loop — accepts HTTP connections",
    "aging-daemon": "Scheduler daemon — ages waiting patients, re-orders the ready queue, dispatches",
    "ai-triage-worker": "Consumer — takes registrations from the AI queue and asks Qwen",
    "irq-handler": "Interrupt bottom-half — handles an emergency button press",
    "buzzer-timeout": "Timer — silences the buzzer after the alarm period",
    "https-server": "HTTPS listener (browser microphone access)",
    "voice-listener": "Recording from the USB microphone",
}


def _role(name: str) -> str:
    for key, role in THREAD_ROLES.items():
        if name.startswith(key):
            return role
    if name.startswith("Thread-") and "process_request_thread" in name:
        return "HTTP request / live stream"
    if name.startswith("Thread-"):
        return "HTTP request / live stream"
    if name.startswith("ollama-scan"):
        return "Network scan for the AI server"
    return "Library thread"


def cpu_temp() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def _meminfo() -> Dict[str, float]:
    out = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                out[k] = float(v.strip().split()[0]) * 1024
    except OSError:
        pass
    return out


_last_cpu = [0.0, 0.0]


def _proc_cpu_percent() -> float | None:
    try:
        with open("/proc/stat") as f:
            vals = [float(x) for x in f.readline().split()[1:]]
        idle, total = vals[3] + vals[4], sum(vals)
        d_idle, d_total = idle - _last_cpu[0], total - _last_cpu[1]
        _last_cpu[0], _last_cpu[1] = idle, total
        return round(100 * (1 - d_idle / d_total), 1) if d_total else None
    except (OSError, ValueError, IndexError):
        return None


def snapshot() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "hostname": platform.node(), "platform": platform.platform(terse=True),
        "python": platform.python_version(), "machine": platform.machine(),
        "pid": os.getpid(), "uptime": time.time() - BOOT, "cpu_temp": cpu_temp(),
        "cpu_count": os.cpu_count(),
    }
    try:
        info["model"] = open("/proc/device-tree/model").read().strip("\x00\n ")
    except OSError:
        info["model"] = None
    if psutil:
        vm = psutil.virtual_memory()
        proc = psutil.Process()
        info.update(cpu_percent=psutil.cpu_percent(interval=None),
                    per_cpu=psutil.cpu_percent(interval=None, percpu=True),
                    mem_total=vm.total, mem_used=vm.total - vm.available, mem_percent=vm.percent,
                    rss=proc.memory_info().rss, proc_threads=proc.num_threads(),
                    load=list(os.getloadavg()) if hasattr(os, "getloadavg") else None)
        try:
            du = psutil.disk_usage(str(config.DATA_DIR))
            info.update(disk_total=du.total, disk_used=du.used)
        except Exception:
            pass
    else:
        mi = _meminfo()
        total, avail = mi.get("MemTotal", 0), mi.get("MemAvailable", 0)
        rss = None
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss = float(line.split()[1]) * 1024
        except OSError:
            pass
        info.update(cpu_percent=_proc_cpu_percent(), per_cpu=None, mem_total=total, mem_used=total - avail,
                    mem_percent=round(100 * (total - avail) / total, 1) if total else None, rss=rss,
                    load=list(os.getloadavg()) if hasattr(os, "getloadavg") else None)
    try:
        info["db_size"] = sum(p.stat().st_size for p in config.DATA_DIR.glob("medos.db*"))
    except OSError:
        info["db_size"] = None
    return info


def threads() -> List[Dict[str, Any]]:
    out = []
    for t in threading.enumerate():
        out.append({"name": t.name, "daemon": t.daemon, "alive": t.is_alive(), "ident": t.ident,
                    "role": _role(t.name)})
    out.sort(key=lambda t: (t["name"] != "MainThread", t["name"].startswith("Thread-"), t["name"]))
    return out
