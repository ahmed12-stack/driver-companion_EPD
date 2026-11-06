"""python -m dc_vision [--vision-dir DIR] [--model FILE] [--source picamera] [--always-on]

Reads road signs with the Pi camera while the car is switched on, and tells
the rest of the device: the speech module speaks the sign, the trip logger
counts it, and a speed-limit sign is kept on road/speed_limit for the
overspeed check. Needs no phone.

The pipeline itself (camera, health check, region proposals, CNN, 3-of-5
vote) lives in vision/pipeline; the installer copies it to
/opt/driver-companion/vision.
"""

import asyncio
import importlib.util
import logging
import os
import sys
import threading
import time
from pathlib import Path

from dc_common import module, topics

from .reporter import SignReporter
from .runner import run_pipeline

log = logging.getLogger("dc_vision")

RETRY_S = 30        # no camera, or the pipeline failed: try again after this
STOP_WAIT_S = 5


def add_args(p):
    p.add_argument("--vision-dir", default=os.environ.get("DC_VISION_DIR", "/opt/driver-companion/vision"),
                   help="folder holding pipeline/ and models/")
    p.add_argument("--model", default=None,
                   help="model file (default: the .tflite model, or the .npz one if TFLite is missing)")
    p.add_argument("--source", default="picamera",
                   help='"picamera", a webcam number, or a video file (for testing)')
    p.add_argument("--always-on", action="store_true",
                   help="read signs even with the ignition off (bench testing)")


def has_tflite() -> bool:
    return any(importlib.util.find_spec(m) is not None for m in ("ai_edge_litert", "tflite_runtime"))


def pick_model(vision_dir: str, model) -> Path:
    if model:
        return Path(model)
    models = Path(vision_dir) / "models"
    tflite = models / "sign_cnn_int8.tflite"
    if has_tflite() and tflite.exists():
        return tflite
    return models / "sign_cnn.npz"  # same CNN in plain NumPy: slower, no TFLite needed


class BoardPublisher:
    """Stands in for the pipeline's socket publisher: hands each event to the
    event loop (the pipeline runs in a worker thread)."""

    def __init__(self, loop, on_event):
        self._loop = loop
        self._on_event = on_event

    def send(self, event):
        event.setdefault("t_publish", time.monotonic())
        self._loop.call_soon_threadsafe(self._on_event, dict(event))

    def close(self):
        pass


class Supervisor:
    """Runs the pipeline while the ignition is on; restarts it when it fails."""

    def __init__(self, reporter, start_pipeline, source="picamera", retry_s=RETRY_S):
        self.reporter = reporter
        self.start_pipeline = start_pipeline  # (stop_event) -> awaitable result
        self.source = source
        self.retry_s = retry_s
        self.on = asyncio.Event()
        self.off = asyncio.Event()
        self.off.set()
        self.runs = 0

    def ignition(self, on: bool):
        if on:
            self.off.clear()
            self.on.set()
        else:
            self.on.clear()
            self.off.set()

    async def run(self):
        while True:
            await self.on.wait()
            stop = threading.Event()
            self.runs += 1
            job = asyncio.ensure_future(self.start_pipeline(stop))
            off = asyncio.ensure_future(self.off.wait())
            done, _ = await asyncio.wait({job, off}, return_when=asyncio.FIRST_COMPLETED)
            if off in done:  # ignition off: stop reading signs
                stop.set()
                try:
                    await asyncio.wait_for(job, STOP_WAIT_S)
                except Exception:
                    log.exception("pipeline did not stop cleanly")
                self.reporter.set_camera("off")
                continue
            off.cancel()
            try:
                result = job.result()
            except Exception:
                log.exception("sign reader failed; trying again in %d s", self.retry_s)
                self.reporter.failed()
                result = "failed"
            if result == "ended" and self.source == "picamera":
                result = "missing"  # the camera stopped sending frames
            if result == "missing":
                log.warning("no camera found; trying again in %d s", self.retry_s)
                self.reporter.set_camera("missing")
            elif result == "ended":
                log.info("video finished")
                await self.off.wait()  # test video: wait for the ignition to go off
                continue
            try:  # wait before retrying, unless the ignition goes off
                await asyncio.wait_for(self.off.wait(), self.retry_s)
            except asyncio.TimeoutError:
                pass


async def setup(bus, args, make_classifier=None):
    vision_dir = os.path.abspath(args.vision_dir)
    if not os.path.isdir(os.path.join(vision_dir, "pipeline")):
        raise SystemExit(f"vision pipeline not found in {vision_dir}")
    if vision_dir not in sys.path:
        sys.path.insert(0, vision_dir)
    from pipeline.config import Config

    cfg = Config()
    cfg.model_path = pick_model(vision_dir, args.model)
    source = int(args.source) if str(args.source).isdigit() else args.source
    log.info("model %s, camera %s", cfg.model_path, source)

    loop = asyncio.get_running_loop()
    reporter = SignReporter(bus.post)
    publisher = BoardPublisher(loop, reporter.on_event)

    def start_pipeline(stop):
        return loop.run_in_executor(None, run_pipeline, cfg, publisher, source, stop, make_classifier)

    sup = Supervisor(reporter, start_pipeline, source)

    def on_ignition(topic, msg):
        on = isinstance(msg, dict) and msg.get("on") is True
        sup.ignition(on or args.always_on)

    bus.subscribe(topics.IGNITION, on_ignition)
    if args.always_on:
        sup.ignition(True)
    else:
        reporter.set_camera("off")
    task = loop.create_task(sup.run())

    async def cleanup():
        sup.ignition(False)  # the supervisor stops the camera and the worker thread
        for _ in range(STOP_WAIT_S * 10):
            if reporter.camera != "ok" and reporter.camera != "blinded":
                break
            await asyncio.sleep(0.1)
        task.cancel()

    setup.supervisor = sup  # for tests
    setup.reporter = reporter
    return cleanup


if __name__ == "__main__":
    module.main("dc_vision", setup, add_args)
