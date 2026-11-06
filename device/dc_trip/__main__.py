"""python -m dc_trip [--llm-bin PATH] [--llm-model PATH] [--halt-after-min N]"""

import logging
import os

from dc_common import module, topics

from .summary import Summarizer
from .trip import TripLogger, TripStore
from .trip_service import TripService

log = logging.getLogger("dc_trip")

LLM_DIR = "/opt/driver-companion/llm"


def add_args(p):
    p.add_argument("--llm-bin", default=os.environ.get("DC_LLM_BIN", f"{LLM_DIR}/llama-completion"))
    p.add_argument("--llm-model", default=os.environ.get("DC_LLM_MODEL", f"{LLM_DIR}/model.gguf"))
    p.add_argument("--halt-after-min", type=float, default=0,
                   help="shut the Pi down this many minutes after the ignition goes off (0 = never)")


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def make_service(bus, data_dir, summarizer, halt_after_min=0, halt=None):
    """The trip service wired to the board. Shared with the tests."""
    service = TripService(
        TripLogger(os.path.join(data_dir, "trip_current.json")),
        TripStore(os.path.join(data_dir, "trips.json")),
        summarizer,
        say=lambda text: bus.post(topics.SAY, {"text": text, "priority": "system"}),
        on_saved=lambda trip: bus.post(topics.TRIP_SAVED, trip),
        halt_after_min=halt_after_min, halt=halt)

    def on_ignition(topic, msg):
        if isinstance(msg, dict) and isinstance(msg.get("on"), bool):
            service.ignition_changed(msg["on"])

    def on_fix(topic, fix):
        if not isinstance(fix, dict):
            return
        values = [_number(fix.get(k)) for k in ("lat", "lon", "kmh", "acc", "ts")]
        if any(v is None for v in values):
            log.debug("ignored a malformed GPS fix")
            return
        lat, lon, kmh, acc, ts = values
        if -90 <= lat <= 90 and -180 <= lon <= 180 and acc >= 0:
            service.add_fix(lat, lon, kmh, acc, ts)

    def on_event(topic, msg):
        if isinstance(msg, dict) and msg.get("event") in topics.TRIP_EVENTS:
            seconds = _number(msg.get("seconds")) or 0
            service.record(msg["event"], max(0.0, float(seconds)),
                           counted=msg.get("continued") is not True)

    bus.subscribe(topics.IGNITION, on_ignition)
    bus.subscribe(topics.GPS_FIX, on_fix)
    bus.subscribe(topics.TRIP_EVENT, on_event)
    return service


async def setup(bus, args):
    summarizer = Summarizer(args.llm_bin, args.llm_model,
                            cache_path=os.path.join(args.data_dir, "llm_prompt.cache"))
    log.info("trip remarks by %s", "the LLM" if summarizer.available
             else "nobody (no LLM installed): facts only")
    service = make_service(bus, args.data_dir, summarizer, args.halt_after_min)

    async def cleanup():
        service.save_now()  # a running trip resumes after a restart
    return cleanup


if __name__ == "__main__":
    module.main("dc_trip", setup, add_args)
