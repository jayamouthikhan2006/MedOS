# MedOS: Smart Hospital Emergency Scheduler

Priority scheduling for a hospital emergency department, built as an operating-systems project and designed to run on a **Raspberry Pi 4 (4 GB)**. Patients are ordered by how sick they are, not by when they arrived. Long waits slowly gain priority (aging), so nobody is forgotten. A physical emergency button interrupts everything.

Any laptop, tablet or phone on the same network opens MedOS in a browser. Nothing to install on the clients, and no internet needed.

| Screen | Who uses it | What it does |
|---|---|---|
| **Reception** `/reception` | Receptionist | Register by typing or **voice**, live triage preview, Qwen second opinion, live queue with priority bars, emergency hold-button, printable token slip with QR |
| **Doctor** `/doctor` | Doctors | Current patient, next patient, **Finish consultation** (outcome + notes), AI pre-consultation brief, Available / Break / Off duty |
| **Admin** `/admin` | Manager | Doctors available, rooms available, queue length, average waiting time, emergency alerts, charts, room floor plan; manage doctors, rooms and the triage protocol; hardware panel; AI settings, shift report, Q&A; **system monitor**; settings and CSV export |
| **Patient** `/p/<code>` | Patients (phone) | Token number, queue position, estimated wait, **alert when it's almost their turn** |
| **Waiting-room display** `/display` | TV on the Pi's HDMI port | Calls tokens aloud with a chime, shows who is with a doctor and who's next |
| **Kiosk** `/kiosk` | Patients (Pi touchscreen) | Self check-in by speaking, emergency help button |
| **Pharmacy** `/pharmacy` | Pharmacist | Prescriptions from doctors queue up **first come, first served**; start, ready, collected; patients see "ready" on the TV and their phone |
| **Ambulance desk** `/ambulance` | Reception / ambulance staff | Picks the hospital with the **shortest time to treatment** (SJF-style) for emergency and non-emergency cases, and dispatches ambulances emergencies first, then shortest trip |
| **Scheduler Lab** `/lab` | Students / examiners | FCFS vs Priority vs Priority + aging vs Pre-emptive on the same patients, with Gantt charts and metrics |

First PINs: **staff 1111**, **admin 1234**. The admin PIN opens every screen. Change both in *Admin → Settings*.

---

## 1. Try it on your laptop (2 minutes)

Needs Python 3.9 or newer.

```bash
pip install -r requirements.txt
```

```bash
python run.py
```

Open http://localhost:8080. Demo patients load on the first run. On Windows you can also double-click `run_windows.bat`.

On a laptop the GPIO pins are simulated. Use *Admin → Hardware → Simulate button press* to fire the emergency interrupt. Qwen runs from your local Ollama (`http://localhost:11434`, model `qwen3:4b`).

---

## 2. Put it on the Raspberry Pi

1. Flash **Raspberry Pi OS (64-bit, Bookworm)** with Raspberry Pi Imager. In the imager's settings set a username, your Wi-Fi and **enable SSH**.
2. Copy this folder to the Pi. From the laptop, in the folder that contains `Med OS`:
   ```bash
   scp -r "Med OS" pi@raspberrypi.local:~/medos
   ```
   (Replace `pi` with your username. A USB stick works too.)
3. On the Pi (over SSH or a keyboard):
   ```bash
   cd ~/medos
   sudo bash deploy/install.sh --display
   sudo reboot
   ```
   The installer sets up Python, the GPIO libraries, the offline speech model (Vosk, Indian English), HTTPS for browser microphones, a `medos` service that starts on boot on port 80, and the hostname `medos`. `--display` opens the waiting-room display full-screen on the HDMI monitor at every boot. Use `PAGE=kiosk bash deploy/display-autostart.sh` for a touchscreen kiosk instead.

Useful commands on the Pi:

```bash
journalctl -u medos -f        # live log
sudo systemctl restart medos  # restart after changes
```

### Connect a laptop to the Pi, three ways

| Situation | Do this | Open |
|---|---|---|
| Pi and laptop on the same Wi-Fi or router | nothing | http://medos.local |
| No router: the Pi makes its own Wi-Fi | `sudo bash deploy/hotspot.sh`, then join **MedOS-Hospital** (password `medos1234`) | http://10.42.0.1 |
| Just an Ethernet cable between laptop and Pi | `sudo bash deploy/direct-cable.sh`, plug in, wait a minute | http://10.43.0.1 |

The home page shows the Pi's addresses and a QR code, so phones can scan their way in.

---

## 3. Wiring the emergency hardware

BCM numbering. The pins are shown live in *Admin → Hardware*.

