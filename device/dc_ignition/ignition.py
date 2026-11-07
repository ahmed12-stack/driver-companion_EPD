"""Ignition sense: tells the trip logger when the car is switched on and off.

Wiring: the input pin reads LOW when the ignition is on.
  Bench:  a toggle switch between GPIO27 (pin 13) and GND (pin 14).
  Car:    a PC817 optocoupler driven from the car's ignition (ACC) line, its
          output transistor between GPIO27 and GND.
The Pi's internal pull-up holds the pin HIGH (off) otherwise.

TripButton is for a push button that springs back (DC-1 prototype): each
press flips the trip between running and stopped.
"""

import asyncio
import logging
import time

log = logging.getLogger(__name__)

ON_DEBOUNCE_S = 1.0   # must read "on" this long before a trip starts
OFF_DEBOUNCE_S = 5.0  # longer, because some cars drop ACC while cranking
POLL_S = 0.2


class Ignition:
    def __init__(self, pin: int, on_change, clock=time.monotonic):
        self.pin = pin
        self.on_change = on_change
        self.state = None
        self._clock = clock
        self._device = None

    def open(self) -> bool:
        try:
            from gpiozero import DigitalInputDevice
            self._device = DigitalInputDevice(self.pin, pull_up=True)
            return True
        except Exception as e:  # gpiozero missing, pin busy, not a Pi
            log.warning("ignition input on GPIO%d unavailable (%s); treating ignition as on",
                        self.pin, e)
            return False

    async def run(self):
        if self._device is None and not self.open():
            self.state = True
            self.on_change(True)
            return
        candidate, since = None, 0.0
        while True:
            raw = bool(self._device.is_active)  # active = pulled low = ignition on
            now = self._clock()
            if raw == self.state:
                candidate = None
            elif raw != candidate:
                candidate, since = raw, now
            elif now - since >= (ON_DEBOUNCE_S if raw else OFF_DEBOUNCE_S):
                self.state, candidate = raw, None
                log.info("ignition %s", "on" if raw else "off")
                self.on_change(raw)
            await asyncio.sleep(POLL_S)

    def close(self):
        if self._device is not None:
            self._device.close()
            self._device = None


PRESS_S = 0.08        # held at least this long to count (ignores contact bounce)
REPEAT_GUARD_S = 1.5  # a second press sooner than this is ignored (double tap)


class TripButton:
    """Momentary button between the pin and GND: press = start trip, press again = end it."""

    def __init__(self, pin: int, on_change, clock=time.monotonic):
        self.pin = pin
        self.on_change = on_change
        self.state = False
        self._clock = clock
        self._device = None
        self._down_since = None
        self._handled = False
        self._last_toggle = -1e9

    def open(self) -> bool:
        try:
            from gpiozero import DigitalInputDevice
            self._device = DigitalInputDevice(self.pin, pull_up=True)
            return True
        except Exception as e:
            log.warning("trip button on GPIO%d unavailable (%s); trips will not start", self.pin, e)
            return False

    def step(self, pressed: bool) -> bool:
        """Feed one reading; returns True when this reading toggled the trip."""
        now = self._clock()
        if not pressed:
            self._down_since, self._handled = None, False
            return False
        if self._down_since is None:
            self._down_since = now
        if (not self._handled and now - self._down_since >= PRESS_S
                and now - self._last_toggle >= REPEAT_GUARD_S):
            self._handled = True
            self._last_toggle = now
            self.state = not self.state
            log.info("trip button: trip %s", "started" if self.state else "ended")
            self.on_change(self.state)
            return True
        return False

    async def run(self):
        self.on_change(False)           # every power-up starts with no trip running
        if self._device is None and not self.open():
            return
        while True:
            self.step(bool(self._device.is_active))
            await asyncio.sleep(0.02)

    def close(self):
        if self._device is not None:
            self._device.close()
            self._device = None
