"""python -m dc_ignition [--pin 27] [--always-on]"""

import asyncio

from dc_common import module, topics

from .ignition import Ignition


def add_args(p):
    p.add_argument("--pin", type=int, default=27, help="BCM GPIO pin (low = ignition on)")
    p.add_argument("--always-on", action="store_true",
                   help="no ignition wire: report the ignition as always on")


async def setup(bus, args):
    def publish(on: bool):
        bus.post(topics.IGNITION, {"on": on}, retain=True)

    if args.always_on:
        publish(True)
        return None
    ignition = Ignition(args.pin, publish)
    task = asyncio.get_running_loop().create_task(ignition.run())

    async def cleanup():
        task.cancel()
        ignition.close()
    return cleanup


if __name__ == "__main__":
    module.main("dc_ignition", setup, add_args)
