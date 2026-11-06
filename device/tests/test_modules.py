"""The shared bus, dc_speech and dc_health on their own."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from dc_common import topics  # noqa: E402
from dc_common.bus import MemoryBroker, MemoryBus, matches  # noqa: E402
from dc_common.storage import RecordStore  # noqa: E402
from dc_health.monitor import Monitor  # noqa: E402
from dc_speech.__main__ import make_handlers  # noqa: E402
from dc_speech.speaker import Priority  # noqa: E402


@pytest.mark.parametrize("pattern,topic,ok", [
    ("vehicle/ignition", "vehicle/ignition", True),
    ("module/+/status", "module/dc_trip/status", True),
    ("module/+/status", "module/dc_trip/alive", False),
    ("module/+/status", "module/a/b/status", False),
    ("trip/#", "trip/saved", True),
    ("trip/#", "trip", True),
    ("gps/fix", "gps/fix/extra", False),
])
def test_topic_matching(pattern, topic, ok):
    assert matches(pattern, topic) is ok


def test_memory_bus_retained_and_live():
    async def run():
        broker = MemoryBroker()
        a, b = MemoryBus("a", broker), MemoryBus("b", broker)
        got = []
        await a.publish(topics.IGNITION, {"on": True}, retain=True)
        b.subscribe("vehicle/#", lambda t, d: got.append(d))   # gets the retained one
        await asyncio.sleep(0)
        await a.publish(topics.IGNITION, {"on": False})
        b.subscribe(topics.SAY, lambda t, d: 1 / 0)              # a failing handler
        await a.publish(topics.SAY, {"text": "x"})                # must not break the bus
        await a.publish(topics.IGNITION, {"on": True})
        return got
    assert asyncio.run(run()) == [{"on": True}, {"on": False}, {"on": True}]


def test_record_store(tmp_path):
    s = RecordStore(str(tmp_path / "r.json"), 3)
    for i in range(5):
        s.add({"id": str(i)})
    assert [r["id"] for r in s.items] == ["2", "3", "4"]
    assert s.mark_synced("3") and not s.mark_synced("3") and not s.mark_synced("0")
    assert [r["id"] for r in RecordStore(str(tmp_path / "r.json"), 3).unsynced()] == ["2", "4"]


# ----- dc_speech --------------------------------------------------------

class FakeSpeaker:
    def __init__(self):
        self.said, self.volume = [], None

    def say(self, text, priority=Priority.SYSTEM, max_age=None):
        self.said.append((text, priority, max_age))

    def set_volume(self, level):
        self.volume = level


def test_speech_handlers():
    sp = FakeSpeaker()
    on_say, on_settings = make_handlers(sp)
    on_say(topics.SAY, {"text": "Slow down", "priority": "overspeed"})
    on_say(topics.SAY, {"text": "WhatsApp\x07 message", "priority": "message", "max_age": 60})
    on_say(topics.SAY, {"text": "Hi", "priority": "made-up", "max_age": True})
    on_say(topics.SAY, {"text": "   "})
    on_say(topics.SAY, "not a dict")
    assert sp.said == [("Slow down", Priority.OVERSPEED, None),
                       ("WhatsApp message", Priority.MESSAGE, 60),
                       ("Hi", Priority.SYSTEM, None)]
    on_settings(topics.SETTINGS, {"vol": 2})
    assert sp.volume == 2
    on_settings(topics.SETTINGS, {"vol": True})
    on_settings(topics.SETTINGS, {"vol": 9})
    assert sp.volume == 2


# ----- dc_health --------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_selftest():
    said = []
    m = Monitor(["dc_speech", "dc_trip"], said.append, clock=Clock())
    m.on_status("module/dc_speech/status", "online")
    result = m.selftest({"camera": True, "imu": False, "gnss": None}, ["camera", "imu"])
    assert not result["ok"]
    assert result["spoken"] == "Self test: trip logger not running, motion sensor not found."
    m.on_status("module/dc_trip/status", "online")
    ok = m.selftest({"camera": True}, ["camera"])
    assert ok["ok"] and ok["spoken"] == "Self test passed. System ready."


def test_crash_warned_once_after_grace_and_cleared_on_restart():
    clock, said = Clock(), []
    m = Monitor(["dc_trip"], said.append, clock=clock)
    m.on_status("module/dc_trip/status", "online")
    m.on_status("module/dc_trip/status", "offline")      # the board's last will
    clock.t += 5
    assert m.check() == [] and said == []                # systemd may still restart it
    clock.t += 6
    m.check()
    m.check()
    assert said == ["Warning: the trip logger stopped working."]
    m.on_status("module/dc_trip/status", "online")       # restarted
    m.on_alive("module/dc_trip/alive", {"ts": 1})
    assert m.check() == []
    m.on_status("module/dc_trip/status", "offline")
    clock.t += 11
    m.check()
    assert len(said) == 2                                # a new crash warns again


def test_hung_module_and_deliberate_stop():
    clock, said = Clock(), []
    m = Monitor(["dc_phone", "dc_trip"], said.append, clock=clock)
    m.on_status("module/dc_phone/status", "online")
    m.on_status("module/dc_trip/status", "stopped")      # systemctl stop: no warning
    clock.t += 25                                        # dc_phone sends no heartbeat
    m.check()
    assert said == ["Warning: the phone link is not responding."]
