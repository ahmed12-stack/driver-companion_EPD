import asyncio
import json
import os
import stat
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from dc_ble.framing import Reassembler, split  # noqa: E402
from dc_ble.gateway import Gateway  # noqa: E402
from dc_ble.settings import SettingsStore  # noqa: E402
from dc_ble.speaker import Priority, Speaker  # noqa: E402


class FakeSpeaker:
    def __init__(self):
        self.said = []
        self.volume = None

    def say(self, text, priority=Priority.SYSTEM, max_age=None):
        self.said.append((text, priority))

    def set_volume(self, level):
        self.volume = level


@pytest.fixture
def rig(tmp_path):
    sent = []
    speaker = FakeSpeaker()
    settings = SettingsStore(str(tmp_path / "settings.json"))
    gw = Gateway(speaker, settings, send_status=sent.append, device_name="DC-4F2A")
    gw.note_mtu(247)
    return gw, speaker, settings, sent, tmp_path


def send(gw, slot, msg, mtu=247, msg_id=0):
    body = msg if isinstance(msg, bytes) else json.dumps(msg).encode()
    for p in split(body, msg_id, mtu):
        gw.receive(slot, p)


def replies(sent):
    out, r = [], Reassembler()
    for p in sent:
        body = r.feed(p)
        if body is not None:
            out.append(json.loads(body))
    return out


def test_hello(rig):
    gw, _, _, sent, _ = rig
    send(gw, "settings", {"t": "hello", "seq": 9, "proto": 1, "app": "test"})
    assert replies(sent) == [{"t": "hello", "seq": 9, "proto": 1, "fw": "0.1.0",
                              "dev": "DC-4F2A", "unsynced": 0}]


def test_hello_wrong_version(rig):
    gw, _, _, sent, _ = rig
    send(gw, "settings", {"t": "hello", "seq": 1, "proto": 2})
    out = replies(sent)
    assert out[0]["t"] == "err" and out[0]["code"] == "VERSION"
    assert out[1]["t"] == "hello"


def test_notification_spoken(rig):
    gw, speaker, _, _, _ = rig
    send(gw, "notification", {"t": "notif", "app": "WhatsApp", "from": "Rahul", "text": "hi"})
    assert speaker.said == [("WhatsApp message from Rahul", Priority.MESSAGE)]


def test_sms_and_read_text(rig):
    gw, speaker, settings, _, _ = rig
    settings.update({"read_text": True})
    send(gw, "notification", {"t": "notif", "app": "Messages", "kind": "sms",
                              "from": "Bank", "text": "OTP\n1234"})
    assert speaker.said == [("Text message from Bank. OTP 1234", Priority.MESSAGE)]


def test_quiet_mode(rig):
    gw, speaker, settings, _, _ = rig
    settings.update({"quiet": True})
    send(gw, "notification", {"t": "notif", "app": "WhatsApp", "from": "Rahul"})
    send(gw, "notification", {"t": "call", "from": "Mom", "state": "ringing"})
    assert speaker.said == []


def test_call(rig):
    gw, speaker, _, _, _ = rig
    send(gw, "notification", {"t": "call", "from": "Mom", "state": "ringing"})
    send(gw, "notification", {"t": "call", "from": "Mom", "state": "ended"})
    assert speaker.said == [("Incoming call from Mom", Priority.CALL)]


def test_long_message_small_mtu(rig):
    gw, speaker, _, _, _ = rig
    gw.note_mtu(23)
    send(gw, "notification", {"t": "notif", "app": "WhatsApp", "from": "R" * 50}, mtu=23)
    assert speaker.said[0][0] == "WhatsApp message from " + "R" * 50


def test_settings_update_persists(rig):
    gw, speaker, _, sent, tmp_path = rig
    send(gw, "settings", {"t": "settings", "seq": 4, "vol": 2, "apps": ["com.whatsapp"]})
    assert replies(sent) == [{"t": "ack", "seq": 4}]
    assert speaker.volume == 2
    path = tmp_path / "settings.json"
    assert json.loads(path.read_text())["apps"] == ["com.whatsapp"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert SettingsStore(str(path)).values["vol"] == 2
    assert json.loads(gw.settings_snapshot())["vol"] == 2


@pytest.mark.parametrize("bad", [{"vol": 5}, {"vol": True}, {"quiet": "yes"},
                                 {"apps": "x"}, {"colour": "red"}])
def test_settings_rejected(rig, bad):
    gw, _, settings, sent, _ = rig
    send(gw, "settings", {"t": "settings", "seq": 1, **bad})
    assert replies(sent)[0]["code"] == "BAD_FIELD"
    assert settings.values["vol"] == 1


def test_errors(rig):
    gw, _, _, sent, _ = rig
    send(gw, "settings", b"not json")
    send(gw, "settings", {"no_type": 1})
    send(gw, "settings", {"t": "teleport", "seq": 2})
    send(gw, "location", {"t": "notif", "seq": 3, "app": "a", "from": "b"})
    send(gw, "notification", {"t": "notif", "seq": 4, "app": "WhatsApp"})
    send(gw, "update", {"t": "anything"})
    codes = [m["code"] for m in replies(sent)]
    assert codes == ["BAD_JSON", "BAD_JSON", "UNKNOWN_TYPE", "WRONG_SLOT", "BAD_FIELD", "NOT_READY"]


def test_location(rig):
    gw, _, _, sent, _ = rig
    send(gw, "location", {"t": "loc", "lat": 20.5, "lon": 78.9, "kmh": 40, "acc": 5})
    assert gw.last_location["kmh"] == 40
    assert json.loads(gw.status_snapshot())["phone_gps"] is True
    send(gw, "location", {"t": "loc", "lat": 200, "lon": 0, "kmh": 1})
    assert replies(sent)[0]["code"] == "BAD_FIELD"


def test_passkey_only_in_pairing_mode(rig):
    gw, speaker, _, _, _ = rig
    gw.on_passkey(4821)
    assert speaker.said == []
    gw.pairing = True
    gw.on_passkey(4821)
    assert speaker.said[0][0] == "Pairing code: 0, 0, 4, 8, 2, 1. Again: 0, 0, 4, 8, 2, 1."


def test_connection_lost_resets(rig):
    gw, speaker, _, _, _ = rig
    gw.note_mtu(23)
    packets = split(json.dumps({"t": "notif", "app": "A", "from": "B" * 40}).encode(), 0, 23)
    gw.receive("notification", packets[0])
    gw.connection_lost()
    assert gw.mtu == 23
    for p in packets:
        gw.receive("notification", p)
    assert speaker.said[0][0] == "A message from " + "B" * 40


def test_speaker_priority_and_gap():
    spoken = []

    class Recording(Speaker):
        async def speak(self, text):
            spoken.append(text)

    async def run():
        s = Recording(engine="x", message_gap_s=0.05)
        s.say("message", Priority.MESSAGE)
        s.say("slow down", Priority.OVERSPEED)
        s.say("stop sign", Priority.SIGN)
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(run())
    assert spoken == ["slow down", "stop sign", "message"]


def test_speaker_drops_stale():
    spoken = []

    class Recording(Speaker):
        async def speak(self, text):
            spoken.append(text)

    async def run():
        t = [0.0]
        s = Recording(engine="x", clock=lambda: t[0], message_gap_s=0)
        s.say("old", Priority.MESSAGE, max_age=60)
        t[0] = 61
        s.say("new", Priority.MESSAGE, max_age=60)
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(run())
    assert spoken == ["new"]
