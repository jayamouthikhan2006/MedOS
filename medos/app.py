"""Flask application factory and page routes."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for

from . import __version__, api as api_module, config, db
from .api import api, current_role
from .events import log
from .hardware import Hardware
from .llm import ai_worker
from .scheduler import scheduler

PAGES = {
    "reception": ("Reception desk", ("reception",)),
    "doctor": ("Doctor", ("doctor",)),
    "admin": ("Admin", ()),
    "pharmacy": ("Pharmacy", ("pharmacy", "reception", "doctor")),
    "ambulance": ("Ambulance desk", ("reception", "doctor")),
}


def create_app(start_background: bool = True) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.update(SECRET_KEY=config.secret_key(), PERMANENT_SESSION_LIFETIME=timedelta(days=7),
                      SESSION_COOKIE_SAMESITE="Lax", JSON_SORT_KEYS=False,
                      SEND_FILE_MAX_AGE_DEFAULT=0, TEMPLATES_AUTO_RELOAD=True)
    app.json.sort_keys = False
    db.init()

    hw = Hardware(on_button=lambda: scheduler.raise_emergency("gpio"))
    scheduler.hardware = hw
    api_module.hardware = hw
    scheduler.ai_hook = ai_worker.submit
    app.extensions["medos_hardware"] = hw

    app.register_blueprint(api)

    static_dir = Path(app.static_folder)

    @app.context_processor
    def inject():
        # Cache-buster that changes whenever a stylesheet or script is edited.
        stamp = max((f.stat().st_mtime for f in static_dir.glob("*/*.*") if f.suffix in (".css", ".js")), default=0)
        return {"hospital_name": db.get_setting("hospital_name"), "version": __version__,
                "role": current_role(), "static_v": "%s-%d" % (__version__, stamp)}

    def allowed(page: str) -> bool:
        role = current_role()
        if role is None:
            return False
        roles = PAGES[page][1]
        return role == "admin" or role in roles

    @app.get("/")
    def launcher():
        return render_template("launcher.html", page="home")

    @app.get("/login")
    def login():
        return render_template("login.html", page="login", want=request.args.get("role") or "reception",
                               next=request.args.get("next") or "")

    def staff_page(page: str):
        if not allowed(page):
            want = {"ambulance": "reception"}.get(page, page)
            return redirect(url_for("login", role=want, next=request.path))
        return render_template("%s.html" % page, page=page)

    @app.get("/reception")
    def reception():
        return staff_page("reception")

    @app.get("/doctor")
    def doctor():
        return staff_page("doctor")

    @app.get("/admin")
    def admin():
        return staff_page("admin")

    @app.get("/pharmacy")
    def pharmacy_page():
        return staff_page("pharmacy")

    @app.get("/ambulance")
    def ambulance_page():
        return staff_page("ambulance")

    @app.get("/patient")
    def patient_lookup():
        return render_template("patient.html", page="patient", code="")

    @app.get("/p/<code>")
    def patient(code: str):
        return render_template("patient.html", page="patient", code=code)

    @app.get("/display")
    def display():
        return render_template("display.html", page="display")

    @app.get("/kiosk")
    def kiosk():
        return render_template("kiosk.html", page="kiosk")

    @app.get("/lab")
    def lab():
        return render_template("lab.html", page="lab")

    @app.get("/logout")
    def logout():
        session.pop("role", None)
        return redirect(url_for("launcher"))

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        if resp.mimetype == "application/json":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    if start_background:
        scheduler.start()
        ai_worker.start()
        from .voice import voice
        if voice.whisper_available():   # load the speech model now so the first recording is quick
            import threading
            threading.Thread(target=lambda: voice._get_whisper(), name="whisper-warmup", daemon=True).start()
        log("SYSTEM", "MedOS %s started — GPIO %s, scheduler tick %ss" % (
            __version__, hw.mode, db.get_setting("tick_seconds")))
        if config.SEED_DEMO and not db.get_setting("demo_seeded") and not db.scalar("SELECT COUNT(*) FROM patients"):
            from . import demo
            demo.seed()
    return app
