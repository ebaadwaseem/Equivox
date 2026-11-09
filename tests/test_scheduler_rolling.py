import asyncio

from backend.scheduler import Scheduler
from shared.protocol import Transcript


def test_at_most_two_requests_in_flight_and_newest_is_used():
    active = 0
    max_active = 0
    received: list[str] = []

    async def handler(t: Transcript) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        received.append(t.text)
        try:
            await asyncio.sleep(0.1)
        finally:
            active -= 1

    async def run():
        sched = Scheduler(handler, debounce_ms=5)
        for word in ["click", "click sub", "click submit", "click submit button"]:
            sched.submit(Transcript(text=word, is_final=False))
            await asyncio.sleep(0.01)
        assert max_active <= 2
        await asyncio.sleep(0.5)
        assert max_active <= 2

    asyncio.run(run())
    assert received
    assert received[-1] == "click submit button"


def test_force_skips_the_debounce_for_silence_retry():
    received: list[str] = []

    async def handler(t: Transcript) -> None:
        received.append(t.text)

    async def run():
        sched = Scheduler(handler, debounce_ms=500)
        sched.submit(Transcript(text="search for pizza", is_final=False, silent_ms=900), force=True)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert received == ["search for pizza"]
