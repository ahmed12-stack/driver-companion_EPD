"""Minimal subscriber - copy this into the speech / speed modules.

    python tools/listen.py /run/driver_companion/vision.sock
"""
import json
import os
import socket
import sys
import tempfile
import time

path = sys.argv[1] if len(sys.argv) > 1 else "/run/driver_companion/vision.sock"
me = os.path.join(tempfile.mkdtemp(), "listener.sock")
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
s.bind(me)
os.chmod(me, 0o600)
s.settimeout(3.0)


def subscribe():
    """(Re)subscribe; retries until the vision process is up, so modules can
    start in any order and survive a vision restart (REQ-22)."""
    while True:
        try:
            s.sendto(b"subscribe", path)
            return
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.5)


subscribe()
print("subscribed to", path)
while True:
    try:
        data, _ = s.recvfrom(4096)
    except socket.timeout:            # heartbeats stopped: vision restarted?
        subscribe()
        continue
    ev = json.loads(data)
    if ev["type"] == "sign":
        print(f"SIGN  {ev['label']:<22} conf={ev['confidence']:.2f} "
              f"latency={ev['latency_ms']} ms")
    else:
        print(ev)
