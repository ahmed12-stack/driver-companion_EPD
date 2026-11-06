"""The path of a trip, kept small enough to send to the phone over Bluetooth.

Points are kept only every ROUTE_MIN_STEP_M metres. If a long trip collects
more than ROUTE_MAX_POINTS, every other point is dropped and the spacing
doubles, so a trip of any length stays under a few kilobytes.

The path is sent as an encoded polyline (the format map apps use): about
5 bytes per point instead of about 25 for plain numbers.
"""

import math

ROUTE_MIN_STEP_M = 25.0
ROUTE_MAX_POINTS = 300


def distance_m(a, b) -> float:
    """Metres between two (lat, lon) points; accurate enough at these distances."""
    lat = math.radians((a[0] + b[0]) / 2)
    dy = (b[0] - a[0]) * 111_320.0
    dx = (b[1] - a[1]) * 111_320.0 * math.cos(lat)
    return math.hypot(dx, dy)


def add_point(state: dict, lat: float, lon: float):
    """Add a fix to the trip's path if it is far enough from the last point kept."""
    route = state.setdefault("route", [])
    step = state.setdefault("route_step_m", ROUTE_MIN_STEP_M)
    point = [round(lat, 5), round(lon, 5)]
    if route and distance_m(route[-1], point) < step:
        return
    route.append(point)
    if len(route) > ROUTE_MAX_POINTS:
        state["route"] = route[::2]
        state["route_step_m"] = step * 2


def final_route(state: dict) -> list:
    """The kept points plus the very last fix, so the path ends where the car parked."""
    route = list(state.get("route") or [])
    last = state.get("last")
    if last is not None:
        end = [round(last["lat"], 5), round(last["lon"], 5)]
        if not route or route[-1] != end:
            route.append(end)
    return route


def encode(points) -> str:
    """Encoded polyline, precision 5 (Google's algorithm)."""
    out = []
    prev_lat = prev_lon = 0
    for lat, lon in points:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


def decode(text: str) -> list:
    """The reverse of encode(); used by the tests (the app has its own)."""
    points, index, lat, lon = [], 0, 0, 0
    while index < len(text):
        deltas = []
        for _ in range(2):
            shift = result = 0
            while True:
                b = ord(text[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / 1e5, lon / 1e5))
    return points
