"""Overspeed check (REQ-05, REQ-19), separate from the board so it can be tested.

Speed: the GPS fixes (phone GPS now; a GPS module later, same topic).
Limit: the last speed-limit sign the camera read.

- Over the limit by more than OVER_BY_KMH for OVER_FOR_S seconds: warn.
- Still over: warn again at most every REPEAT_S seconds.
- Back under for UNDER_FOR_S seconds: the episode ends.
- Each episode counts as one overspeed warning for the trip, with the time spent over it.
- The limit is forgotten after LIMIT_KM kilometres or when the trip ends (REQ-19).
"""

import logging
import math

log = logging.getLogger(__name__)

OVER_BY_KMH = 5.0
OVER_FOR_S = 3.0
REPEAT_S = 30.0
UNDER_FOR_S = 2.0
LIMIT_KM = 5.0
MAX_ACCURACY_M = 30.0   # weaker fixes are ignored (same rule as the trip log)
STALE_S = 5.0           # no usable fix for this long: the episode ends
MAX_JUMP_KMH = 200.0


def _km(a, b) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0088 * math.asin(math.sqrt(h))


class OverspeedChecker:
    def __init__(self, warn, event, state=None):
        self.warn = warn        # warn(limit_kmh): speak "Slow down. Speed limit 50."
        self.event = event      # event(seconds, continued): trip/event overspeed
        self.state = state or (lambda over, kmh, limit: None)  # road/overspeed
        self.limit = None       # km/h, or None
        self.km_since_limit = 0.0
        self._last = None       # (lat, lon, ts) of the last usable fix
        self._over_since = None
        self._under_since = None
        self._episode = False
        self._last_warn = None
        self._reported_s = 0.0  # seconds of this episode already sent to the trip
        self.warnings = 0

    # ----- inputs ---------------------------------------------------------------
    def set_limit(self, kmh):
        if not isinstance(kmh, (int, float)) or isinstance(kmh, bool) or not 5 <= kmh <= 150:
            return
        if kmh != self.limit:
            log.info("speed limit now %d km/h", kmh)
        self.limit = float(kmh)
        self.km_since_limit = 0.0

    def trip_ended(self):
        """Ignition off: close any episode and forget the limit (REQ-19)."""
        self._finish(self._last[2] if self._last else None)
        self.limit = None
        self._last = None
        self._over_since = None

    def fix(self, lat, lon, kmh, acc, ts):
        if acc > MAX_ACCURACY_M or not 0 <= kmh <= MAX_JUMP_KMH:
            return
        if self._last is not None:
            dt = ts - self._last[2]
            if dt <= 0:
                return  # duplicate or out of order
            if dt > STALE_S:
                self._finish(self._last[2])  # no speed for a while: close the episode
            d = _km(self._last[:2], (lat, lon))
            if d / (dt / 3600) <= MAX_JUMP_KMH:
                self.km_since_limit += d
        self._last = (lat, lon, ts)

        if self.limit is not None and self.km_since_limit > LIMIT_KM:
            log.info("speed limit %d expired after %.0f km", self.limit, LIMIT_KM)
            self._finish(ts)
            self.limit = None
        if self.limit is None:
            self._over_since = None
            return

        if kmh > self.limit + OVER_BY_KMH:
            self._under_since = None
            if self._over_since is None:
                self._over_since = ts
            over_for = ts - self._over_since
            if not self._episode and over_for >= OVER_FOR_S:
                self._episode = True
                self._reported_s = over_for
                self.warnings += 1
                self.event(over_for, False)  # one more overspeed warning for the trip
                self.state(True, kmh, self.limit)
                self._say(ts)
            elif self._episode and ts - self._last_warn >= REPEAT_S:
                self._report_time(ts)
                self._say(ts)
        else:
            if self._under_since is None:
                self._under_since = ts
            if not self._episode:
                self._over_since = None
            elif ts - self._under_since >= UNDER_FOR_S:
                self._finish(self._under_since)

    # ----- helpers --------------------------------------------------------------
    def _say(self, ts):
        self._last_warn = ts
        self.warn(int(self.limit))

    def _report_time(self, until):
        """Send the time over the limit not yet sent (it adds to the same warning)."""
        total = until - self._over_since
        extra = total - self._reported_s
        if extra > 0:
            self.event(extra, True)
            self._reported_s = total

    def _finish(self, until):
        """Close the episode, sending its last stretch of time over the limit."""
        if self._episode and until is not None and self._over_since is not None:
            self._report_time(until)
        self._end_episode()

    def _end_episode(self):
        if self._episode:
            self._episode = False
            self.state(False, None, self.limit)
        self._over_since = None
        self._under_since = None
        self._last_warn = None
        self._reported_s = 0.0
