"""Partials arrive fast. Debounce the newest one, allow at most two decision
requests in flight, abort only the oldest, and coalesce everything newer into a
single pending transcript that fires as soon as a slot frees. Finals skip the debounce.

The handler must only decide; browser actions live in their own worker in main.py so
cancelling a stale model request can never cancel an action.
"""
import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from shared.protocol import Transcript

logger = logging.getLogger("scheduler")


class Scheduler:
    def __init__(
        self,
        handler: Callable[[Transcript], Awaitable[None]],
        debounce_ms: int = 200,
        max_inflight: int = 2,
    ):
        self.handler = handler
        self.debounce = debounce_ms / 1000
        self.max_inflight = max_inflight
        self._pending: Optional[Transcript] = None
        self._delay_task: Optional[asyncio.Task] = None
        self._inflight: list[asyncio.Task] = []

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    def submit(self, t: Transcript, force: bool = False) -> None:
        self._pending = t
        if self._delay_task and not self._delay_task.done():
            self._delay_task.cancel()
        delay = 0.0 if (force or t.is_final) else self.debounce
        self._delay_task = asyncio.create_task(self._delayed(t, delay))

    async def _delayed(self, t: Transcript, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        if self._delay_task is asyncio.current_task():
            self._delay_task = None
        if self._pending is not t:
            logger.debug("scheduler: dropped superseded transcript %r", t.text)
            return
        self._dispatch()

    def _dispatch(self) -> None:
        if not self._pending:
            return
        while len(self._inflight) >= self.max_inflight:
            oldest = self._inflight.pop(0)
            oldest.cancel()
            logger.debug("scheduler: aborted oldest in-flight request")
        t = self._pending
        self._pending = None
        logger.debug("scheduler: dispatching %r (final=%s) at %.0f ms", t.text, t.is_final, time.monotonic() * 1000)
        task = asyncio.create_task(self._run(t))
        self._inflight.append(task)

    async def _run(self, t: Transcript) -> None:
        try:
            await self.handler(t)
        finally:
            current = asyncio.current_task()
            if current in self._inflight:
                self._inflight.remove(current)
            if self._pending and len(self._inflight) < self.max_inflight and self._delay_task is None:
                self._dispatch()
