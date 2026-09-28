"""Stage 6 - Tell the other modules (interface contract).

Events go out as one JSON object per line over a Unix datagram socket.
Any module (Abhay's speech, Amrit's speed logic, Anirudha's trip logger)
connects by sending b"subscribe" to the socket; the vision process then
sends it every event. Unix sockets never leave the device (REQ-16), and
the socket file is chmod 0600 (REQ-30).

Event types:
  {"type":"sign", "label":"speed_limit_50", "speed_kmh":50,
   "confidence":0.93, "t_capture":..., "t_publish":..., "latency_ms":312}
  {"type":"camera", "state":"blinded"}   -> speech says "Sign alerts paused"
  {"type":"camera", "state":"recovered"} -> speech says "Sign alerts resumed"
  {"type":"heartbeat", "fps":14.8, "throttled":false}  every 2 s (REQ-22)
"""
import json
import os
import socket
import time


class Publisher:
    def __init__(self, path):
        self.path = str(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if os.path.exists(self.path):
            os.unlink(self.path)
        old = os.umask(0o177)                 # created as 0600
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.sock.bind(self.path)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        self.sock.setblocking(False)
        self.subscribers = set()

    def _accept_subscribers(self):
        while True:
            try:
                msg, addr = self.sock.recvfrom(64)
            except BlockingIOError:
                return
            if msg.strip() == b"subscribe" and addr:
                self.subscribers.add(addr)

    def send(self, event):
        self._accept_subscribers()
        event.setdefault("t_publish", time.monotonic())
        data = (json.dumps(event) + "\n").encode()
        for addr in list(self.subscribers):
            try:
                self.sock.sendto(data, addr)
            except OSError:                   # subscriber died
                self.subscribers.discard(addr)

    def close(self):
        self.sock.close()
        if os.path.exists(self.path):
            os.unlink(self.path)


class ListPublisher:
    """Test double that just stores events."""

    def __init__(self):
        self.events = []

    def send(self, event):
        event.setdefault("t_publish", time.monotonic())
        self.events.append(event)

    def close(self):
        pass
