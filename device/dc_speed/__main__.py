"""python -m dc_speed

Overspeed warnings (REQ-05): compares the speed from gps/fix with the last
speed-limit sign on road/speed_limit, and says "Slow down. Speed limit 50."
when the driver stays more than 5 km/h over it for 3 seconds. Each warning,
and the time over the limit, goes to the trip log.
"""

import logging
import time

from dc_common import module, phrases, topics

from .checker import OverspeedChecker

log = logging.getLogger("dc_speed")

WARN_MAX_AGE_S = 3.0      # a warning that could not be spoken in time is dropped
RETAINED_LIMIT_MAX_AGE_S = 600  # after a restart, a limit read this recently still counts


def _number(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def make_checker(bus, clock=time.time):
    def warn(limit):
        bus.post(topics.SAY, {"text": phrases.overspeed_phrase(limit), "priority": "overspeed",
                              "max_age": WARN_MAX_AGE_S})

    def event(seconds, continued):
        msg = {"event": "overspeed", "seconds": round(seconds, 1)}
        if continued:
            msg["continued"] = True
        bus.post(topics.TRIP_EVENT, msg)

    def state(over, kmh, limit):
        bus.post(topics.OVERSPEED, {"over": over, "kmh": None if kmh is None else round(kmh),
                                    "limit": None if limit is None else int(limit)}, retain=True)

    checker = OverspeedChecker(warn, event, state)
    ignition = {"on": False}

    def on_ignition(topic, msg):
        on = isinstance(msg, dict) and msg.get("on") is True
        if ignition["on"] and not on:
            checker.trip_ended()
        if on and not ignition["on"]:
            checker.trip_ended()  # a new trip starts with no limit (REQ-19)
        ignition["on"] = on

    def on_limit(topic, msg):
        if not isinstance(msg, dict) or not ignition["on"]:
            return
        ts = _number(msg.get("ts"))
        if ts is not None and clock() - ts > RETAINED_LIMIT_MAX_AGE_S:
            return  # an old sign from an earlier trip, kept by the board
        checker.set_limit(_number(msg.get("kmh")))

    def on_fix(topic, fix):
        if not isinstance(fix, dict) or not ignition["on"]:
            return
        values = [_number(fix.get(k)) for k in ("lat", "lon", "kmh", "acc", "ts")]
        if any(v is None for v in values):
            return
        lat, lon, kmh, acc, ts = values
        if -90 <= lat <= 90 and -180 <= lon <= 180 and acc >= 0:
            checker.fix(lat, lon, kmh, acc, ts)

    # The ignition first, so a retained limit arriving at start-up is judged correctly.
    bus.subscribe(topics.IGNITION, on_ignition)
    bus.subscribe(topics.SPEED_LIMIT, on_limit)
    bus.subscribe(topics.GPS_FIX, on_fix)
    return checker


async def setup(bus, args):
    make_checker(bus)
    return None


if __name__ == "__main__":
    module.main("dc_speed", setup)
