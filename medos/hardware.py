"""GPIO: emergency push button (interrupt), red LED and active buzzer.

On a Raspberry Pi this uses gpiozero (lgpio backend on Bookworm). Anywhere else, or when
MEDOS_SIMULATE_GPIO=1, a simulated board keeps the same state so every screen still works.

Wiring (BCM numbering, see Admin → Hardware):
    Button : GPIO17 (pin 11) ── button ── GND (pin 9)          internal pull-up
    LED    : GPIO27 (pin 13) ── 330 Ω ── LED(+) ; LED(−) ── GND (pin 14)
    Buzzer : GPIO22 (pin 15) ── buzzer(+) ; buzzer(−) ── GND (pin 20)
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import config, db


def is_raspberry_pi() -> bool:
    try:
        with open("/proc/device-tree/model") as f:
            return "raspberry pi" in f.read().lower()
    except OSError:
        return False


class Hardware:
    def __init__(self, on_button: Callable[[], None]):
        self.on_button = on_button
        self.pin_button = config.PIN_BUTTON
        self.pin_led = config.PIN_LED
        self.pin_buzzer = config.PIN_BUZZER
        self.mode = "simulated"
        self.error: Optional[str] = None
        self.led_state = "off"          # off | on | blinking
        self.buzzer_state = "off"       # off | on | beeping
        self.interrupts = 0
        self.last_press: Optional[float] = None
        self.presses: List[float] = []
        self._led = self._buzzer = self._button = None
        self._buzzer_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._debounce_until = 0.0
        if not config.SIMULATE_GPIO:
            self._init_gpio()

    def _init_gpio(self) -> None:
        try:
            from gpiozero import LED, Button, Buzzer  # type: ignore
            self._led = LED(self.pin_led)
            self._buzzer = Buzzer(self.pin_buzzer)
            self._button = Button(self.pin_button, pull_up=True, bounce_time=0.15)
            self._button.when_pressed = self._irq
            self.mode = "gpio"
            self._led.blink(on_time=0.1, off_time=0.1, n=3, background=True)   # power-on self test
        except Exception as exc:  # not a Pi, no permission, or pins busy
            self.error = "%s: %s" % (type(exc).__name__, exc) if is_raspberry_pi() else None
            self._led = self._buzzer = self._button = None
            self.mode = "simulated"

    # ---------------------------------------------------------------- input
    def _irq(self) -> None:
        """Edge-triggered callback from the GPIO library's interrupt thread."""
        now = time.time()
        if now < self._debounce_until:          # ignore repeat presses for 3 s
            return
        self._debounce_until = now + 3
        self.interrupts += 1
        self.last_press = now
        self.presses = (self.presses + [now])[-20:]
        # Never do slow work inside the interrupt callback: hand off to a worker thread.
        threading.Thread(target=self.on_button, name="irq-handler", daemon=True).start()

    def simulate_press(self) -> None:
        self._debounce_until = 0
        self._irq()

    # ---------------------------------------------------------------- output
    def alarm_on(self) -> None:
        with self._lock:
            self.led_state = "blinking"
            self.buzzer_state = "beeping"
            if self._led:
                self._led.blink(on_time=0.25, off_time=0.25, background=True)
            if self._buzzer:
                self._buzzer.beep(on_time=0.2, off_time=0.3, background=True)
            if self._buzzer_timer:
                self._buzzer_timer.cancel()
            secs = float(db.get_setting("alarm_buzzer_seconds") or 20)
            self._buzzer_timer = threading.Timer(secs, self._buzzer_quiet)
            self._buzzer_timer.name = "buzzer-timeout"
            self._buzzer_timer.daemon = True
            self._buzzer_timer.start()

    def _buzzer_quiet(self) -> None:
        """Silence the buzzer after a while; the LED keeps blinking until someone acknowledges."""
        with self._lock:
            if self._buzzer:
                self._buzzer.off()
            self.buzzer_state = "off"

    def alarm_off(self) -> None:
        with self._lock:
            if self._buzzer_timer:
                self._buzzer_timer.cancel()
                self._buzzer_timer = None
            if self._led:
                self._led.off()
            if self._buzzer:
                self._buzzer.off()
            self.led_state = "off"
            self.buzzer_state = "off"

    def chime(self) -> None:
        if self.buzzer_state != "off":
            return
        if self._buzzer:
            self._buzzer.beep(on_time=0.06, off_time=0.08, n=2, background=True)

    def test(self, what: str) -> None:
        if what == "led":
            if self._led:
                self._led.blink(on_time=0.2, off_time=0.2, n=5, background=True)
            self._flash_state("led_state", "blinking", 2.0)
        elif what == "buzzer":
            if self._buzzer:
                self._buzzer.beep(on_time=0.1, off_time=0.1, n=3, background=True)
            self._flash_state("buzzer_state", "beeping", 0.6)
        elif what == "button":
            self.simulate_press()

    def _flash_state(self, attr: str, value: str, seconds: float) -> None:
        if getattr(self, attr) != "off":
            return
        setattr(self, attr, value)

        def back():
            if getattr(self, attr) == value:
                setattr(self, attr, "off")
        t = threading.Timer(seconds, back)
        t.daemon = True
        t.start()

    def state(self) -> Dict[str, Any]:
        return {
            "mode": self.mode, "is_pi": is_raspberry_pi(), "error": self.error,
            "pins": {"button": self.pin_button, "led": self.pin_led, "buzzer": self.pin_buzzer},
            "led": self.led_state, "buzzer": self.buzzer_state,
            "interrupts": self.interrupts, "last_press": self.last_press,
            "button_pressed": bool(self._button.is_pressed) if self._button else False,
        }

    def close(self) -> None:
        for dev in (self._led, self._buzzer, self._button):
            try:
                if dev:
                    dev.close()
            except Exception:
                pass
