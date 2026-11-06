"""The runner every module uses: logging, the message board, heartbeats, and a
clean stop on Ctrl+C or `systemctl stop`.

A module is just:

    async def setup(bus, args):
        bus.subscribe("some/topic", on_message)
        ...start any background tasks...
        return cleanup   # optional: an async function run on stop

    if __name__ == "__main__":
        module.main("dc_mymodule", setup, add_args)
"""

import argparse
import asyncio
import logging
import os
import signal
import time

from . import topics
from .bus import MqttBus

log = logging.getLogger(__name__)

HEARTBEAT_S = 5


def parse_args(name: str, add_args=None, argv=None):
    p = argparse.ArgumentParser(prog=f"python -m {name}")
    p.add_argument("--bus", default=os.environ.get("DC_BUS", topics.BUS_SOCKET),
                   help="message board socket")
    p.add_argument("--data-dir", default=os.environ.get("DC_DATA_DIR", "/var/lib/driver-companion"))
    p.add_argument("-v", "--verbose", action="store_true")
    if add_args:
        add_args(p)
    return p.parse_args(argv)


async def _heartbeat(bus, name):
    while True:
        await bus.publish(topics.alive(name), {"ts": time.time()})
        await asyncio.sleep(HEARTBEAT_S)


async def run(name: str, setup, args, bus=None, stop: asyncio.Event = None):
    """Run one module until stopped. Tests pass their own bus and stop event."""
    os.umask(0o077)  # REQ-30: files we create are private
    bus = bus or MqttBus(name, args.bus)
    loop = asyncio.get_running_loop()
    if stop is None:  # tests bring their own stop event
        stop = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

    # Connect first, so messages posted during a slow setup (e.g. Bluetooth)
    # go out; subscriptions made during setup still get retained messages.
    tasks = [loop.create_task(bus.run())]
    cleanup = await setup(bus, args)
    tasks.append(loop.create_task(_heartbeat(bus, name)))
    log.info("%s running", name)
    await stop.wait()
    log.info("%s stopping", name)
    if cleanup is not None:
        try:
            await cleanup()
        except Exception:
            log.exception("cleanup failed")
    try:
        await asyncio.wait_for(bus.stop(), 2)
    except Exception:
        pass
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main(name: str, setup, add_args=None, argv=None):
    args = parse_args(name, add_args, argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format=f"%(asctime)s %(levelname)s {name}: %(message)s")
    asyncio.run(run(name, setup, args))
