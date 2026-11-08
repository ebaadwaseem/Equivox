"""PERSON 3: scheduler.

Partials arrive fast. Wait ~150 ms after the last one, keep only one model call
in flight, and if newer text arrives, the newest wins and stale results are dropped.
Right now main.py only sends finals, so this is ready for when audio streams partials.
"""
import asyncio
from typing import Awaitable, Callable

from shared.protocol import Transcript


class Scheduler:
    def __init__(self, handler: Callable[[Transcript], Awaitable[None]], debounce_ms: int = 150):
        self.handler = handler
        self.debounce = debounce_ms / 1000
        self._latest: Transcript | None = None
        self._version = 0
        self._task: asyncio.Task | None = None

    def submit(self, t: Transcript) -> None:
        self._latest = t
        self._version += 1
        if self._task and not self._task.done():
            self._task.cancel()          # latest wins
        self._task = asyncio.create_task(self._run(self._version, t.is_final))

    async def _run(self, version: int, is_final: bool) -> None:
        if not is_final:
            await asyncio.sleep(self.debounce)
        if version == self._version and self._latest:
            await self.handler(self._latest)
