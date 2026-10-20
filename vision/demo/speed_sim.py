"""Simulated speed logic for the laptop demo (stands in for the GPS module).

Rules come straight from the requirements:
  REQ-05  warn when speed > limit + 5 km/h for at least 3 s;
          repeat the warning at most once every 30 s while still over.
  REQ-19  the active limit expires after 5 km (or when the trip ends).
A speed-limit sign seen by the camera sets the active limit.
On the real device the speed comes from the NEO-6M GPS instead of a slider.
"""


class SpeedMonitor:
    def __init__(self, margin_kmh=5.0, hold_s=3.0, repeat_s=30.0, expire_km=5.0):
        self.margin, self.hold, self.repeat, self.expire = margin_kmh, hold_s, repeat_s, expire_km
        self.limit = None            # active limit in km/h, or None
        self.speed = 0.0
        self.km_since_limit = 0.0
        self.trip_km = 0.0
        self.over_since = None       # when we first went over the margin
        self.last_warn = None
        self.warnings = 0
        self._t = None

    def set_limit(self, kmh, t):
        self.limit = kmh
        self.km_since_limit = 0.0
        self.over_since = None
        self.last_warn = None        # a new limit may be warned about straight away

    def update(self, speed_kmh, t):
        """Call every frame. Returns a list of events: ("warn", text) or ("expired", text)."""
        events = []
        if self._t is not None:
            km = speed_kmh * (t - self._t) / 3600.0      # distance = speed x time
            self.km_since_limit += km
            self.trip_km += km
        self._t, self.speed = t, speed_kmh

        if self.limit is not None and self.km_since_limit >= self.expire:
            events.append(("expired", f"Speed limit {self.limit} no longer applies"))
            self.limit, self.over_since = None, None

        if self.limit is not None and speed_kmh > self.limit + self.margin:
            if self.over_since is None:
                self.over_since = t
            held = t - self.over_since >= self.hold
            can_repeat = self.last_warn is None or t - self.last_warn >= self.repeat
            if held and can_repeat:
                self.last_warn = t
                self.warnings += 1
                events.append(("warn", f"Slow down. Speed limit {self.limit}"))
        else:
            self.over_since = None
        return events

    @property
    def state(self):
        """'none' (no limit), 'ok', 'over' (over margin, < 3 s), 'warning' (warned)."""
        if self.limit is None:
            return "none"
        if self.speed <= self.limit + self.margin:
            return "ok"
        return "warning" if self.last_warn is not None else "over"
