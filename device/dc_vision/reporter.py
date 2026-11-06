"""Turns the vision pipeline's events into messages on the board.

The pipeline (Asir's vision/pipeline) sends events like
  {"type": "sign", "label": "speed_limit_50", "speed_kmh": 50, ...}
  {"type": "camera", "state": "blinded" | "recovered"}
  {"type": "heartbeat", "fps": 14.8, "throttled": false, "stage_ms": {...}}
This class runs on the event loop and decides what the rest of the device hears.
"""

import logging
import time

from dc_common import phrases, topics

log = logging.getLogger(__name__)

SIGN_MAX_AGE_S = 3.0  # a sign alert older than this is no use any more
STATS_LOG_EVERY_S = 60


class SignReporter:
    def __init__(self, post, clock=time.monotonic, wall=time.time):
        self.post = post          # post(topic, data, retain=False)
        self.camera = None        # ok | blinded | missing | off
        self.signs_seen = 0
        self.last_stats = None
        self._clock = clock
        self._wall = wall
        self._last_stats_log = float("-inf")
        self._missing_said = False

    def on_event(self, ev: dict):
        kind = ev.get("type")
        if kind == "sign":
            self._on_sign(ev)
        elif kind == "camera":
            state = ev.get("state")
            if state == "blinded":
                self.set_camera("blinded")
            elif state == "recovered":
                self.set_camera("ok")
        elif kind == "heartbeat":
            self.last_stats = ev
            if self.camera in (None, "off", "missing"):
                self.set_camera("ok")  # frames are arriving
            now = self._clock()
            if now - self._last_stats_log >= STATS_LOG_EVERY_S:
                self._last_stats_log = now
                log.info("camera %.1f fps, throttled %s, ms per stage %s",
                         ev.get("fps", 0), ev.get("throttled"), ev.get("stage_ms"))

    def _on_sign(self, ev: dict):
        label = ev.get("label")
        if not isinstance(label, str) or not label:
            return
        if self.camera == "blinded":
            return  # cannot happen in the pipeline, but never speak through a pause
        self.signs_seen += 1
        log.info("sign %s (confidence %.2f, %s ms after the frame)", label,
                 ev.get("confidence", 0), ev.get("latency_ms"))
        self.post(topics.SAY, {"text": phrases.sign_phrase(label), "priority": "sign",
                               "max_age": SIGN_MAX_AGE_S})
        self.post(topics.TRIP_EVENT, {"event": "sign"})
        kmh = ev.get("speed_kmh")
        if isinstance(kmh, (int, float)) and not isinstance(kmh, bool) and kmh > 0:
            self.post(topics.SPEED_LIMIT, {"kmh": int(kmh), "ts": self._wall()}, retain=True)

    def set_camera(self, state: str):
        """Record the camera state on the board and tell the driver when it matters."""
        before, self.camera = self.camera, state
        if state == before:
            return
        self.post(topics.VISION_CAMERA, {"state": state}, retain=True)
        if state == "blinded":
            self.say(phrases.SIGNS_PAUSED)
        elif state == "ok" and before == "blinded":
            self.say(phrases.SIGNS_RESUMED)
        elif state == "ok" and before == "missing":
            self.say(phrases.CAMERA_FOUND)
        elif state == "missing" and not self._missing_said:
            self._missing_said = True  # once per start, not on every retry
            self.say(phrases.CAMERA_MISSING)

    def failed(self):
        self.say(phrases.SIGNS_FAILED)

    def say(self, text: str):
        self.post(topics.SAY, {"text": text, "priority": "system"})
