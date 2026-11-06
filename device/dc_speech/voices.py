"""The voices the speaker can use.

PiperVoice  natural voice (Piper, offline). Slow to make new sentences on a
            Pi 3B+ (several seconds), so:
            - fixed phrases (road signs, system messages) are made in advance
              and kept, so they start at once (REQ-02: sign alert within 1 s);
            - other sentences are made one at a time while the previous one
              plays;
            - message and call text is never kept on disk (privacy).
EspeakVoice the old robotic voice; always available, used when Piper is
            missing or fails.
PrintVoice  writes speech to the log (no speaker).
"""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import uuid
import wave
from array import array

log = logging.getLogger(__name__)


class PrintVoice:
    name = "print"

    async def speak(self, text: str, volume: int, keep: bool = True):
        print(f"[SAY] {text}", flush=True)


class EspeakVoice:
    name = "espeak"
    AMPLITUDE = {0: 60, 1: 120, 2: 200}
    RATE = 145  # words per minute; clearer than the default 175

    def __init__(self, binary=None):
        self.binary = binary or shutil.which("espeak-ng") or shutil.which("espeak")

    @property
    def available(self) -> bool:
        return bool(self.binary)

    async def speak(self, text: str, volume: int, keep: bool = True):
        if not self.binary:
            print(f"[SAY] {text}", flush=True)
            return
        proc = await asyncio.create_subprocess_exec(
            self.binary, "-s", str(self.RATE), "-a", str(self.AMPLITUDE.get(volume, 120)), "--", text,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str, min_len: int = 12) -> list:
    """Sentences, with very short ones joined to their neighbour (fewer, longer
    pieces sound more natural and waste less time starting Piper)."""
    out = []
    for part in _SENTENCE_END.split(text.strip()):
        part = part.strip()
        if not part:
            continue
        if out and (len(out[-1]) < min_len or len(part) < min_len):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out


def scale_pcm16(data: bytes, factor: float) -> bytes:
    """Make 16-bit audio quieter (volume levels)."""
    if factor >= 0.999:
        return data
    try:
        import audioop  # Python 3.11 on the Pi: fast
        return audioop.mul(data, 2, factor)
    except ImportError:
        pass
    try:
        import numpy as np
        a = np.frombuffer(data, dtype="<i2").astype(np.float32) * factor
        return a.astype("<i2").tobytes()
    except ImportError:
        a = array("h", data)
        for i in range(len(a)):
            a[i] = int(a[i] * factor)
        return a.tobytes()


class PiperVoice:
    name = "piper"
    VOLUME = {0: 0.35, 1: 0.65, 2: 1.0}

    def __init__(self, binary: str, model: str, cache_dir: str, fallback,
                 max_kept: int = 100, timeout_s: float = 45, player: str = "aplay"):
        self.binary = binary
        self.model = model
        self.cache_dir = cache_dir
        self.fallback = fallback
        self.max_kept = max_kept      # kept clips of non-fixed sentences
        self.timeout_s = timeout_s
        self.player = player
        self.voice_id = os.path.basename(model)
        self.fixed = set()            # cache keys of fixed phrases (never thrown away)
        self.failures = 0
        os.makedirs(cache_dir, exist_ok=True)

    @property
    def available(self) -> bool:
        return os.access(self.binary, os.X_OK) and os.path.isfile(self.model)

    # ----- clips -------------------------------------------------------------
    def key(self, text: str) -> str:
        return hashlib.sha1(f"{self.voice_id}\n{text}".encode("utf-8")).hexdigest()[:24]

    def path_for(self, text: str) -> str:
        return os.path.join(self.cache_dir, self.key(text) + ".wav")

    def is_ready(self, text: str) -> bool:
        return os.path.isfile(self.path_for(text))

    async def make(self, text: str, dest: str) -> bool:
        """Run Piper once for one sentence; write the clip to dest."""
        tmp = os.path.join(self.cache_dir, f".part-{uuid.uuid4().hex}.wav")
        try:
            proc = await asyncio.create_subprocess_exec(
                "nice", "-n", "5", self.binary, "--model", self.model, "--output_file", tmp,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
            try:
                _, err = await asyncio.wait_for(proc.communicate(text.encode("utf-8") + b"\n"),
                                                self.timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                log.warning("Piper took over %.0f s; gave up on this sentence", self.timeout_s)
                return False
            if proc.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) <= 44:
                log.warning("Piper failed (exit %s): %s", proc.returncode,
                            err.decode("utf-8", "replace").strip()[-300:])
                return False
            os.replace(tmp, dest)
            return True
        except OSError as e:
            log.warning("cannot run Piper: %s", e)
            return False
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    async def clip(self, text: str, keep: bool):
        """A clip for this sentence: kept/cached if keep, else a throw-away file. None if Piper failed."""
        path = self.path_for(text)
        if os.path.isfile(path):
            try:
                os.utime(path)  # recently used: kept longer
            except OSError:
                pass
            return path, False
        dest = path if keep else os.path.join(self.cache_dir, f".once-{uuid.uuid4().hex}.wav")
        if await self.make(text, dest):
            self.failures = 0
            if keep:
                self._trim()
            return dest, not keep
        self.failures += 1
        return None, False

    async def prepare(self, text: str) -> bool:
        """Make a fixed phrase in advance (it is then never thrown away)."""
        self.fixed.add(self.key(text))
        if self.is_ready(text):
            return True
        ok, _ = await self.clip(text, keep=True)
        return ok is not None

    def _trim(self):
        clips = []
        for name in os.listdir(self.cache_dir):
            if name.endswith(".wav") and not name.startswith(".") and name[:-4] not in self.fixed:
                full = os.path.join(self.cache_dir, name)
                try:
                    clips.append((os.path.getmtime(full), full))
                except OSError:
                    pass
        clips.sort()
        for _, full in clips[:max(0, len(clips) - self.max_kept)]:
            try:
                os.remove(full)
            except OSError:
                pass

    # ----- speaking ---------------------------------------------------------------
    async def speak(self, text: str, volume: int, keep: bool = True):
        parts = split_sentences(text) or [text]
        pending = asyncio.ensure_future(self.clip(parts[0], keep))
        try:
            for i, part in enumerate(parts):
                path, throwaway = await pending
                # make the next sentence while this one plays
                pending = asyncio.ensure_future(self.clip(parts[i + 1], keep)) if i + 1 < len(parts) else None
                if path is None:
                    await self.fallback.speak(part, volume, keep)
                    continue
                try:
                    await self.play(path, volume)
                finally:
                    if throwaway:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
        finally:
            if pending is not None and not pending.done():
                pending.cancel()

    async def play(self, path: str, volume: int):
        with wave.open(path, "rb") as w:
            rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            data = w.readframes(w.getnframes())
        if width != 2:
            raise ValueError(f"{path}: expected 16-bit audio")
        data = scale_pcm16(data, self.VOLUME.get(volume, 0.65))
        proc = await asyncio.create_subprocess_exec(
            self.player, "-q", "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", str(channels), "-",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)
        try:
            proc.stdin.write(data)
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass
        await proc.wait()
