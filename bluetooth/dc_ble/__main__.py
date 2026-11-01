"""Entry point: python -m dc_ble [options]; see --help."""

import argparse
import asyncio
import logging
import os
import signal

from .bluez import BluetoothService
from .gateway import Gateway
from .ignition import Ignition
from .settings import SettingsStore
from .simulate import fake_drive
from .speaker import Speaker
from .summary import Summarizer
from .trip import TripLogger, TripStore
from .trip_service import TripService

log = logging.getLogger("dc_ble")

HOLD_TO_PAIR_S = 5
LLM_DIR = "/opt/driver-companion/llm"


def parse_args():
    p = argparse.ArgumentParser(description="Driver Companion Bluetooth gateway")
    p.add_argument("--pair", action="store_true", help="start in pairing mode")
    p.add_argument("--button-pin", type=int, default=17, help="BCM GPIO pin of the button")
    p.add_argument("--no-button", action="store_true", help="do not use the GPIO button")
    p.add_argument("--ignition-pin", type=int, default=27,
                   help="BCM GPIO pin of the ignition sense input (low = ignition on)")
    p.add_argument("--no-ignition", action="store_true",
                   help="no ignition wire: treat the ignition as always on")
    p.add_argument("--simulate", action="store_true",
                   help="run one fake drive at start-up (testing without a car)")
    p.add_argument("--sim-speed", type=float, default=10.0,
                   help="how many times faster than real time the fake drive runs")
    p.add_argument("--llm-bin", default=os.environ.get("DC_LLM_BIN", f"{LLM_DIR}/llama-completion"))
    p.add_argument("--llm-model", default=os.environ.get("DC_LLM_MODEL", f"{LLM_DIR}/model.gguf"))
    p.add_argument("--halt-after-min", type=float, default=0,
                   help="shut the Pi down this many minutes after the ignition goes off (0 = never)")
    p.add_argument("--data-dir", default=os.environ.get("DC_DATA_DIR", "/var/lib/driver-companion"))
    p.add_argument("--print-speech", action="store_true",
                   help="print speech in the terminal instead of playing it (testing without a speaker)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


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


async def main(args):
    os.umask(0o077)
    settings = SettingsStore(os.path.join(args.data_dir, "settings.json"))
    speaker = Speaker(volume=settings.values["vol"], engine="" if args.print_speech else None)
    store = TripStore(os.path.join(args.data_dir, "trips.json"))
    bt = BluetoothService()
    gateway = Gateway(speaker, settings, send_status=bt.push_status,
                      send_trip=bt.push_trip, trips=store)
    bt.attach(gateway)
    trips = TripService(
        TripLogger(os.path.join(args.data_dir, "trip_current.json")), store,
        Summarizer(args.llm_bin, args.llm_model,
                   cache_path=os.path.join(args.data_dir, "llm_prompt.cache")), speaker,
        on_saved=gateway.sync_trips, halt_after_min=args.halt_after_min)
    if not args.simulate:
        gateway.on_fix = trips.add_fix  # real drives use the phone's GPS
    log.info("trip summaries by %s", "the LLM" if trips.summarizer.available
             else "the plain template (no LLM installed)")

    loop = asyncio.get_running_loop()
    background = [loop.create_task(speaker.run())]
    await bt.start()

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    # `sudo systemctl kill -s USR1 driver-companion-ble` also starts pairing.
    loop.add_signal_handler(signal.SIGUSR1, lambda: loop.create_task(bt.start_pairing()))
    button = None if args.no_button else setup_button(args.button_pin, loop, bt)

    ignition = None
    if args.simulate:
        log.info("starting a fake drive at %gx speed", args.sim_speed)
        background.append(loop.create_task(
            fake_drive(trips.ignition_changed, trips.add_fix, args.sim_speed)))
    elif args.no_ignition:
        trips.ignition_changed(True)
    else:
        ignition = Ignition(args.ignition_pin, trips.ignition_changed)
        background.append(loop.create_task(ignition.run()))

    if args.pair:
        await bt.start_pairing()

    await stop.wait()
    trips.save_now()
    for task in background:
        task.cancel()
    if button is not None:
        button.close()
    if ignition is not None:
        ignition.close()


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main(args))
