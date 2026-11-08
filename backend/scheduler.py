"""Leading-window coalescing without cancelling useful requests or actions.

A continuous stream cannot postpone the first dispatch. While slots are occupied,
keep the newest update per physical utterance (including separate final commands).
"""
import asyncio
import logging
from collections import OrderedDict
from typing import Awaitable, Callable

from shared.protocol import Transcript

logger = logging.getLogger("scheduler")


class Scheduler:
    def __init__(self, handler: Callable[[Transcript], Awaitable[None]],
                 debounce_ms: int = 80, max_inflight: int = 1):
        self.handler = handler
        self.debounce = debounce_ms / 1000
        self.max_inflight = max_inflight
        self._pending = OrderedDict()
        self._delay_task = None
        self._inflight = set()
        self._serial = 0

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    def submit(self, t: Transcript, force: bool = False) -> None:
        self._serial += 1
        key = t.id if t.id is not None else ("typed", self._serial) if t.is_final else "partial"
        self._pending[key] = t
        if force or t.is_final:
            if self._delay_task:
                self._delay_task.cancel()
                self._delay_task = None
            self._dispatch()
        elif self._delay_task is None:
            self._delay_task = asyncio.create_task(self._delayed())

    async def _delayed(self) -> None:
        await asyncio.sleep(self.debounce)
        self._delay_task = None
        self._dispatch()

    def _dispatch(self) -> None:
        while self._pending and len(self._inflight) < self.max_inflight:
            _, t = self._pending.popitem(last=False)
            task = asyncio.create_task(self._run(t))
            self._inflight.add(task)

    async def _run(self, t: Transcript) -> None:
        try:
            await self.handler(t)
        except Exception:
            logger.exception("decision failed for %r", t.text)
        finally:
            self._inflight.discard(asyncio.current_task())
            if self._delay_task is None:
                self._dispatch()

    async def aclose(self) -> None:
        tasks = list(self._inflight)
        if self._delay_task:
            tasks.append(self._delay_task)
        self._pending.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
