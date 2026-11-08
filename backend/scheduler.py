"""PERSON 3: scheduler.

Partials arrive fast. Wait ~150 ms after the last one, keep only one model call
in flight, and if newer text arrives, the newest wins and stale results are dropped.
Finals skip the debounce and dispatch immediately.
"""
import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from shared.protocol import Transcript

logger = logging.getLogger("scheduler")


class Scheduler:
    def __init__(self, handler: Callable[[Transcript], Awaitable[None]], debounce_ms: int = 150):
        self.handler = handler
        self.debounce = debounce_ms / 1000
        self._version = 0
        self._task: Optional[asyncio.Task] = None

    def submit(self, t: Transcript) -> None:
        self._version += 1
        version = self._version
        submit_ts = time.perf_counter()
        if self._task and not self._task.done():
            self._task.cancel()  # latest wins: abandon whatever was in flight
        self._task = asyncio.create_task(self._run(version, t, submit_ts))

    async def _run(self, version: int, t: Transcript, submit_ts: float) -> None:
        if not t.is_final:
            await asyncio.sleep(self.debounce)
        if version != self._version:
            logger.debug("scheduler: dropped stale transcript %r (v%d superseded by v%d)", t.text, version, self._version)
            return
        wait_ms = (time.perf_counter() - submit_ts) * 1000
        logger.debug("scheduler: dispatching %r (final=%s) after %.0f ms", t.text, t.is_final, wait_ms)
        await self.handler(t)
