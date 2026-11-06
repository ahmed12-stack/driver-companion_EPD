import asyncio
import os
import stat
import sys
import types
from array import array

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from dc_common import phrases  # noqa: E402
from dc_speech import __main__ as speech  # noqa: E402
from dc_speech.speaker import Priority, Speaker  # noqa: E402
from dc_speech.voices import EspeakVoice, PiperVoice, scale_pcm16, split_sentences  # noqa: E402

FAKE_PIPER = """#!/usr/bin/env python3
import os, sys, time, wave
out = sys.argv[sys.argv.index("--output_file") + 1]
text = sys.stdin.read().strip()
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write("make " + text + "\\n")
if "FAIL" in text:
    sys.exit(1)
time.sleep(0.05)
w = wave.open(out, "wb")
w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
w.writeframes(b"\\x00\\x10" * 2205)
w.close()
"""

FAKE_APLAY = """#!/usr/bin/env python3
import os, sys
data = sys.stdin.buffer.read()
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write("play %d\\n" % len(data))
"""


class Recorder:
    name = "recorder"

    def __init__(self):
        self.said = []

    async def speak(self, text, volume, keep=True):
        self.said.append(text)


def script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def piper(tmp_path, monkeypatch):
    log = tmp_path / "log.txt"
    log.write_text("")
    monkeypatch.setenv("FAKE_LOG", str(log))
    model = tmp_path / "en_GB-cori-medium.onnx"
    model.write_bytes(b"model")
    voice = PiperVoice(script(tmp_path / "piper", FAKE_PIPER), str(model), str(tmp_path / "cache"),
                       Recorder(), max_kept=2, player=script(tmp_path / "aplay", FAKE_APLAY))
    voice.log_lines = lambda: log.read_text().splitlines()
    return voice


def test_split_sentences():
    assert split_sentences("Trip complete. You drove 6 km. Nice.") == ["Trip complete.", "You drove 6 km. Nice."]
    assert split_sentences("Hi. There you are!") == ["Hi. There you are!"]
    assert split_sentences("Speed limit 50.") == ["Speed limit 50."]


def test_fixed_phrase_made_once_then_instant(piper):
    assert asyncio.run(piper.prepare("Speed limit 50."))
    asyncio.run(piper.speak("Speed limit 50.", 2))
    asyncio.run(piper.speak("Speed limit 50.", 2))
    lines = piper.log_lines()
    assert lines.count("make Speed limit 50.") == 1
    assert lines.count("play 4410") == 2


def test_message_text_never_kept(piper):
    asyncio.run(piper.speak("WhatsApp from Abhay: on my way.", 2, keep=False))
    assert [n for n in os.listdir(piper.cache_dir)] == []
    assert "play 4410" in piper.log_lines()


def test_sentences_made_and_played_in_order(piper):
    asyncio.run(piper.speak("Trip complete, you drove far. Take a moment to relax.", 2))
    lines = piper.log_lines()
    makes = [line for line in lines if line.startswith("make")]
    assert makes == ["make Trip complete, you drove far.", "make Take a moment to relax."]
    assert lines.count("play 4410") == 2


def test_piper_failure_falls_back_to_espeak(piper):
    asyncio.run(piper.speak("This will FAIL. This works fine.", 2))
    assert piper.fallback.said == ["This will FAIL."]
    assert piper.log_lines().count("play 4410") == 1


def test_old_clips_thrown_away_but_fixed_kept(piper):
    asyncio.run(piper.prepare("Pairing mode on."))
    for i in range(5):
        asyncio.run(piper.speak(f"Sentence number {i} here.", 2))
    kept = [n for n in os.listdir(piper.cache_dir) if n.endswith(".wav")]
    assert len(kept) == 3  # 2 recent sentences + the fixed phrase
    assert piper.is_ready("Pairing mode on.")


def test_volume_levels():
    loud = array("h", [10000, -10000]).tobytes()
    quiet = array("h", scale_pcm16(loud, 0.5))
    assert list(quiet) == [5000, -5000]
    assert scale_pcm16(loud, 1.0) == loud


def test_speaker_keeps_private_text_off_disk():
    rec = []

    class Voice:
        async def speak(self, text, volume, keep=True):
            rec.append((text, keep))

    async def go():
        sp = Speaker(Voice(), message_gap_s=0)
        task = asyncio.ensure_future(sp.run())
        sp.say("Speed limit 50.", Priority.SIGN)
        sp.say("Message from Abhay", Priority.MESSAGE)
        sp.say("Incoming call from Mom", Priority.CALL)
        for _ in range(50):
            if len(rec) == 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()
    asyncio.run(go())
    assert rec == [("Speed limit 50.", True), ("Incoming call from Mom", False),
                   ("Message from Abhay", False)]


def test_voice_choice(tmp_path):
    args = types.SimpleNamespace(print_speech=False, engine="auto", data_dir=str(tmp_path),
                                 piper=str(tmp_path / "nope"), voice=str(tmp_path / "nope.onnx"))
    assert isinstance(speech.make_voice(args), EspeakVoice)
    args.print_speech = True
    assert speech.make_voice(args).name == "print"


def test_prepares_all_fixed_phrases_when_idle(piper, monkeypatch):
    monkeypatch.setattr(speech, "PREPARE_START_S", 0)
    monkeypatch.setattr(phrases, "fixed_phrases", lambda: ["Speed limit 50.", "No entry."])

    async def go():
        sp = Speaker(piper)
        await speech.prepare_phrases(sp, piper)
    asyncio.run(go())
    assert piper.is_ready("Speed limit 50.") and piper.is_ready("No entry.")
    assert len(piper.fixed) == 2