| Part | Pi pin | Connection |
|---|---|---|
| Push button | GPIO17 (pin 11) → button → GND (pin 9) | Internal pull-up; a press is a falling-edge **interrupt** |
| Red LED | GPIO27 (pin 13) → 330 Ω resistor → LED long leg; LED short leg → GND (pin 14) | Blinks until someone acknowledges the alert |
| Active buzzer | GPIO22 (pin 15) → buzzer + ; buzzer − → GND (pin 20) | Beeps for 20 s (configurable), then the LED keeps blinking |
| USB microphone | any USB port | Voice registration on the Pi, fully offline |

```
 pin:  1   3   5   7  [9]  [11] [13] [15]  17  19
      3V3 G2  G3  G4  GND  G17  G27  G22  3V3 G10
 pin:  2   4   6   8   10   12  [14]  16   18  [20]
      5V  5V  GND G14 G15  G18  GND  G23  G24  GND
      button = 9 + 11 · LED = 13 + 14 · buzzer = 15 + 20
```

Pressing the button: the interrupt handler hands off to a worker thread, a Critical *Emergency patient* is created at the front of the queue, a free doctor is assigned (or the least urgent consultation is pre-empted), every screen shows the red alarm bar, the display announces it, and the LED and buzzer start. *Acknowledge* on any staff screen silences them.

---

## 4. Using Qwen 3 from your laptop

MedOS works without AI; the rules engine always runs. With Qwen it also:

- gives a **second-opinion triage** for every new patient, reasoning first and then choosing a level. It can only **raise** a priority, never lower it.
- turns a spoken check-in into form fields ("my name is Kavitha, 34, high fever since two days" → name, age, symptoms).
- writes a **pre-consultation brief** for the doctor, a **shift handover report**, and answers questions about today.

To let the Pi use the model on your laptop:

1. On the laptop, run once:
   ```powershell
   powershell -ExecutionPolicy Bypass -File laptop\enable-ollama-lan.ps1
   ```
   It makes Ollama listen on the network, opens port 11434 in the firewall (asks for admin once), and pulls `qwen3:4b`.
2. Connect the laptop to the Pi's network (any of the three ways above).
3. In MedOS open *Admin → AI assistant → Find on network*, then *Use this*. Or type `http://<laptop-ip>:11434`.

No laptop? `curl -fsSL https://ollama.com/install.sh | sh` on the Pi, then `ollama pull qwen3:1.7b`, and set the model to `qwen3:1.7b` at `http://localhost:11434`. It's slower but fully self-contained.

---

## 5. Voice registration

| Where the microphone is | How | Needs |
|---|---|---|
| USB mic on the Pi | *Reception → Speak to register* with **Pi microphone** selected, or the Kiosk | Installed by `install.sh` (offline Vosk) |
| The laptop or phone you're using | *Speak to register* with **This device** selected | Open MedOS over HTTPS: `https://medos.local:8443` (accept the certificate once). Browsers only allow microphones on secure pages. |

While you speak, the words appear live. When you stop, Qwen (or the built-in parser if Qwen is offline) fills in name, age, sex, phone and symptoms for the receptionist to confirm.

---

## 6. How the scheduler works

```
effective priority = level base + (16 − rank) × 10 + modifiers
                   + min(aging rate × minutes waited, aging limit)      ← aging
                   → at least 1450 if the patient was pre-empted         ← resumes next
                   + 5000 for an emergency override                      ← interrupt

level base: Critical 1500 · High 600 · Medium 300 · Low 0
modifiers:  age ≥ 65 or ≤ 5: +40 · pregnant: +60 · pain ≥ 8/10: +40
defaults:   aging 5 points/minute, limit 450, re-ordered every 15 s
```

- **Rank** comes from the priority table in the proposal (1 = severe abdominal pain … 15 = routine check-up). Ten red flags sit above it at rank 0 and are always Critical: chest pain, breathing difficulty, stroke signs, unconscious, severe bleeding, seizure, anaphylaxis, major trauma, poisoning or snake bite, severe burns. The table is editable in *Admin → Triage protocol*.
- **Vital signs** can raise the level on their own: SpO₂ < 90 %, pulse ≥ 130 or ≤ 40, or systolic BP < 90 is Critical.
- **Aging** lets a Low case waiting about 70 minutes pass a newly arrived Medium case. Critical can never be overtaken: the best High score plus every modifier plus the full aging limit is still below the lowest Critical score.
- **Dispatch**: whenever a doctor finishes or becomes available, the head of the ready queue goes to the doctor who has been idle longest.
- **Waiting-time estimate**: a small multi-server simulation of the queue against each doctor's expected finish time, using the average consultation length per level, learned from today's finished consultations.

### Operating-system concepts, and where to see them

