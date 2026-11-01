"""Trip logging: one trip per ignition cycle, saved so a power cut loses little.

A trip starts when the ignition comes on and ends when it goes off, so stops
in traffic never end it. Times come from the GPS fixes themselves (the phone's
clock), because the Pi has no battery-backed clock and its time can be wrong
after a power-up.
"""

import logging
import math
import time

from .storage import atomic_write_json, read_json, remove

log = logging.getLogger(__name__)

MOVING_KMH = 3.0           # slower than this counts as standing still
STOP_MIN_S = 20            # a standstill at least this long counts as a stop
MAX_ACCURACY_M = 30.0      # fixes less accurate than this are ignored
MAX_PLAUSIBLE_KMH = 200.0  # jumps faster than this are GPS glitches
MAX_GAP_S = 30             # a longer gap between fixes is not counted as driving
RESUME_GAP_S = 600         # resuming after a restart: longer than this is a new trip
MIN_TRIP_KM = 0.1          # shorter trips (just started the engine) are dropped
CHECKPOINT_EVERY_S = 30    # REQ-23: lose at most 60 s of trip data
MAX_TRIPS = 20             # REQ-11: keep the last 20 trips

EVENTS = ("overspeed", "harsh_brake", "harsh_accel", "sign")


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _new_state() -> dict:
    return {
        "first_ts": None, "last_ts": None, "last": None,
        "km": 0.0, "moving_s": 0.0, "max_kmh": 0.0,
        "stops": 0, "stopped_since": None, "stop_counted": False,
        "events": {e: 0 for e in EVENTS}, "overspeed_s": 0.0,
    }


def close_trip(state: dict, ended: str):
    """Turn a running trip into its final record, or None if too short to keep."""
    if state is None or state.get("first_ts") is None:
        return None
    km = state["km"]
    if km < MIN_TRIP_KM:
        return None
    stops = state["stops"]
    if state.get("stop_counted"):
        stops -= 1  # the final standstill is parking, not a stop along the way
    moving_s = state["moving_s"]
    start, end = int(state["first_ts"]), int(state["last_ts"])
    ev = state["events"]
    return {
        "id": str(start),
        "start": start,
        "end": end,
        "km": round(km, 1),
        "min": max(1, round((end - start) / 60)),
        "moving_min": round(moving_s / 60),
        "max_kmh": round(state["max_kmh"]),
        "avg_kmh": round(km / (moving_s / 3600)) if moving_s >= 60 else 0,
        "stops": max(0, stops),
        "overspeed": ev.get("overspeed", 0),
        "overspeed_s": round(state.get("overspeed_s", 0)),
        "harsh_brake": ev.get("harsh_brake", 0),
        "harsh_accel": ev.get("harsh_accel", 0),
        "signs": ev.get("sign", 0),
        "ended": ended,
    }


class TripLogger:
    """Keeps the running trip. Not thread-safe: call it from the event loop."""

    def __init__(self, checkpoint_path: str, clock=time.monotonic):
        self._path = checkpoint_path
        self._clock = clock
        self._last_save = float("-inf")
        self.current = None
        # A trip saved before a restart or power cut, waiting to be resumed or closed.
        self.pending = read_json(checkpoint_path, None)
        if self.pending is not None and not isinstance(self.pending, dict):
            self.pending = None

    @property
    def active(self) -> bool:
        return self.current is not None

    def start(self):
        """Ignition on. Resumes a trip interrupted by a restart, if there is one."""
        if self.current is not None:
            return
        if self.pending is not None:
            self.current, self.pending = self.pending, None
            log.info("trip resumed after restart")
        else:
            self.current = _new_state()
            log.info("trip started")
        self.checkpoint(force=True)

    def finish(self, ended: str = "ignition"):
        """Ignition off. Returns the finished trip, or None if too short."""
        trip = close_trip(self.current, ended)
        self.current = None
        remove(self._path)
        return trip

    def close_pending(self):
        """A trip saved before a power cut that will not be resumed."""
        trip = close_trip(self.pending, "power_loss")
        self.pending = None
        if self.current is None:
            remove(self._path)
        return trip

    def add_fix(self, lat, lon, kmh, acc, ts):
        """Add one GPS fix. Returns a trip that had to be closed first (a long
        gap after a restart means the old trip really ended), else None."""
        s = self.current
        if s is None or acc > MAX_ACCURACY_M or not (0 <= kmh <= MAX_PLAUSIBLE_KMH):
            return None
        closed = None
        if s["last_ts"] is not None and ts - s["last_ts"] > RESUME_GAP_S:
            closed = close_trip(s, "power_loss")
            s = self.current = _new_state()
        prev = s["last"]
        if prev is not None:
            dt = ts - prev["ts"]
            if dt <= 0:
                return closed  # duplicate or out of order
            moving = kmh >= MOVING_KMH or prev["kmh"] >= MOVING_KMH
            d = haversine_km(prev["lat"], prev["lon"], lat, lon)
            if dt <= MAX_GAP_S and moving and d / (dt / 3600) <= MAX_PLAUSIBLE_KMH:
                s["km"] += d
                s["moving_s"] += dt
        if s["first_ts"] is None:
            s["first_ts"] = ts
        s["max_kmh"] = max(s["max_kmh"], kmh)
        if kmh < MOVING_KMH:
            if s["stopped_since"] is None:
                s["stopped_since"] = ts
            elif (not s["stop_counted"] and s["km"] > 0
                    and ts - s["stopped_since"] >= STOP_MIN_S):
                s["stops"] += 1
                s["stop_counted"] = True
        else:
            s["stopped_since"] = None
            s["stop_counted"] = False
        s["last"] = {"lat": lat, "lon": lon, "ts": ts, "kmh": kmh}
        s["last_ts"] = ts
        self.checkpoint()
        return closed

    def record(self, event: str, seconds: float = 0.0):
        """For other modules: overspeed, harsh_brake, harsh_accel, sign."""
        if self.current is None or event not in EVENTS:
            return
        self.current["events"][event] += 1
        if event == "overspeed":
            self.current["overspeed_s"] += seconds

    def checkpoint(self, force: bool = False):
        if self.current is None:
            return
        now = self._clock()
        if force or now - self._last_save >= CHECKPOINT_EVERY_S:
            atomic_write_json(self._path, self.current)
            self._last_save = now


class TripStore:
    """The last 20 finished trips, each marked synced once the phone has it."""

    def __init__(self, path: str):
        self.path = path
        data = read_json(path, [])
        self.trips = [t for t in data if isinstance(t, dict) and "id" in t] \
            if isinstance(data, list) else []

    def add(self, trip: dict):
        trip = dict(trip, synced=False)
        self.trips = [t for t in self.trips if t["id"] != trip["id"]] + [trip]
        self.trips = self.trips[-MAX_TRIPS:]
        self._save()

    def unsynced(self) -> list:
        return [t for t in self.trips if not t.get("synced")]

    def mark_synced(self, trip_id: str) -> bool:
        for t in self.trips:
            if t["id"] == trip_id and not t.get("synced"):
                t["synced"] = True
                self._save()
                return True
        return False

    def _save(self):
        atomic_write_json(self.path, self.trips)
