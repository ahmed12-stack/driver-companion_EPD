"""Entry point: python -m dc_ble [--pair] [--button-pin 17] [--no-button] [-v]"""

import argparse
import asyncio
import logging
import os
import signal

from .bluez import BluetoothService
from .gateway import Gateway
from .settings import SettingsStore
from .speaker import Speaker

log = logging.getLogger("dc_ble")

HOLD_TO_PAIR_S = 5


def parse_args():
    p = argparse.ArgumentParser(description="Driver Companion Bluetooth gateway")
    p.add_argument("--pair", action="store_true", help="start in pairing mode")
    p.add_argument("--button-pin", type=int, default=17, help="BCM GPIO pin of the button")
    p.add_argument("--no-button", action="store_true", help="do not use the GPIO button")
    p.add_argument("--data-dir", default=os.environ.get("DC_DATA_DIR", "/var/lib/driver-companion"))
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def setup_button(pin, loop, bt):
    try:
        from gpiozero import Button
    except ImportError:
        log.warning("gpiozero not installed; hold-to-pair button disabled")
        return None
    button = Button(pin, hold_time=HOLD_TO_PAIR_S)
    # gpiozero calls this from its own thread, so hand it to the event loop.
    button.when_held = lambda: loop.call_soon_threadsafe(
        lambda: loop.create_task(bt.start_pairing()))
    return button


async def main(args):
    os.umask(0o077)
    settings = SettingsStore(os.path.join(args.data_dir, "settings.json"))
    speaker = Speaker(volume=settings.values["vol"])
    bt = BluetoothService()
    gateway = Gateway(speaker, settings, send_status=bt.push_status)
    bt.attach(gateway)

    loop = asyncio.get_running_loop()
    speaker_task = loop.create_task(speaker.run())
    await bt.start()

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    # `sudo systemctl kill -s USR1 driver-companion-ble` also starts pairing.
    loop.add_signal_handler(signal.SIGUSR1, lambda: loop.create_task(bt.start_pairing()))
    button = None if args.no_button else setup_button(args.button_pin, loop, bt)

    if args.pair:
        await bt.start_pairing()

    await stop.wait()
    speaker_task.cancel()
    if button is not None:
        button.close()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main(args))
