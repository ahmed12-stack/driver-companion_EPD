"""Runs the vision pipeline in a worker thread until told to stop.

This is vision/pipeline/main.py's loop, made stoppable from the outside
(the ignition going off) and able to report "no camera" instead of hanging.
"""

import logging
import time

log = logging.getLogger(__name__)

HEARTBEAT_S = 2.0


def run_pipeline(cfg, publisher, source, stop, make_classifier=None) -> str:
    """Blocks until stop is set. Returns why it ended:
    "stopped" (asked to), "missing" (no frame ever came) or "ended" (a video file ran out).
    Raises if the pipeline itself fails (e.g. the model will not load)."""
    from pipeline.capture import CameraSource, LatestFrame
    from pipeline.classifier import load_classifier
    from pipeline.main import VisionPipeline
    from pipeline.throttle import pi_is_throttled

    classifier = (make_classifier or load_classifier)(cfg)
    pipe = VisionPipeline(cfg, classifier, publisher)
    mailbox = LatestFrame()
    cam = CameraSource(cfg, mailbox, source)
    cam.start()
    seq, frames, last_beat, beat_frames = 0, 0, time.monotonic(), 0
    try:
        while not stop.is_set():
            got = mailbox.get(seq, timeout=1.0)
            if got is None:
                if not cam.is_alive():
                    return "ended" if frames else "missing"
                continue
            frame, ts, seq = got
            pipe.process(frame, ts)
            frames += 1
            beat_frames += 1
            now = time.monotonic()
            if now - last_beat >= HEARTBEAT_S:  # heartbeat + thermal guard (REQ-24)
                throttled = pi_is_throttled()
                cam.set_fps(cfg.throttled_fps if throttled else cfg.target_fps)
                publisher.send({"type": "heartbeat",
                                "fps": round(beat_frames / (now - last_beat), 1),
                                "throttled": throttled,
                                "stage_ms": {k: round(v, 1) for k, v in pipe.stage_ms.items()}})
                last_beat, beat_frames = now, 0
        return "stopped"
    finally:
        cam.stop()
        cam.join(timeout=3)