| Concept | In MedOS | Watch it live |
|---|---|---|
| First come, first served (FCFS) | `pharmacy.py`: prescriptions join one FIFO queue; "Start next order" can only take the head | Pharmacy page; `PHARMACY` log lines |
| Shortest job first (SJF) | `ambulance.py`: each case goes to the hospital with the shortest drive + wait; ambulances take emergencies first, then the shortest trip, with aging so long trips can't starve | Ambulance desk; `AMBULANCE` log lines |
| Dynamic priority scheduling | `scheduler.py` `ready_queue()`: binary heap rebuilt from live scores | Reception queue order, priority bars |
| Aging (starvation prevention) | `aging-daemon` thread re-scores every tick and logs overtakes | Hatched part of the priority bar; `AGING` lines in the kernel log |
| Pre-emption | `_preempt_for()`: the least urgent consultation yields to an emergency | Doctor screen notice; `PREEMPT` log lines; Scheduler Lab striped blocks |
| Interrupt handling | GPIO17 falling-edge callback → deferred to an `irq-handler` thread | `INTERRUPT` log lines; interrupt counter in *Hardware* |
| Process synchronisation | One instrumented re-entrant lock guards every queue change; a mutex guards the microphone | *System monitor*: acquisitions, contention, max wait. Test: `test_concurrent_doctors_never_share_a_patient` |
| Producer / consumer | Registrations enqueue work, the `ai-triage-worker` thread consumes it | *System monitor*: AI queue length |
| Inter-process communication | Server-Sent Events push every change to every screen; the ECG line in the top bar flatlines if the link drops | *System monitor*: live screens subscribed |
| Memory management | Speech model loads lazily; bounded event ring buffer; SQLite WAL | *System monitor*: process RSS, database size |
| Process priority | The service runs with `Nice=-5` | `ps -o ni -p $(pgrep -f run.py)` on the Pi |

---

## 7. A five-minute demo

1. **Home** (`/`): the live queue in big numbers. Scan the QR code with a phone.
2. **Reception**: type *"sudden chest pain and sweating"* and watch it jump to Critical before you press anything. Register it; show the token slip and QR.
3. **Patient page** on the phone: position, estimated wait, *Alert me*.
4. **Doctor**: pick a doctor, *Generate with Qwen* for the brief, *Finish consultation*; the next most urgent patient arrives automatically.
5. Press the **hardware button** (or *Admin → Hardware → Simulate button press*): red bar everywhere, LED and buzzer on, a consultation is pre-empted, the TV display announces it.
6. **Admin → System monitor**: the kernel log reads ARRIVE → INTERRUPT → PREEMPT → DISPATCH, plus lock and thread statistics.
7. **Scheduler Lab**: FCFS vs priority vs aging vs pre-emption on the same patients. Point at *Critical: time to a doctor* and *Longest wait, low priority*.

---

## 8. Project layout

```
run.py                  start the server (python run.py --port 8080 [--https])
medos/
  scheduler.py          dynamic priority scheduler, aging daemon, dispatch, pre-emption, ETA
  triage.py             rules engine + the priority table
  llm.py                Qwen 3 via Ollama, AI worker thread, network discovery
  voice.py              Vosk speech recognition, USB mic, spoken-form parser
  hardware.py           GPIO button interrupt, LED, buzzer (simulated off-Pi)
  api.py                REST API + Server-Sent Events
  db.py · events.py     SQLite storage, event bus and kernel log
  sysmon.py             CPU, memory, temperature, threads
  demo.py               demo patients
  templates/ static/    the screens (no build step, works offline)
deploy/                 install.sh, hotspot.sh, direct-cable.sh, display-autostart.sh
laptop/                 enable-ollama-lan.ps1 / disable-ollama-lan.ps1
tests/                  pytest suite for triage and scheduling
```

Data lives in `data/medos.db` (SQLite). *Admin → Settings → Data* exports CSV, reloads the demo, or clears patients.

Run the tests:

```bash
python -m pytest -q
```

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `medos.local` doesn't open | Use the IP shown by `hostname -I` on the Pi, or on the Home page. Some Android phones don't support `.local`. |
| Hardware tab says *Simulated* on the Pi | Check `journalctl -u medos` for the GPIO error; make sure `python3-gpiozero` and `python3-lgpio` are installed and reboot after `install.sh`. |
| Voice button is disabled | No USB mic or speech model on the Pi, and the page isn't on HTTPS. Plug in the mic and rerun `install.sh`, or use `https://medos.local:8443`. |
| AI shows *Unreachable* | Run `laptop\enable-ollama-lan.ps1`, make sure both devices are on the same network, then *Find on network*. |
| The TV display is silent | Click once on the display page, or use `--display` in `install.sh`, which lets Chromium play sound without a click. You hear the chime but no spoken token? Run `sudo apt install speech-dispatcher espeak-ng` and reboot. |
| Times look wrong | Set the Pi's time zone: `sudo raspi-config` → Localisation Options → Timezone (Asia/Kolkata). |

MedOS is a decision-support prototype for teaching and demonstration. It isn't a certified medical device.
