"""Vision pipeline entry point.

    camera -> health check -> proposals -> CNN -> 3-of-5 vote -> publish

Run on the Pi:
    python -m pipeline.main
Run on a laptop against a dash-cam video (no Pi hardware needed):
    python -m pipeline.main --source drive.mp4 --model models/sign_cnn_int8.tflite
"""
import argparse
import logging
import signal
import time

from .capture import CameraSource, LatestFrame
from .classifier import TFLiteClassifier
from .config import SPEED_LIMIT_KMH, Config
from .health import CameraHealth
from .proposals import propose
from .publisher import Publisher
from .throttle import pi_is_throttled
from .voting import TemporalVoter

log = logging.getLogger("vision")


class VisionPipeline:
    """Everything after capture. Kept separate from the camera thread so it
    can be fed frames directly in tests."""

    def __init__(self, cfg, classifier, publisher):
        self.cfg, self.clf, self.pub = cfg, classifier, publisher
        self.health = CameraHealth(cfg)
        self.voter = TemporalVoter(cfg.vote_window, cfg.vote_needed,
                                   cfg.sighting_gap_frames)
        self.stage_ms = {"health": 0.0, "propose": 0.0, "classify": 0.0}
        self.frames = 0

    def process(self, rgb, t_capture):
        self.frames += 1
        t0 = time.monotonic()

        change = self.health.update(rgb, t_capture)
        if change:
            self.pub.send({"type": "camera", "state": change})
            log.warning("camera %s", change)
            if change == "blinded":
                self.voter.reset()
        t1 = time.monotonic()
        if self.health.blinded:
            self._acc("health", t1 - t0)
            return []

        boxes = propose(rgb, self.cfg)
        t2 = time.monotonic()

        votes, best_conf = set(), {}
        if boxes:
            crops = [rgb[y:y + h, x:x + w] for (x, y, w, h) in boxes]
            for label, conf in self.clf.classify(crops):
                if conf >= self.cfg.min_confidence:
                    votes.add(label)
                    best_conf[label] = max(conf, best_conf.get(label, 0.0))
        t3 = time.monotonic()

        confirmed = self.voter.update(votes, t_capture)
        now = time.monotonic()
        for label in confirmed:
            ev = {"type": "sign", "label": label,
                  "confidence": round(best_conf.get(label, 0.0), 3),
                  "t_capture": t_capture, "t_publish": now,
                  "latency_ms": round((now - t_capture) * 1000, 1)}
            if label in SPEED_LIMIT_KMH:
                ev["speed_kmh"] = SPEED_LIMIT_KMH[label]
            self.pub.send(ev)
            log.info("SIGN %s conf=%.2f latency=%.0f ms", label,
                     ev["confidence"], ev["latency_ms"])

        self._acc("health", t1 - t0)
        self._acc("propose", t2 - t1)
        self._acc("classify", t3 - t2)
        return confirmed

    def _acc(self, k, dt):
        # running average in ms (for the PDR timing table)
        self.stage_ms[k] += (dt * 1000 - self.stage_ms[k]) / max(self.frames, 1)


def run(source, cfg):
    clf = TFLiteClassifier(cfg)
    pub = Publisher(cfg.socket_path)
    pipe = VisionPipeline(cfg, clf, pub)
    mailbox = LatestFrame()
    cam = CameraSource(cfg, mailbox, source)

    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))

    cam.start()
    seq, last_beat, beat_frames = 0, time.monotonic(), 0
    try:
        while not stop["flag"]:
            got = mailbox.get(seq, timeout=1.0)
            if got is None:
                if cam.finished.is_set():
                    break
                continue
            frame, ts, seq = got
            pipe.process(frame, ts)
            beat_frames += 1

            now = time.monotonic()
            if now - last_beat >= 2.0:            # heartbeat + thermal check
                throttled = pi_is_throttled()
                cam.set_fps(cfg.throttled_fps if throttled else cfg.target_fps)
                pub.send({"type": "heartbeat",
                          "fps": round(beat_frames / (now - last_beat), 1),
                          "throttled": throttled,
                          "stage_ms": {k: round(v, 1) for k, v in pipe.stage_ms.items()}})
                last_beat, beat_frames = now, 0
    finally:
        cam.stop()
        pub.close()


def main():
    ap = argparse.ArgumentParser(description="Driver Companion vision pipeline")
    ap.add_argument("--source", default="picamera",
                    help='"picamera", a webcam index, or a video file')
    ap.add_argument("--model", default=None, help="path to .tflite model")
    ap.add_argument("--socket", default=None, help="output socket path")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = Config()
    if a.model:
        from pathlib import Path
        cfg.model_path = Path(a.model)
    if a.socket:
        from pathlib import Path
        cfg.socket_path = Path(a.socket)
    source = int(a.source) if a.source.isdigit() else a.source
    run(source, cfg)


if __name__ == "__main__":
    main()
