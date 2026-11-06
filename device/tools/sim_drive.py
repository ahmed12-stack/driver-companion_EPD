"""Drive the real modules with a fake trip, through the message board.

  sudo systemctl stop dc@ignition          # it owns vehicle/ignition
  sudo /opt/driver-companion/venv/bin/python tools/sim_drive.py [--speed-limit 50]
  sudo systemctl start dc@ignition

Publishes the ignition coming on, about 10 minutes of GPS fixes (6 km, a
traffic stop) at 10x speed, then the ignition going off. dc_trip then speaks
the summary and dc_phone sends the trip to the app, exactly as in a real car.
With --speed-limit 50 it also pretends a 50 sign was read at the start, so
dc_speed warns on the faster stretches (up to 72 km/h).
"""

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dc_common import topics  # noqa: E402
from dc_common.bus import MqttBus  # noqa: E402
from dc_trip.simulate import fake_drive  # noqa: E402


async def main(args):
    bus = MqttBus("dc_sim", args.bus, announce=False)
    states = {}
    bus.subscribe(topics.STATUS_ALL, lambda t, s: states.__setitem__(topics.module_of(t), s))
    runner = asyncio.get_running_loop().create_task(bus.run())
    if not await bus._wait_connected():
        sys.exit(f"cannot reach the message board at {args.bus}: is mosquitto running?")
    await asyncio.sleep(0.5)  # retained module states arrive
    if states.get("dc_ignition") == "online" and not args.force:
        sys.exit("dc_ignition is running and owns the ignition. Stop it first:\n"
                 "  sudo systemctl stop dc@ignition\n(or pass --force)")
    for needed in ("dc_trip", "dc_speech"):
        if states.get(needed) != "online":
            print(f"note: {needed} is not running, so the trip will not be "
                  f"{'recorded' if needed == 'dc_trip' else 'spoken'}")

    fixes = 0

    def on_ignition(on):
        bus.post(topics.IGNITION, {"on": on}, retain=True)
        print("ignition", "on" if on else "off")
        if on and args.speed_limit:
            # as if the camera had just read a speed-limit sign (tests overspeed warnings)
            bus.post(topics.SPEED_LIMIT, {"kmh": args.speed_limit, "ts": time.time()}, retain=True)
            print(f"speed limit sign: {args.speed_limit} km/h")

    def on_fix(lat, lon, kmh, acc, ts):
        nonlocal fixes
        fixes += 1
        bus.post(topics.GPS_FIX, {"lat": lat, "lon": lon, "kmh": kmh, "acc": acc,
                                  "ts": ts, "source": "sim"})
        if fixes % 60 == 0:
            print(f"  {fixes // 60} min of driving sent, now {kmh:.0f} km/h")

    print(f"fake drive at {args.speed:g}x speed")
    await fake_drive(on_ignition, on_fix, args.speed)
    await asyncio.sleep(1)
    print("done: dc_trip now speaks the summary and sends the trip to the app")
    await bus.stop()
    runner.cancel()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bus", default=os.environ.get("DC_BUS", topics.BUS_SOCKET))
    p.add_argument("--speed", type=float, default=10.0, help="times faster than real time")
    p.add_argument("--force", action="store_true", help="run even if dc_ignition is running")
    p.add_argument("--speed-limit", type=int, default=0,
                   help="pretend a speed-limit sign was read at the start (e.g. 50: overspeed warnings)")
    asyncio.run(main(p.parse_args()))
