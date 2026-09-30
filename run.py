"""Start MedOS.

    python run.py                 # http://0.0.0.0:8080
    python run.py --port 80       # needs root or CAP_NET_BIND_SERVICE on Linux
    python run.py --https         # also serve https on 8443 so browsers allow the microphone
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading

from werkzeug.serving import make_server

from medos import __version__, config
from medos.app import create_app
from medos.llm import local_ipv4


def ensure_cert() -> tuple:
    config.CERT_DIR.mkdir(parents=True, exist_ok=True)
    crt, key = config.CERT_DIR / "medos.crt", config.CERT_DIR / "medos.key"
    if not crt.exists() or not key.exists():
        if not shutil.which("openssl"):
            raise SystemExit("openssl is needed to create the HTTPS certificate")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                        "-keyout", str(key), "-out", str(crt), "-subj", "/CN=medos.local"],
                       check=True, capture_output=True)
    return str(crt), str(key)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="MedOS — Smart Hospital Emergency Scheduler")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--https", action="store_true", help="also serve HTTPS (self-signed) for browser microphones")
    ap.add_argument("--https-port", type=int, default=config.HTTPS_PORT)
    args = ap.parse_args()

    # One log line per request would flood the Pi's journal (every screen polls and streams).
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app = create_app()
    server = make_server(args.host, args.port, app, threaded=True)

    urls = ["http://%s:%d" % (ip, args.port) for ip in local_ipv4()] or ["http://localhost:%d" % args.port]
    print("\n  MedOS %s is running" % __version__)
    print("  ─────────────────────────────────────────")
    print("  This device : http://localhost:%d" % args.port)
    for u in urls:
        print("  Network     : %s" % u)
    import socket
    print("  Hostname    : http://%s.local:%d" % (socket.gethostname().lower(), args.port))

    if args.https:
        crt, key = ensure_cert()
        https = make_server(args.host, args.https_port, app, threaded=True, ssl_context=(crt, key))
        threading.Thread(target=https.serve_forever, name="https-server", daemon=True).start()
        print("  HTTPS       : https://<address>:%d  (accept the certificate once)" % args.https_port)
    print("  Stop with Ctrl+C\n")

    def shutdown(*_):
        hw = app.extensions.get("medos_hardware")
        if hw:
            hw.alarm_off()
            hw.close()
        os._exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    sys.exit(main())
