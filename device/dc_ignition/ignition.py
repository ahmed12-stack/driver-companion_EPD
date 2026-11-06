"""Ignition sense: tells the trip logger when the car is switched on and off.

Wiring: the input pin reads LOW when the ignition is on.
  Bench:  a toggle switch between GPIO27 (pin 13) and GND (pin 14).
  Car:    a PC817 optocoupler driven from the car's ignition (ACC) line, its
          output transistor between GPIO27 and GND.
The Pi's internal pull-up holds the pin HIGH (off) otherwise.
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
