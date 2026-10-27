"""Make and read protocol packets by hand, for testing with nRF Connect.

  python tools/frame.py notif                 # hex to paste into a write
  python tools/frame.py hello --mtu 23        # split for a small MTU
  python tools/frame.py --json '{"t":"call","from":"Mom","state":"ringing"}'
  python tools/frame.py decode 0x00-00-01-7B-22-74...   # read a notification
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dc_ble.framing import split  # noqa: E402

SAMPLES = {
    "hello": {"t": "hello", "seq": 1, "proto": 1, "app": "nrf-test"},
    "notif": {"t": "notif", "seq": 2, "app": "WhatsApp", "kind": "msg",
              "from": "Rahul", "text": "Reached?"},
    "sms": {"t": "notif", "seq": 3, "app": "Messages", "kind": "sms",
            "from": "Bank", "text": "Your OTP is 1234"},
    "call": {"t": "call", "seq": 4, "from": "Mom", "state": "ringing"},
    "settings": {"t": "settings", "seq": 5, "read_text": True},
    "loc": {"t": "loc", "lat": 20.5, "lon": 78.9, "kmh": 42.5, "acc": 5},
}


def encode(msg, mtu, msg_id):
    body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    return split(body, msg_id, mtu)


def decode(hex_parts):
    raw = re.sub(r"0x|[^0-9a-fA-F]", "", "".join(hex_parts))
    data = bytes.fromhex(raw)
    print(f"header: id={data[0]} chunk={data[1]} of {data[2]}")
    print(data[3:].decode("utf-8", errors="replace"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("sample", nargs="?", choices=list(SAMPLES) + ["decode"])
    p.add_argument("hex", nargs="*", help="packet hex, for decode")
    p.add_argument("--json", help="custom message instead of a sample")
    p.add_argument("--mtu", type=int, default=247)
    p.add_argument("--id", type=int, default=0, help="message ID byte (0-255)")
    a = p.parse_args()

    if a.sample == "decode":
        decode(a.hex)
        return
    msg = json.loads(a.json) if a.json else SAMPLES.get(a.sample)
    if msg is None:
        p.error("give a sample name or --json")
    packets = encode(msg, a.mtu, a.id)
    print(f"{len(packets)} packet(s) at MTU {a.mtu}; write them in order:")
    for i, pkt in enumerate(packets):
        print(f"  {i + 1}: {pkt.hex().upper()}")


if __name__ == "__main__":
    main()
