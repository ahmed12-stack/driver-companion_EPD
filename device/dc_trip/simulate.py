"""A recorded-style fake drive, for testing and demos without a car.

Drives about 6 km over 10 minutes of trip time: pulls away, a traffic stop,
a faster stretch, slows down and parks, then switches the ignition off. Fed
through the same code as a real drive. With speed 10 it takes about a minute.
"""

import asyncio
import math
import time

# (seconds, speed at start, speed at end) in km/h
PROFILE = [
    (15, 0, 0),     # engine on, not moving yet
    (20, 0, 30),
    (100, 30, 45),
    (25, 45, 0),
    (75, 0, 0),     # waiting in traffic
    (20, 0, 50),
    (150, 50, 62),
    (15, 62, 72),
    (30, 72, 55),
    (120, 55, 40),
    (25, 40, 0),
    (30, 0, 0),     # parked, engine still on
]
START = (20.3550, 85.8190)


async def fake_drive(on_ignition, on_fix, speed: float = 10.0):
    lat, lon = START
    heading = math.radians(30)
    ts = time.time()
    on_ignition(True)
    for seconds, v0, v1 in PROFILE:
        for i in range(seconds):
            kmh = v0 + (v1 - v0) * (i + 1) / seconds
            step_km = kmh / 3600
            lat += step_km / 111.32 * math.cos(heading)
            lon += step_km / (111.32 * math.cos(math.radians(lat))) * math.sin(heading)
            heading += math.radians(0.3)
            ts += 1
            on_fix(lat, lon, kmh, 5.0, ts)
            await asyncio.sleep(1 / speed if speed > 0 else 0)
    on_ignition(False)
