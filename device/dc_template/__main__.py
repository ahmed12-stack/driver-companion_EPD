"""python -m dc_template [--demo]

The smallest useful module. Run it on the Pi next to the others:
    sudo /opt/driver-companion/venv/bin/python -m dc_template --demo -v
"""

import logging

from dc_common import module, topics

log = logging.getLogger("dc_template")


def add_args(p):
    # Your module's own options go here.
    p.add_argument("--demo", action="store_true", help="speak the speed every 10 km/h")


async def setup(bus, args):
    last_band = None

    # 1. React to a topic. Keep handlers quick; start a task for slow work.
    def on_fix(topic, fix):
        nonlocal last_band
        if not isinstance(fix, dict) or not isinstance(fix.get("kmh"), (int, float)):
            return
        log.debug("fix from %s: %.0f km/h", fix.get("source"), fix["kmh"])
        band = int(fix["kmh"] // 10)
        if args.demo and band != last_band:
            last_band = band
            # 2. Publish to a topic: here, ask dc_speech to say something.
            bus.post(topics.SAY, {"text": f"About {band * 10} kilometres per hour.",
                                  "priority": "system"})

    bus.subscribe(topics.GPS_FIX, on_fix)

    # A module that detects something for the trip would publish, e.g.:
    #   bus.post(topics.TRIP_EVENT, {"event": "overspeed", "seconds": 4})

    # 3. Optional: undo anything on stop (close hardware, cancel tasks).
    async def cleanup():
        log.info("template module stopped")
    return cleanup


if __name__ == "__main__":
    module.main("dc_template", setup, add_args)
