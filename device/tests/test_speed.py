import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from dc_common import topics  # noqa: E402
from dc_common.bus import MemoryBroker, MemoryBus  # noqa: E402
from dc_speed.__main__ import make_checker  # noqa: E402
from dc_speed.checker import OverspeedChecker  # noqa: E402

KM_PER_DEG = 111.195


class Drive:
    """Feeds 1 fix per second, driving north."""

    def __init__(self, checker, lat=20.0, ts=1000.0):
        self.c, self.lat, self.ts = checker, lat, ts

    def go(self, seconds, kmh, acc=5.0):
        for _ in range(seconds):
            self.lat += kmh / 3600 / KM_PER_DEG
            self.ts += 1
            self.c.fix(self.lat, 85.0, kmh, acc, self.ts)


def checker():
    warned, events, states = [], [], []
    c = OverspeedChecker(warned.append, lambda s, cont: events.append((s, cont)),
                         lambda over, kmh, limit: states.append(over))
    return c, warned, events, states


def test_no_limit_no_warning():
    c, warned, events, _ = checker()
    Drive(c).go(60, 120)
    assert warned == [] and events == []


def test_warns_after_three_seconds_over():
    c, warned, events, states = checker()
    c.set_limit(50)
    d = Drive(c)
    d.go(10, 50)
    d.go(2, 60)
    assert warned == []          # only 1 s over so far (the first over-fix starts the clock)
    d.go(2, 60)
    assert warned == [50]
    assert events == [(3, False)] and states == [True]


def test_small_or_short_excess_is_fine():
    c, warned, _, _ = checker()
    c.set_limit(50)
    d = Drive(c)
    d.go(30, 55)                 # exactly 5 over: allowed
    for _ in range(5):
        d.go(2, 70)              # 2 s bursts, never 3
        d.go(1, 40)
    assert warned == []


def test_repeats_every_30_s_and_counts_once():
    c, warned, events, states = checker()
    c.set_limit(50)
    d = Drive(c)
    d.go(70, 65)                 # over for 70 s
    assert warned == [50, 50, 50]        # at 3 s, 33 s, 63 s
    d.go(5, 45)                  # back under
    assert sum(1 for _, cont in events if not cont) == 1
    assert sum(s for s, _ in events) == pytest.approx(70, abs=1)
    assert states == [True, False]


def test_new_episode_after_slowing_down():
    c, warned, events, _ = checker()
    c.set_limit(40)
    d = Drive(c)
    d.go(10, 55)
    d.go(10, 30)
    d.go(10, 55)
    assert warned == [40, 40]
    assert sum(1 for _, cont in events if not cont) == 2


def test_limit_expires_after_5_km():
    c, warned, _, _ = checker()
    c.set_limit(60)
    d = Drive(c)
    d.go(330, 55)                # 5.04 km under the limit
    d.go(10, 90)
    assert warned == [] and c.limit is None


def test_trip_end_forgets_limit_and_closes_episode():
    c, warned, events, states = checker()
    c.set_limit(30)
    d = Drive(c)
    d.go(20, 50)
    c.trip_ended()
    assert states == [True, False] and c.limit is None
    assert sum(s for s, _ in events) == pytest.approx(20, abs=1)
    d.go(20, 50)
    assert warned == [30]        # no new warnings without a new sign


def test_weak_fixes_ignored():
    c, warned, _, _ = checker()
    c.set_limit(50)
    Drive(c).go(20, 90, acc=60)
    assert warned == []


def test_through_the_board():
    async def go():
        broker = MemoryBroker()
        bus = MemoryBus("dc_speed", broker)
        make_checker(bus, clock=lambda: 5000.0)
        other = MemoryBus("other", broker)
        await other.publish(topics.IGNITION, {"on": True}, retain=True)
        await other.publish(topics.SPEED_LIMIT, {"kmh": 50, "ts": 4990.0}, retain=True)
        lat, ts = 20.0, 1000.0
        for _ in range(5):
            lat += 70 / 3600 / KM_PER_DEG
            ts += 1
            await other.publish(topics.GPS_FIX, {"lat": lat, "lon": 85.0, "kmh": 70, "acc": 5,
                                                 "ts": ts, "source": "phone"})
        await asyncio.sleep(0.05)
        return broker.log
    log = asyncio.run(go())
    said = [d for t, d in log if t == topics.SAY]
    assert said == [{"text": "Slow down. Speed limit 50.", "priority": "overspeed", "max_age": 3.0}]
    assert [d for t, d in log if t == topics.TRIP_EVENT] == [{"event": "overspeed", "seconds": 3.0}]
    assert [d["over"] for t, d in log if t == topics.OVERSPEED] == [True]


def test_old_limit_from_last_trip_ignored():
    async def go():
        broker = MemoryBroker()
        other = MemoryBus("other", broker)
        await other.publish(topics.SPEED_LIMIT, {"kmh": 30, "ts": 100.0}, retain=True)
        await other.publish(topics.IGNITION, {"on": True}, retain=True)
        bus = MemoryBus("dc_speed", broker)
        c = make_checker(bus, clock=lambda: 5000.0)
        await asyncio.sleep(0.05)
        return c
    assert asyncio.run(go()).limit is None


def test_trip_log_adds_time_without_counting_twice(tmp_path):
    from dc_trip.trip import TripLogger
    lg = TripLogger(str(tmp_path / "cur.json"))
    lg.start()
    lg.record("overspeed", 3)
    lg.record("overspeed", 27, counted=False)
    assert lg.current["events"]["overspeed"] == 1 and lg.current["overspeed_s"] == 30
