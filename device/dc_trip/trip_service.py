"""Ties the trip pieces together: ignition, GPS fixes, summary, speech.

The module wires `say` to alert/say and `on_saved` to trip/saved, so this file
knows nothing about the message board and can be tested on its own."""

import asyncio
import logging

from .summary import facts_sentence

log = logging.getLogger(__name__)


class TripService:
    def __init__(self, logger, store, summarizer, say, on_saved, halt_after_min: float = 0,
                 halt=None):
        self.logger = logger
        self.store = store
        self.summarizer = summarizer
        self.say = say            # say(text): speak a system message
        self.on_saved = on_saved
        self.halt_after_min = halt_after_min
        self._halt = halt or _poweroff
        self._lock = asyncio.Lock()  # one LLM run at a time (the Pi has 1 GB)
        self._halt_task = None
        self._tasks = set()

    def ignition_changed(self, on: bool):
        if on:
            self._cancel_halt()
            self.logger.start()
            return
        if self.logger.pending is not None:
            self._spawn(self._complete(self.logger.close_pending(), speak=False))
        if self.logger.active:
            trip = self.logger.finish("ignition")
            if trip is None:
                log.info("trip too short, not kept")
            else:
                self._spawn(self._complete(trip, speak=True))
        if self.halt_after_min > 0:
            self._cancel_halt()
            self._halt_task = asyncio.get_running_loop().create_task(self._halt_later())

    def add_fix(self, lat, lon, kmh, acc, ts):
        closed = self.logger.add_fix(lat, lon, kmh, acc, ts)
        if closed is not None:
            self._spawn(self._complete(closed, speak=False))

    def record(self, event: str, seconds: float = 0.0, counted: bool = True):
        self.logger.record(event, seconds, counted)

    def save_now(self):
        """Before the program exits: keep the running trip so it can resume."""
        self.logger.checkpoint(force=True)

    async def wait_idle(self):
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _spawn(self, coro):
        if coro is None:
            return
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _complete(self, trip, speak: bool):
        if trip is None:
            return
        facts = facts_sentence(trip)
        if speak:
            self.say(facts)  # the numbers, straight away
        async with self._lock:
            remark = await self.summarizer.remark(trip)  # about 10 s on the Pi
        if remark:
            trip["summary"], trip["summary_by"] = f"{facts} {remark}", "llm"
            if speak:
                self.say(remark)
        else:
            trip["summary"], trip["summary_by"] = facts, "template"
        self.store.add(trip)
        log.info("trip %s saved (%.1f km, summary by %s)", trip["id"], trip["km"],
                 trip["summary_by"])
        self.on_saved(trip)

    def _cancel_halt(self):
        if self._halt_task is not None:
            self._halt_task.cancel()
            self._halt_task = None

    async def _halt_later(self):
        await asyncio.sleep(self.halt_after_min * 60)
        await self.wait_idle()
        async with self._lock:
            pass
        log.info("ignition off for %.0f min: shutting down", self.halt_after_min)
        await self._halt()


async def _poweroff():
    proc = await asyncio.create_subprocess_exec("systemctl", "poweroff")
    await proc.wait()
