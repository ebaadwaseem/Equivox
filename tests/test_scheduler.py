import asyncio

from backend.scheduler import Scheduler
from shared.protocol import Transcript


def test_latest_wins_drops_superseded_partials():
    received: list[str] = []

    async def handler(t: Transcript) -> None:
        received.append(t.text)

    async def run():
        sched = Scheduler(handler, debounce_ms=50)
        sched.submit(Transcript(text="click sub", is_final=False))
        await asyncio.sleep(0.01)
        sched.submit(Transcript(text="click submit", is_final=False))
        await asyncio.sleep(0.2)  # let the debounced call land

    asyncio.run(run())
    assert received == ["click submit"]


def test_final_transcript_dispatches_without_waiting_for_debounce():
    received: list[str] = []

    async def handler(t: Transcript) -> None:
        received.append(t.text)

    async def run():
        sched = Scheduler(handler, debounce_ms=150)
        t0 = asyncio.get_event_loop().time()
        sched.submit(Transcript(text="click submit", is_final=True))
        await asyncio.sleep(0.02)  # much shorter than the debounce window
        return asyncio.get_event_loop().time() - t0

    elapsed = asyncio.run(run())
    assert received == ["click submit"]
    assert elapsed < 0.15


def test_multiple_rapid_partials_only_handler_runs_once():
    received: list[str] = []

    async def handler(t: Transcript) -> None:
        received.append(t.text)

    async def run():
        sched = Scheduler(handler, debounce_ms=50)
        for word in ["click", "click sub", "click submit", "click submit button"]:
            sched.submit(Transcript(text=word, is_final=False))
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.2)

    asyncio.run(run())
    assert received == ["click submit button"]
