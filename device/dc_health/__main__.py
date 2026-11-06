"""python -m dc_health [--modules a,b] [--expect-hardware camera,imu,gnss]"""

import asyncio
import logging
import os

from dc_common import module, topics

from . import hardware
from .monitor import RESTART_GRACE_S, Monitor

log = logging.getLogger("dc_health")

BUILT_IN_MODULES = "dc_speech,dc_phone,dc_trip,dc_ignition"
MODULES_FILE = os.environ.get("DC_MODULES_FILE", "/etc/driver-companion/modules.list")


def default_modules(path: str = MODULES_FILE) -> str:
    """The installer writes the modules it set up (e.g. dc_vision only when the
    vision folder came with the update) to this file."""
    try:
        with open(path, encoding="utf-8") as f:
            text = ",".join(line.strip() for line in f if line.strip() and not line.startswith("#"))
        return text or BUILT_IN_MODULES
    except OSError:
        return BUILT_IN_MODULES
CHECK_EVERY_S = 2


def _list(text: str) -> list:
    return [x.strip() for x in text.split(",") if x.strip()]


def add_args(p):
    p.add_argument("--modules", default=default_modules(),
                   help="modules that must be running (comma-separated)")
    p.add_argument("--expect-hardware", default="",
                   help="hardware that must be present: camera, imu, gnss (comma-separated)")
    p.add_argument("--startup-wait", type=float, default=20,
                   help="seconds to let the other modules start before the self-test")
    p.add_argument("--restart-grace", type=float, default=RESTART_GRACE_S,
                   help="seconds a crashed module may take to come back before a warning")


async def setup(bus, args):
    monitor = Monitor(_list(args.modules),
                      say=lambda text: bus.post(topics.SAY, {"text": text, "priority": "system"}),
                      grace_s=args.restart_grace)
    bus.subscribe(topics.STATUS_ALL, monitor.on_status)
    bus.subscribe(topics.ALIVE_ALL, monitor.on_alive)
    bus.subscribe(topics.VISION_CAMERA, monitor.on_camera)
    expect = _list(args.expect_hardware)

    async def selftest_then_watch():
        await asyncio.sleep(args.startup_wait)
        found = await hardware.check_all()
        result = monitor.selftest(found, expect)
        log.info("self test: %s | modules %s | hardware %s", result["spoken"],
                 result["modules"], result["hardware"])
        await bus.publish(topics.SELFTEST, result, retain=True)
        monitor.say(result["spoken"])
        while True:
            await asyncio.sleep(CHECK_EVERY_S)
            monitor.check()

    task = asyncio.get_running_loop().create_task(selftest_then_watch())

    async def cleanup():
        task.cancel()
    return cleanup


if __name__ == "__main__":
    module.main("dc_health", setup, add_args)
