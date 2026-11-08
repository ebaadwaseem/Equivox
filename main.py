"""Run: python main.py   (opens a browser; type commands in the terminal)

  transcript (audio/) -> page state (perception/) -> decision (backend/) -> action (executor/)
"""
import asyncio
import os

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from audio.transcriber import TypedTranscriber
from backend.model import decide
from backend.scheduler import Scheduler
from executor.act import execute, highlight
from perception.perceive import get_page_state, shortlist, to_prompt_text
from shared.protocol import Transcript

load_dotenv()
START_URL = os.getenv("VB_START_URL", "https://news.ycombinator.com")


async def main() -> None:
    async with async_playwright() as p:
        cdp = os.getenv("CDP_URL")  # e.g. http://localhost:9222 to attach to your real Chrome
        if cdp:
            browser = await p.chromium.connect_over_cdp(cdp)
            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        else:
            browser = await p.chromium.launch(headless=False)
            page = await browser.new_page()
            await page.goto(START_URL)

        async def handle(t: Transcript) -> None:
            state = await get_page_state(page)
            cands = shortlist(state, t.text)
            viewport = page.viewport_size
            vp = (viewport["width"], viewport["height"]) if viewport else None
            d = await decide(t.text, to_prompt_text(cands, vp), cands)
            print(f"  → {d}")
            if not (d.is_command and d.complete and t.is_final):
                return  # partial: later this is where PREVIEW (highlight only) goes
            if d.target_id:
                await highlight(page, d.target_id)
            try:
                print("  ✓", await execute(page, d))
            except Exception as e:
                print("  ✗", e)

        sched = Scheduler(handle)
        async for t in TypedTranscriber().stream():
            sched.submit(t)
            await asyncio.sleep(0)


if __name__ == "__main__":
    asyncio.run(main())
