# MedOS — Build Plan

Smart Hospital Emergency Scheduler using Dynamic Priority Scheduling.
Runs on a Raspberry Pi 4 (4 GB). Any laptop, tablet or phone on the same network opens it in a browser.

## 1. Goals (from the proposal PDF + the three reference images)

| Requirement | Where it lives |
|---|---|
| Replace FCFS with severity-based ordering | `medos/scheduler.py` — dynamic priority + aging |
| Voice registration **and** receptionist registration | Reception desk, Kiosk, `medos/voice.py` |
| Symptom analysis → Critical / High / Medium / Low | `medos/triage.py` (rules, always on) + Qwen 3 second opinion (`medos/llm.py`) |
| Highest-priority patient → next available doctor, automatically | `Scheduler.dispatch()` |
| Emergency override button (hardware + on-screen) | `medos/hardware.py` (GPIO interrupt) + `Scheduler.raise_emergency()` |
| Live dashboards: Admin, Reception, Doctor, Patient | `/admin`, `/reception`, `/doctor`, `/p/<code>` |
| Priority table (15 illnesses) | Seeded into the **Triage protocol**, editable by admin |
| GPIO: push button, red LED, active buzzer | `medos/hardware.py`, wiring shown in Admin → Hardware |
| OS concepts made visible | Admin → System monitor, and the Scheduler Lab |

## 2. Architecture

```
 Patient ─┬─ Voice (Pi USB mic / browser mic / kiosk) ──┐
          └─ Receptionist form ─────────────────────────┤
                                                        ▼
                                 Flask server on Raspberry Pi (threaded)
                                                        │
             ┌────────────── Triage engine (rules) ◄────┤────► AI triage worker ─► Qwen 3 (Ollama, laptop)
             ▼                                          │         (producer/consumer queue, upgrade-only)
   Dynamic Priority Scheduler  ◄── aging daemon (tick)  │
   (heap ready-queue, RLock)   ◄── GPIO interrupt (emergency button) ─► LED + buzzer
             │
             ▼
        SQLite (WAL)  ──►  Event bus (Server-Sent Events)  ──►  Admin · Reception · Doctor · Patient · TV display · Kiosk
```

## 3. Scheduling model

```
effective_priority = level_base + (16 - rank) * 10 + modifiers
                   + min(aging_rate * minutes_waited, aging_cap)     ← aging prevents starvation
                   → lifted to 1450 if the patient was pre-empted    ← resumes before other non-critical cases
                   + 5000 if emergency override                      ← interrupt
level_base: critical 1500 · high 600 · medium 300 · low 0      (defaults: aging 5/min, cap 450)
```

* Ties break by arrival time (FCFS inside equal priority).
* Aging lets a long-waiting case climb one tier (a Low case waiting ~70 min passes a fresh Medium one),
  but Critical is never overtaken: the top High case plus every modifier plus the full cap stays below it.
* Dispatch: every time a doctor becomes free (finish / available) or a patient arrives, the head of the
  ready queue goes to the longest-idle available doctor.
* Emergency override = interrupt: if no doctor is free, the doctor treating the lowest-priority
  non-critical patient is pre-empted; that patient returns to the queue with the pre-emption bonus.
* ETA: multi-server simulation of the queue against each doctor's expected free time,
  using the average consultation length per level (learned from today's data, defaults otherwise).

## 4. OS concepts → concrete code

| Concept | Implementation |
|---|---|
| Dynamic priority scheduling | `Scheduler.ready_queue()` — heap rebuilt with live scores |
| Aging | `AgingDaemon` thread recomputes scores every tick and logs overtakes |
| Pre-emption | `Scheduler._preempt_for()` |
| Interrupt handling | `gpiozero.Button.when_pressed` edge callback → `raise_emergency()` |
| Process synchronisation | `InstrumentedLock` (RLock with contention stats) guards every queue mutation; mic mutex for voice |
| Producer/consumer | AI triage `queue.Queue` + worker thread |
| Concurrency | Threaded HTTP server, SSE streams, aging daemon, AI worker, GPIO callbacks |
| Memory management | Lazy-loaded Vosk model, bounded event ring buffer, SQLite WAL, RSS shown live |

## 5. Screens

* **Launcher** — live hospital status, every role, QR code to join from a phone.
* **Reception** — register (typed or voice), live triage preview, AI second opinion, queue with priority bars, emergency hold-button, token slip with QR.
* **Doctor** — current patient, next patient, finish consultation (outcome + notes), AI pre-consultation brief, status (available / break / off duty), pre-emption notice.
* **Admin** — doctors available, rooms available, queue length, average wait, emergency alerts, charts, rooms floor plan, manage doctors/rooms/triage protocol, hardware panel, AI settings + shift report, system monitor, settings, data export.
* **Patient** — token number, queue position, estimated wait, notification when almost their turn.
* **Waiting-room display** — now calling (chime + spoken announcement), serving, queue by level.
* **Kiosk** — patient self check-in by voice on the Pi touchscreen.
* **Scheduler Lab** — FCFS vs Priority vs Priority + Aging vs Pre-emptive, Gantt charts and metrics.

## 6. Build order

1. Data layer (SQLite schema, settings, seed protocol & demo staff)
2. Triage engine + tests
3. Scheduler (aging, dispatch, pre-emption, ETA) + tests
4. Event bus, hardware, voice, LLM, system monitor
5. REST API + SSE
6. Design system (CSS tokens, fonts, icons) and shared JS core
7. Pages: launcher → reception → doctor → admin → patient → display → kiosk → lab
8. Pi deployment: install script, systemd service, hotspot, kiosk autostart, laptop Ollama script
9. End-to-end verification in the browser with the real Qwen 3 model
