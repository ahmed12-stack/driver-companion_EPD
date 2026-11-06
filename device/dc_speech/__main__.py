"""python -m dc_speech [--engine auto|piper|espeak] [--voice MODEL.onnx] [--print-speech]"""

import asyncio
import logging
import os

from dc_common import module, phrases, topics
from dc_common.text import clean_text

from .speaker import Priority, Speaker
from .voices import EspeakVoice, PiperVoice, PrintVoice

log = logging.getLogger("dc_speech")

PRIORITY = {
    "overspeed": Priority.OVERSPEED,
    "sign": Priority.SIGN,
    "call": Priority.CALL,
    "message": Priority.MESSAGE,
    "system": Priority.SYSTEM,
}
MAX_TEXT = 400
PIPER_DIR = "/opt/driver-companion/piper"
PREPARE_START_S = 5   # let the start-up speech go first


def add_args(p):
    p.add_argument("--print-speech", action="store_true",
                   help="print speech in the log instead of playing it (no speaker)")
    p.add_argument("--engine", choices=("auto", "piper", "espeak"), default="auto",
                   help="auto: Piper if it is installed, else espeak")
    p.add_argument("--piper", default=os.path.join(PIPER_DIR, "piper", "piper"), help="Piper program")
    p.add_argument("--voice", default=os.path.join(PIPER_DIR, "voices", "en_GB-cori-medium.onnx"),
                   help="Piper voice model (.onnx)")


def make_voice(args):
    if args.print_speech:
        return PrintVoice()
    espeak = EspeakVoice()
    if args.engine == "espeak":
        return espeak
    piper = PiperVoice(args.piper, args.voice, os.path.join(args.data_dir, "voice_cache"), espeak)
    if piper.available:
        log.info("voice: Piper, %s", piper.voice_id)
        return piper
    log.warning("Piper or its voice not found (%s, %s); using espeak", args.piper, args.voice)
    return espeak


async def prepare_phrases(speaker, voice):
    """Make the fixed phrases in advance, one at a time, only while nothing is being said."""
    await asyncio.sleep(PREPARE_START_S)
    todo = phrases.fixed_phrases()
    made = 0
    for text in todo:
        while not speaker.idle:
            await asyncio.sleep(0.5)
        if not voice.is_ready(text):
            if await voice.prepare(text):
                made += 1
            elif voice.failures >= 3:
                log.warning("Piper keeps failing; stopped preparing phrases")
                return
        else:
            voice.fixed.add(voice.key(text))
    log.info("fixed phrases ready (%d made now, %d in all)", made, len(todo))


def make_handlers(speaker):
    def on_say(topic, msg):
        if not isinstance(msg, dict):
            return
        text = clean_text(msg.get("text"), MAX_TEXT)
        if not text:
            return
        priority = PRIORITY.get(msg.get("priority"), Priority.SYSTEM)
        max_age = msg.get("max_age")
        if not isinstance(max_age, (int, float)) or isinstance(max_age, bool) or max_age <= 0:
            max_age = None
        speaker.say(text, priority, max_age)

    def on_settings(topic, settings):
        if isinstance(settings, dict) and settings.get("vol") in (0, 1, 2) \
                and not isinstance(settings.get("vol"), bool):
            speaker.set_volume(settings["vol"])

    return on_say, on_settings


async def setup(bus, args):
    voice = make_voice(args)
    speaker = Speaker(voice)
    on_say, on_settings = make_handlers(speaker)
    bus.subscribe(topics.SAY, on_say)
    bus.subscribe(topics.SETTINGS, on_settings)
    loop = asyncio.get_running_loop()
    tasks = [loop.create_task(speaker.run())]
    if isinstance(voice, PiperVoice):
        tasks.append(loop.create_task(prepare_phrases(speaker, voice)))

    async def cleanup():
        for t in tasks:
            t.cancel()
    return cleanup


if __name__ == "__main__":
    module.main("dc_speech", setup, add_args)
