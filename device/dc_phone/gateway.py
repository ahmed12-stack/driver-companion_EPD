"""Protocol logic: turns messages from the phone into messages on the board.

Nothing here touches Bluetooth directly, so it can be tested on a laptop.
The BlueZ layer (bluez.py) feeds raw packets in through receive() and sends
the packets this class produces as Status and Trip Out notifications.

What it puts on the message board (through `post`):
  alert/say         notifications, SMS, calls, pairing messages
  gps/fix           the phone's GPS fixes
  settings/current  the settings, whenever the app changes them
"""

import json
import logging
import time

from dc_common import FIRMWARE_VERSION, topics
from dc_common.text import clean_text

from . import PROTOCOL_VERSION
from .framing import MIN_MTU, FrameError, MessageIds, Reassembler, split
from .settings import SettingsError

log = logging.getLogger(__name__)

MESSAGE_MAX_AGE_S = 60   # messages not spoken within this are dropped
CALL_MAX_AGE_S = 20      # a call alert this late is useless
PHONE_GPS_FRESH_S = 3.0  # REQ-33 switch-over window

SLOTS = ("notification", "location", "settings", "update")


class Gateway:
    def __init__(self, post, settings, send_status, device_name="DC-0000",
                 clock=time.monotonic, send_trip=None, trips=None):
        self.post = post            # post(topic, data, retain=False): onto the message board
        self.settings = settings
        self.device_name = device_name
        self.pairing = False
        self.last_location = None
        self.mtu = MIN_MTU
        self.trips = trips          # outbox of trips the phone has not confirmed, or None
        self.session = False        # True after a hello on this connection
        self._send_status = send_status
        self._send_trip = send_trip
        self._gps_logged = False
        self._clock = clock
        self._ids = MessageIds()
        self._reassemblers = {
            slot: Reassembler(on_error=self._frame_error, clock=clock) for slot in SLOTS
        }

    # ----- connection housekeeping -------------------------------------

    def note_mtu(self, mtu):
        if isinstance(mtu, int) and mtu >= MIN_MTU:
            self.mtu = mtu

    def connection_lost(self):
        for r in self._reassemblers.values():
            r.reset()
        self.mtu = MIN_MTU
        self.session = False
        self._gps_logged = False

    # ----- inbound -----------------------------------------------------

    def receive(self, slot: str, packet: bytes):
        body = self._reassemblers[slot].feed(packet)
        if body is None:
            return
        if slot == "update":
            self.send_error("NOT_READY", None, "updates over Bluetooth come in a later version")
            return
        try:
            msg = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self.send_error("BAD_JSON", None, "message is not valid UTF-8 JSON")
            return
        if not isinstance(msg, dict) or not isinstance(msg.get("t"), str):
            self.send_error("BAD_JSON", None, 'message must be an object with a "t" field')
            return

        seq = msg.get("seq") if isinstance(msg.get("seq"), int) else None
        kind = msg["t"]
        # Never log content. GPS arrives every second, so only at debug level.
        log.log(logging.DEBUG if kind == "loc" else logging.INFO,
                "received %s on %s (%d bytes)", kind, slot, len(body))
        handler = self._HANDLERS.get((slot, kind))
        if handler is None:
            known = any(t == kind for _, t in self._HANDLERS)
            self.send_error("WRONG_SLOT" if known else "UNKNOWN_TYPE", seq,
                            f"type {kind!r} is not accepted on {slot}")
            return
        handler(self, msg, seq)

    def _on_hello(self, msg, seq):
        if msg.get("proto") != PROTOCOL_VERSION:
            self.send_error("VERSION", seq, f"device speaks protocol {PROTOCOL_VERSION}")
        self.send({
            "t": "hello", "seq": seq, "proto": PROTOCOL_VERSION,
            "fw": FIRMWARE_VERSION, "dev": self.device_name,
            "unsynced": len(self.trips.unsynced()) if self.trips else 0,
        })
        self.session = True
        self.sync_trips()

    def _on_trip_ack(self, msg, seq):
        trip_id = msg.get("id")
        if not isinstance(trip_id, str) or self.trips is None:
            self.send_error("BAD_FIELD", seq, "trip_ack needs an id")
            return
        if self.trips.mark_synced(trip_id):
            log.info("phone has trip %s", trip_id)

    def _on_settings(self, msg, seq):
        changes = {k: v for k, v in msg.items() if k not in ("t", "seq")}
        try:
            self.settings.update(changes)
        except SettingsError as e:
            self.send_error("BAD_FIELD", seq, str(e))
            return
        self.publish_settings()
        self.send({"t": "ack", "seq": seq})

    def publish_settings(self):
        """dc_speech follows the volume from here."""
        self.post(topics.SETTINGS, dict(self.settings.values), retain=True)

    def say(self, text: str, priority: str = "system", max_age=None):
        msg = {"text": text, "priority": priority}
        if max_age is not None:
            msg["max_age"] = max_age
        self.post(topics.SAY, msg)

    def _on_notif(self, msg, seq):
        app = clean_text(msg.get("app"), 40)
        sender = clean_text(msg.get("from"), 60)
        if not app or not sender:
            self.send_error("BAD_FIELD", seq, "notif needs app and from")
            return
        if self.settings.values["quiet"]:
            return
        if msg.get("kind") == "sms":
            spoken = f"Text message from {sender}"
        else:
            spoken = f"{app} message from {sender}"
        if self.settings.values["read_text"]:
            text = clean_text(msg.get("text"), 300)
            if text:
                spoken += f". {text}"
        self.say(spoken, "message", MESSAGE_MAX_AGE_S)

    def _on_call(self, msg, seq):
        sender = clean_text(msg.get("from"), 60)
        state = msg.get("state")
        if not sender or state not in ("ringing", "ended"):
            self.send_error("BAD_FIELD", seq, 'call needs from and state ("ringing" or "ended")')
            return
        if state == "ringing" and not self.settings.values["quiet"]:
            self.say(f"Incoming call from {sender}", "call", CALL_MAX_AGE_S)

    def _on_loc(self, msg, seq):
        try:
            lat, lon, kmh = float(msg["lat"]), float(msg["lon"]), float(msg["kmh"])
            acc = float(msg.get("acc", 0))
        except (KeyError, TypeError, ValueError):
            self.send_error("BAD_FIELD", seq, "loc needs numbers lat, lon and kmh")
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= kmh <= 300 and acc >= 0):
            self.send_error("BAD_FIELD", seq, "loc value out of range")
            return
        self.last_location = {"lat": lat, "lon": lon, "kmh": kmh, "acc": acc,
                              "received": self._clock()}
        if not self._gps_logged:
            log.info("phone GPS fixes arriving")
            self._gps_logged = True
        ts = msg.get("ts")
        ts = float(ts) if isinstance(ts, (int, float)) and ts > 1e9 else time.time()
        self.post(topics.GPS_FIX, {"lat": lat, "lon": lon, "kmh": kmh, "acc": acc,
                                   "ts": ts, "source": "phone"})

    _HANDLERS = {
        ("settings", "hello"): _on_hello,
        ("settings", "settings"): _on_settings,
        ("settings", "trip_ack"): _on_trip_ack,
        ("notification", "notif"): _on_notif,
        ("notification", "call"): _on_call,
        ("location", "loc"): _on_loc,
    }

    # ----- reads -------------------------------------------------------

    def settings_snapshot(self) -> bytes:
        return _encode({"t": "settings", **self.settings.values})

    def status_snapshot(self) -> bytes:
        loc = self.last_location
        fresh = loc is not None and self._clock() - loc["received"] <= PHONE_GPS_FRESH_S
        return _encode({"t": "status", "proto": PROTOCOL_VERSION, "fw": FIRMWARE_VERSION,
                        "dev": self.device_name, "phone_gps": fresh})

    # ----- outbound ----------------------------------------------------

    def send(self, msg: dict):
        for packet in split(_encode(msg), self._ids.next(), self.mtu):
            self._send_status(packet)

    def sync_trips(self):
        """Send every trip the phone does not have yet, on Trip Out. A trip is
        marked synced only when the phone answers with trip_ack, so anything
        lost on the way is sent again at the next connection."""
        if not self.session or self.trips is None or self._send_trip is None:
            return
        for trip in self.trips.unsynced():
            msg = {"t": "trip", **{k: v for k, v in trip.items() if k != "synced"}}
            try:
                for packet in split(_encode(msg), self._ids.next(), self.mtu):
                    self._send_trip(packet)
            except FrameError:
                log.exception("trip %s too large to send", trip.get("id"))
                continue
            log.info("sent trip %s to the phone", trip["id"])

    def send_error(self, code: str, seq, detail: str):
        log.warning("error %s: %s", code, detail)
        try:
            self.send({"t": "err", "code": code, "seq": seq, "msg": detail})
        except FrameError:
            log.exception("could not send error")

    def _frame_error(self, code, detail):
        self.send_error(code, None, detail)

    # ----- pairing -----------------------------------------------------

    def on_passkey(self, passkey: int):
        """Called when Bluetooth asks the device to show a pairing code."""
        if not self.pairing:
            log.warning("pairing attempt outside pairing mode ignored")
            return
        digits = ", ".join(f"{passkey:06d}")
        self.say(f"Pairing code: {digits}. Again: {digits}.")


def _encode(msg: dict) -> bytes:
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
