"""Speaks alerts one at a time, most important first.

This is a minimal alert queue for the Bluetooth checkpoint. The full alert
manager (sign and overspeed alerts, grouping messages) builds on it.
"""

import asyncio
import enum
import itertools
import logging
import shutil
import subprocess
import time

log = logging.getLogger(__name__)

MESSAGE_GAP_S = 5.0                      # REQ-17: gap before informational alerts
VOLUME_AMPLITUDE = {0: 60, 1: 120, 2: 200}  # espeak-ng amplitude per volume level


class Priority(enum.IntEnum):
    OVERSPEED = 0
    SIGN = 1
    CALL = 2
    MESSAGE = 3
    SYSTEM = 4


class Speaker:
    def __init__(self, volume: int = 1, engine=None, clock=time.monotonic,
                 message_gap_s: float = MESSAGE_GAP_S):
        self._queue = asyncio.PriorityQueue()
        self._order = itertools.count()
        self._volume = volume
        self._clock = clock
        self._gap = message_gap_s
        self._last_end = float("-inf")
        self._engine = engine if engine is not None else shutil.which("espeak-ng")
        if not self._engine:
            log.warning("no speech engine in use; speech will be printed instead")

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
            await self.speak(text)
            self._last_end = self._clock()

    async def speak(self, text: str):
        if not self._engine:
            print(f"[SAY] {text}", flush=True)
            return
        proc = await asyncio.create_subprocess_exec(
            self._engine, "-s", "160", "-a", str(VOLUME_AMPLITUDE[self._volume]), "--", text,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await proc.wait()
