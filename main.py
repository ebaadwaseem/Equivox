"""Run: python main.py   (opens a browser; type commands in the terminal)

  transcript (audio/) -> page state (perception/) -> decision (backend/) -> action (executor/)
"""
import asyncio
import logging
import os
import time

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from audio.transcriber import TypedTranscriber
from backend import jev, policy
from backend.model import decide
from backend.scheduler import Scheduler
from executor.act import execute, highlight
from perception.perceive import get_page_state, shortlist, to_prompt_text
from shared.protocol import Decision, Transcript

load_dotenv()
START_URL = os.getenv("VB_START_URL", "https://news.ycombinator.com")

logging.basicConfig(level=os.getenv("VB_LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("main")

AFFIRM = {"yes", "yeah", "yep", "yup", "confirm", "do it", "sure", "go ahead", "ok", "okay"}
DENY = {"no", "nope", "nah", "cancel", "never mind", "nevermind", "stop", "don't"}


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

        recent_actions: list[str] = []
        pending: dict | None = None  # {"kind": "confirm"|"disambiguate", "decision": Decision, ...}

        async def run_action(d: Decision) -> None:
            if d.target_id:
                await highlight(page, d.target_id)
            try:
                result = await execute(page, d)
                print("  ✓", result)
                recent_actions.append(result)
                del recent_actions[:-5]
            except Exception as e:
                print("  ✗", e)

        async def handle(t: Transcript) -> None:
            nonlocal pending
            t_start = time.perf_counter()
            text_norm = t.text.strip().lower()

            if pending and pending["kind"] == "confirm":
                if text_norm in AFFIRM:
                    await run_action(pending["decision"])
                    pending = None
                    return
                if text_norm in DENY:
                    print("  ✗ cancelled")
                    pending = None
                    return
                pending = None  # user moved on; fall through and treat this as a new command

            if pending and pending["kind"] == "disambiguate":
                if text_norm.isdigit():
                    idx = int(text_norm) - 1
                    candidates = pending["candidates"]
                    if 0 <= idx < len(candidates):
                        d = pending["decision"]
                        d.target_id = candidates[idx].id
                        d.ambiguous = False
                        pending = None
                        await run_action(d)
                        return
                pending = None  # fall through and treat this as a new command

            state = await get_page_state(page)
            cands = shortlist(state, t.text)
            viewport = page.viewport_size
            vp = (viewport["width"], viewport["height"]) if viewport else None
            prompt_text = to_prompt_text(cands, vp)

            decide_t0 = time.perf_counter()
            d = await decide(t.text, t.is_final, prompt_text, cands, recent_actions)
            decide_ms = (time.perf_counter() - decide_t0) * 1000

            outcome = policy.decide_outcome(d, t.is_final)
            logger.info(
                "transcript=%r final=%s -> %s intent=%s target=%s conf=%.2f decide_ms=%.0f total_ms=%.0f",
                t.text, t.is_final, outcome, d.intent, d.target_id, d.confidence,
                decide_ms, (time.perf_counter() - t_start) * 1000,
            )

            if outcome in ("IGNORE", "WAIT"):
                return
            if outcome == "PREVIEW":
                if d.target_id:
                    await highlight(page, d.target_id)
                return
            if outcome == "CONFIRM":
                name = next((e.name for e in cands if e.id == d.target_id), d.target_id or d.intent)
                if d.target_id:
                    await highlight(page, d.target_id)
                print(f'  ⚠ "{name}" looks irreversible -- say "yes" to confirm or "no" to cancel.')
                pending = {"kind": "confirm", "decision": d}
                return
            if outcome == "DISAMBIGUATE":
                top = cands[:3]
                print("  ❓ Which one did you mean?")
                for i, e in enumerate(top, 1):
                    print(f'     {i}) {e.role} "{e.name}"')
                pending = {"kind": "disambiguate", "decision": d, "candidates": top}
                return
            if outcome == "COMMIT":
                await run_action(d)

        sched = Scheduler(handle)
        try:
            async for t in TypedTranscriber().stream():
                sched.submit(t)
                await asyncio.sleep(0)
        finally:
            await jev.aclose()


if __name__ == "__main__":
    asyncio.run(main())
