"""dc_phone: the Bluetooth protocol, now posting to the message board."""

import asyncio
import json
import os
import stat
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from dc_common import FIRMWARE_VERSION, topics  # noqa: E402
from dc_common.bus import MemoryBroker, MemoryBus  # noqa: E402
from dc_phone.__main__ import make_gateway  # noqa: E402
from dc_phone.framing import Reassembler, split  # noqa: E402
from dc_phone.settings import SettingsStore  # noqa: E402


class Board:
    """Captures what the gateway posts."""

    def __init__(self):
        self.posts = []

    def post(self, topic, data, retain=False):
        self.posts.append((topic, data, retain))

    def said(self):
        return [(d["text"], d["priority"]) for t, d, _ in self.posts if t == topics.SAY]

    def on(self, topic):
        return [d for t, d, _ in self.posts if t == topic]


@pytest.fixture
def rig(tmp_path):
    from dc_phone.gateway import Gateway
    sent = []
    board = Board()
    settings = SettingsStore(str(tmp_path / "settings.json"))
    gw = Gateway(board.post, settings, send_status=sent.append, device_name="DC-4F2A")
    gw.note_mtu(247)
    return gw, board, settings, sent, tmp_path


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
    assert replies(sent) == [{"t": "hello", "seq": 9, "proto": 1, "fw": FIRMWARE_VERSION,
                              "dev": "DC-4F2A", "unsynced": 0}]


def test_hello_wrong_version(rig):
    gw, _, _, sent, _ = rig
    send(gw, "settings", {"t": "hello", "seq": 1, "proto": 2})
    out = replies(sent)
    assert out[0]["code"] == "VERSION" and out[1]["t"] == "hello"


def test_notification_goes_to_speech(rig):
    gw, board, _, _, _ = rig
    send(gw, "notification", {"t": "notif", "app": "WhatsApp", "from": "Rahul", "text": "hi"})
    assert board.said() == [("WhatsApp message from Rahul", "message")]
    assert board.on(topics.SAY)[0]["max_age"] == 60


def test_sms_and_read_text(rig):
    gw, board, settings, _, _ = rig
    settings.update({"read_text": True})
    send(gw, "notification", {"t": "notif", "app": "Messages", "kind": "sms",
                              "from": "Bank", "text": "OTP\n1234"})
    assert board.said() == [("Text message from Bank. OTP 1234", "message")]


def test_quiet_mode(rig):
    gw, board, settings, _, _ = rig
    settings.update({"quiet": True})
    send(gw, "notification", {"t": "notif", "app": "WhatsApp", "from": "Rahul"})
    send(gw, "notification", {"t": "call", "from": "Mom", "state": "ringing"})
    assert board.said() == []


def test_call(rig):
    gw, board, _, _, _ = rig
    send(gw, "notification", {"t": "call", "from": "Mom", "state": "ringing"})
    send(gw, "notification", {"t": "call", "from": "Mom", "state": "ended"})
    assert board.said() == [("Incoming call from Mom", "call")]


def test_long_message_small_mtu(rig):
    gw, board, _, _, _ = rig
    gw.note_mtu(23)
    send(gw, "notification", {"t": "notif", "app": "WhatsApp", "from": "R" * 50}, mtu=23)
    assert board.said()[0][0] == "WhatsApp message from " + "R" * 50


