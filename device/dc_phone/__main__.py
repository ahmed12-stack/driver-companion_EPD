"""python -m dc_phone [--pair] [--button-pin 17] [--no-button]"""

import asyncio
import logging
import os
import signal

from dc_common import module, topics
from dc_common.storage import RecordStore

from .gateway import Gateway
from .settings import SettingsStore

log = logging.getLogger("dc_phone")

HOLD_TO_PAIR_S = 5
OUTBOX_MAX = 20  # REQ-11: the last 20 trips


def add_args(p):
    p.add_argument("--pair", action="store_true", help="start in pairing mode")
    p.add_argument("--button-pin", type=int, default=17, help="BCM GPIO pin of the button")
    p.add_argument("--no-button", action="store_true", help="do not use the GPIO button")


def setup_button(pin, loop, bt):
    try:
        from gpiozero import Button
    except ImportError:
        log.warning("gpiozero not installed; hold-to-pair button disabled")
        return None
    try:
        button = Button(pin, hold_time=HOLD_TO_PAIR_S)
    except Exception as e:  # pin in use or not available: keep running without it
        log.warning("button on GPIO%d unavailable (%s); hold-to-pair disabled", pin, e)
        return None
    # gpiozero calls this from its own thread, so hand it to the event loop.
    button.when_held = lambda: loop.call_soon_threadsafe(
        lambda: loop.create_task(bt.start_pairing()))
    return button


def make_gateway(bus, data_dir, send_status, send_trip):
    """The gateway and its outbox, wired to the board. Shared with the tests."""
    settings = SettingsStore(os.path.join(data_dir, "settings.json"))
    outbox = RecordStore(os.path.join(data_dir, "phone_outbox.json"), OUTBOX_MAX)
    gateway = Gateway(bus.post, settings, send_status=send_status,
                      send_trip=send_trip, trips=outbox)

    def on_trip_saved(topic, trip):
        if isinstance(trip, dict) and isinstance(trip.get("id"), str):
            outbox.add(trip)
            gateway.sync_trips()  # sent now if the phone is connected, else at next hello

    bus.subscribe(topics.TRIP_SAVED, on_trip_saved)
    gateway.publish_settings()  # so dc_speech knows the volume from the start
    return gateway


async def setup(bus, args):
    from .bluez import BluetoothService  # needs BlueZ and dbus-fast: only on the Pi

    bt = BluetoothService()
    gateway = make_gateway(bus, args.data_dir, bt.push_status, bt.push_trip)
    bt.attach(gateway)
    await bt.start()

    loop = asyncio.get_running_loop()
    # `sudo systemctl kill -s USR1 dc@phone` also starts pairing.
    loop.add_signal_handler(signal.SIGUSR1, lambda: loop.create_task(bt.start_pairing()))
    button = None if args.no_button else setup_button(args.button_pin, loop, bt)
    if args.pair:
        await bt.start_pairing()

    async def cleanup():
        if button is not None:
            button.close()
    return cleanup


if __name__ == "__main__":
    module.main("dc_phone", setup, add_args)
