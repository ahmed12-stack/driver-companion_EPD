"""Speaks alerts one at a time, most important first.

The voice itself (Piper, espeak or the log) is in voices.py.
"""

import asyncio
import enum
import itertools
import logging
import time

from .voices import EspeakVoice

log = logging.getLogger(__name__)

MESSAGE_GAP_S = 5.0  # REQ-17: gap before informational alerts


class Priority(enum.IntEnum):
    OVERSPEED = 0
    SIGN = 1
    CALL = 2
    MESSAGE = 3
    SYSTEM = 4


# Message and call text is personal: never kept on disk as audio.
PRIVATE = (Priority.CALL, Priority.MESSAGE)


class Speaker:
    def __init__(self, voice=None, volume: int = 1, clock=time.monotonic,
                 message_gap_s: float = MESSAGE_GAP_S):
        self._queue = asyncio.PriorityQueue()
        self._order = itertools.count()
        self._volume = volume
        self._clock = clock
        self._gap = message_gap_s
        self._last_end = float("-inf")
        self.voice = voice or EspeakVoice()
        self.speaking = False

    @property
    def idle(self) -> bool:
        return not self.speaking and self._queue.empty()

    def set_volume(self, level: int):
        self._volume = level

    def say(self, text: str, priority: Priority = Priority.SYSTEM, max_age=None):
        """Queue text. Items older than max_age seconds are dropped unspoken."""
        self._queue.put_nowait((int(priority), next(self._order), self._clock(), max_age, text))

    async def run(self):
        while True:
            item = await self._queue.get()
            priority, _, queued_at, max_age, text = item
            now = self._clock()
            if max_age is not None and now - queued_at > max_age:
                log.info("dropped stale alert (priority %d)", priority)
                continue
            if priority == Priority.MESSAGE:
                wait = self._last_end + self._gap - now
                if wait > 0:
                    # Put it back so anything more important can go first.
                    self._queue.put_nowait(item)
                    await asyncio.sleep(min(wait, 0.25))
                    continue
            self.speaking = True
            try:
                await self.speak(text, Priority(priority))
            except Exception:
                log.exception("could not speak")
            finally:
                self.speaking = False
            self._last_end = self._clock()

    async def speak(self, text: str, priority: Priority = Priority.SYSTEM):
        await self.voice.speak(text, self._volume, keep=priority not in PRIVATE)
