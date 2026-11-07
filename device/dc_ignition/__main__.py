"""python -m dc_ignition [--pin 27] [--always-on | --button]"""

import asyncio

from dc_common import module, phrases, topics

from .ignition import Ignition, TripButton


def add_args(p):
    p.add_argument("--pin", type=int, default=27, help="BCM GPIO pin (low = ignition on)")
    p.add_argument("--always-on", action="store_true",
                   help="no ignition wire: report the ignition as always on")
    p.add_argument("--button", action="store_true",
                   help="TRIP is a push button: press to start a trip, press again to end it")


async def setup(bus, args):
    def publish(on: bool):
        bus.post(topics.IGNITION, {"on": on}, retain=True)

    if args.always_on:
        publish(True)
        return None
    if args.button:
        def toggled(on: bool):
            publish(on)
            if button.state is on and button._last_toggle > 0:  # a real press, not power-up
                bus.post(topics.SAY, {"text": phrases.TRIP_STARTED if on else phrases.TRIP_ENDED,
                                      "priority": "system"})
        button = TripButton(args.pin, toggled)
        ignition = button
    else:
        ignition = Ignition(args.pin, publish)
    task = asyncio.get_running_loop().create_task(ignition.run())

    async def cleanup():
        task.cancel()
        ignition.close()
    return cleanup


if __name__ == "__main__":
    module.main("dc_ignition", setup, add_args)
