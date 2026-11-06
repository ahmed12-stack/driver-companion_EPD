"""The message-board contract: every topic, who publishes it, what it carries.

Every module talks to the others only through these topics. Messages are
JSON. Change a topic or its fields only with the whole group's agreement.

  topic                 retained  published by      fields
  --------------------  --------  ----------------  ---------------------------------------
  vehicle/ignition      yes       dc_ignition       on (bool)
  gps/fix               no        dc_phone, dc_gnss lat, lon, kmh, acc (m), ts (epoch s), source
  alert/say             no        any module        text, priority, max_age (s, optional)
  settings/current      yes       dc_phone          vol, apps, read_text, quiet
  trip/event            no        speed, motion,    event (overspeed | harsh_brake |
                                  vision modules    harsh_accel | sign), seconds (optional),
                                                    continued (optional: more time for an
                                                    event already counted, not a new one)
  trip/saved            no        dc_trip           the finished trip incl. route (dc_trip/trip.py)
  road/speed_limit      yes       dc_vision         kmh, ts (epoch s): the last speed-limit sign read
  road/overspeed        yes       dc_speed          over (bool), kmh, limit: is the driver over now
  vision/camera         yes       dc_vision         state: ok | blinded | missing | off
  health/selftest       yes       dc_health         ok, modules, hardware, problems
  module/<name>/status  yes       every module      "online" | "stopped" | "offline"
  module/<name>/alive   no        every module      ts, every 5 s

"offline" is the last will: the board publishes it by itself when a module
disappears without saying goodbye (a crash or a hang). "stopped" means the
module was shut down on purpose.
"""

BUS_SOCKET = "/run/mosquitto/dc-bus.sock"

IGNITION = "vehicle/ignition"
GPS_FIX = "gps/fix"
SAY = "alert/say"
SETTINGS = "settings/current"
TRIP_EVENT = "trip/event"
TRIP_SAVED = "trip/saved"
SELFTEST = "health/selftest"
SPEED_LIMIT = "road/speed_limit"
OVERSPEED = "road/overspeed"
VISION_CAMERA = "vision/camera"

CAMERA_STATES = ("ok", "blinded", "missing", "off")

STATUS_ALL = "module/+/status"
ALIVE_ALL = "module/+/alive"

# alert/say priorities, most urgent first (REQ-17: messages wait for a gap)
PRIORITIES = ("overspeed", "sign", "call", "message", "system")

TRIP_EVENTS = ("overspeed", "harsh_brake", "harsh_accel", "sign")


def status(module: str) -> str:
    return f"module/{module}/status"


def alive(module: str) -> str:
    return f"module/{module}/alive"


def module_of(topic: str) -> str:
    """'module/dc_trip/status' -> 'dc_trip'."""
    parts = topic.split("/")
    return parts[1] if len(parts) == 3 and parts[0] == "module" else ""