def test_settings_saved_and_published(rig):
    gw, board, _, sent, tmp_path = rig
    send(gw, "settings", {"t": "settings", "seq": 4, "vol": 2, "apps": ["com.whatsapp"]})
    assert replies(sent) == [{"t": "ack", "seq": 4}]
    path = tmp_path / "settings.json"
    assert json.loads(path.read_text())["apps"] == ["com.whatsapp"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    published = [(d, r) for t, d, r in board.posts if t == topics.SETTINGS]
    assert published[-1][0]["vol"] == 2 and published[-1][1] is True   # retained


@pytest.mark.parametrize("bad", [{"vol": 5}, {"vol": True}, {"quiet": "yes"},
                                 {"apps": "x"}, {"colour": "red"}])
def test_settings_rejected(rig, bad):
    gw, board, settings, sent, _ = rig
    send(gw, "settings", {"t": "settings", "seq": 1, **bad})
    assert replies(sent)[0]["code"] == "BAD_FIELD"
    assert settings.values["vol"] == 1 and board.on(topics.SETTINGS) == []


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


def test_phone_gps_published(rig):
    gw, board, _, sent, _ = rig
    send(gw, "location", {"t": "loc", "lat": 20.5, "lon": 78.9, "kmh": 40, "acc": 5,
                          "ts": 1_793_000_000.5})
    assert board.on(topics.GPS_FIX) == [{"lat": 20.5, "lon": 78.9, "kmh": 40.0, "acc": 5.0,
                                         "ts": 1_793_000_000.5, "source": "phone"}]
    assert json.loads(gw.status_snapshot())["phone_gps"] is True
    send(gw, "location", {"t": "loc", "lat": 200, "lon": 0, "kmh": 1})
    assert replies(sent)[0]["code"] == "BAD_FIELD"
    assert len(board.on(topics.GPS_FIX)) == 1


def test_passkey_only_in_pairing_mode(rig):
    gw, board, _, _, _ = rig
    gw.on_passkey(4821)
    assert board.said() == []
    gw.pairing = True
    gw.on_passkey(4821)
    assert board.said()[0] == ("Pairing code: 0, 0, 4, 8, 2, 1. Again: 0, 0, 4, 8, 2, 1.", "system")


def test_connection_lost_resets(rig):
    gw, board, _, _, _ = rig
    gw.note_mtu(23)
    packets = split(json.dumps({"t": "notif", "app": "A", "from": "B" * 40}).encode(), 0, 23)
    gw.receive("notification", packets[0])
    gw.connection_lost()
    for p in packets:
        gw.receive("notification", p)
    assert board.said()[0][0] == "A message from " + "B" * 40


# ----- trips through the board ------------------------------------------

TRIP = {"id": "1793000000", "start": 1793000000, "km": 6.3, "summary": "Trip complete."}


def wired(tmp_path):
    """The real module wiring (make_gateway) on an in-memory board."""
    broker = MemoryBroker()
    phone_bus = MemoryBus("dc_phone", broker)
    trip_bus = MemoryBus("dc_trip", broker)
    status, trip_out = [], []
    gw = make_gateway(phone_bus, str(tmp_path), status.append, trip_out.append)
    gw.note_mtu(247)
    return broker, trip_bus, gw, status, trip_out


def test_trip_saved_goes_to_connected_phone(tmp_path):
    async def run():
        broker, trip_bus, gw, status, trip_out = wired(tmp_path)
        send(gw, "settings", {"t": "hello", "seq": 1, "proto": 1})   # phone connected
        await trip_bus.publish(topics.TRIP_SAVED, TRIP)
        trips = replies(trip_out)
        assert trips == [{"t": "trip", **TRIP}]
        assert gw.trips.unsynced()                                   # until the phone says so
        send(gw, "settings", {"t": "trip_ack", "seq": 2, "id": TRIP["id"]})
        assert gw.trips.unsynced() == []
    asyncio.run(run())


def test_trip_waits_for_phone_and_is_resent_until_acked(tmp_path):
    async def run():
        broker, trip_bus, gw, status, trip_out = wired(tmp_path)
        await trip_bus.publish(topics.TRIP_SAVED, TRIP)
        assert trip_out == []                                        # nobody connected
        send(gw, "settings", {"t": "hello", "seq": 1, "proto": 1})
        assert replies(status)[0]["unsynced"] == 1
        assert len(replies(trip_out)) == 1
        gw.connection_lost()
        trip_out.clear()
        send(gw, "settings", {"t": "hello", "seq": 2, "proto": 1})
        assert len(replies(trip_out)) == 1                           # no ack yet: again
    asyncio.run(run())


def test_settings_published_at_start(tmp_path):
    async def run():
        broker, *_ = wired(tmp_path)
        await asyncio.sleep(0.01)
        assert broker.retained[topics.SETTINGS]
    asyncio.run(run())
