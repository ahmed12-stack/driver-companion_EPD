"""The health logic, separate from the board so it can be tested."""

import logging
import time

from dc_common import phrases, topics

log = logging.getLogger(__name__)

HARDWARE_NAMES = {"camera": "camera", "imu": "motion sensor", "gnss": "GPS module"}

RESTART_GRACE_S = 10  # systemd restarts a crashed module within a few seconds
SILENT_S = 20         # no heartbeat for this long while "online" = hung


def friendly(module: str) -> str:
    return phrases.MODULE_NAMES.get(module, module.removeprefix("dc_"))


class Monitor:
    def __init__(self, expected, say, clock=time.monotonic,
                 grace_s: float = RESTART_GRACE_S, silent_s: float = SILENT_S):
        self.expected = list(expected)
        self.grace_s = grace_s
        self.silent_s = silent_s
        self.say = say
        self._clock = clock
        self.started = clock()
        self.status = {}      # module -> (state, since)
        self.last_alive = {}  # module -> time
        self.warned = set()
        self.camera = None    # from dc_vision: ok | blinded | missing | off

    def on_status(self, topic, state):
        module = topics.module_of(topic)
        if not module or state not in ("online", "stopped", "offline"):
            return
        self.status[module] = (state, self._clock())
        if state == "online":
            self.last_alive[module] = self._clock()
            if module in self.warned:
                self.warned.discard(module)
                log.info("%s is working again", module)

    def on_camera(self, topic, msg):
        if isinstance(msg, dict) and msg.get("state") in topics.CAMERA_STATES:
            self.camera = msg["state"]

    def on_alive(self, topic, msg):
        module = topics.module_of(topic)
        if module:
            self.last_alive[module] = self._clock()

    def problem(self, module: str):
        state, since = self.status.get(module, (None, self.started))
        now = self._clock()
        if state == "offline" and now - since > self.grace_s:
            return "stopped working"
        if state == "online" and now - self.last_alive.get(module, since) > self.silent_s:
            return "is not responding"
        return None

    def check(self) -> list:
        """Warn once per module that has a problem; returns the current problems."""
        found = []
        for module in self.expected:
            problem = self.problem(module)
            if problem is None:
                continue
            found.append((module, problem))
            if module not in self.warned:
                self.warned.add(module)
                log.warning("%s %s", module, problem)
                self.say(phrases.module_warning(module, problem))
        return found

    def selftest(self, hardware: dict, expect_hardware) -> dict:
        problems = []
        modules = {}
        for module in self.expected:
            state = self.status.get(module, (None, 0))[0] or "missing"
            modules[module] = state
            if state != "online":
                problems.append(f"{friendly(module)} not running")
        if "dc_vision" in self.expected and self.camera == "missing":
            problems.append("camera not found")
        for item in expect_hardware:
            if hardware.get(item) is not True:
                problems.append(f"{HARDWARE_NAMES.get(item, item)} not found")
        spoken = (phrases.SELFTEST_OK if not problems
                  else "Self test: " + ", ".join(problems) + ".")
        return {"ok": not problems, "modules": modules, "hardware": hardware,
                "problems": problems, "spoken": spoken}
