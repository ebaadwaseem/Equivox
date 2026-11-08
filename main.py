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
from executor import act
from executor.act import execute, highlight
from perception.perceive import get_page_state, shortlist, to_prompt_text
from shared.protocol import Decision, Element, Transcript
from ui import vox

load_dotenv()
START_URL = os.getenv("VB_START_URL", "https://news.ycombinator.com")

logging.basicConfig(level=os.getenv("VB_LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("main")

AFFIRM = {"yes", "yeah", "yep", "yup", "confirm", "do it", "sure", "go ahead", "ok", "okay"}
DENY = {"no", "nope", "nah", "cancel", "never mind", "nevermind", "stop", "don't"}
TAB_INTENTS = {"new_tab", "close_tab", "switch_tab"}


def _target_name(d: Decision, cands: list[Element]) -> str:
    return next((e.name for e in cands if e.id == d.target_id), d.target_id or "")


async def main() -> None:
    async with async_playwright() as p:
        cdp = os.getenv("CDP_URL")  # e.g. http://localhost:9222 to attach to your real Chrome
        if cdp:
            browser = await p.chromium.connect_over_cdp(cdp)
            ctx = browser.contexts[0]
        else:
            browser = await p.chromium.launch(headless=False)
            # browser.new_page() is a shortcut that creates a context which can't have
            # more tabs added to it later ("Please use browser.new_context()") -- tab
            # management needs a real context we can call .new_page() on repeatedly.
            ctx = await browser.new_context()

        active_page = None  # tab commands can change which Page subsequent commands act on
        recent_actions: list[str] = []
        pending: dict | None = None  # {"kind": "confirm"|"disambiguate", "decision": Decision, ...}

        async def run_action(d: Decision, target_name: str = "") -> None:
            nonlocal active_page
            if d.intent in TAB_INTENTS:
                try:
                    if d.intent == "new_tab":
                        active_page = await act.new_tab(ctx, d.text)
                        print("  ✓ opened new tab", active_page.url)
                    elif d.intent == "close_tab":
                        active_page = await act.close_tab(ctx, active_page)
                        print("  ✓ closed tab, now on", active_page.url)
                    elif d.intent == "switch_tab":
                        active_page = await act.switch_tab(ctx, active_page, d.text or "next")
                        print("  ✓ switched tab ->", active_page.url)
                    recent_actions.append(f"did {d.intent} {d.text or ''}".strip())
                    del recent_actions[:-5]
                    await vox.set_then_idle_after(active_page, "acting", vox.describe_action(d, target_name))
                except Exception as e:
                    print("  ✗", e)
                    await vox.set(active_page, "error", "Hmm, that didn't work")
                return
            if d.target_id:
                await highlight(active_page, d.target_id)
            try:
                result = await execute(active_page, d)
                print("  ✓", result)
                recent_actions.append(result)
                del recent_actions[:-5]
                if result.startswith("no match") or result.startswith("could not find") or result.startswith("skipped"):
                    await vox.set_then_idle_after(active_page, "error", result[:60])
                else:
                    await vox.set_then_idle_after(active_page, "acting", vox.describe_action(d, target_name))
            except Exception as e:
                print("  ✗", e)
                await vox.set(active_page, "error", "Hmm, that didn't work")

        async def on_vox_command(text: str) -> None:
            sched.submit(Transcript(text=text, is_final=True))

        async def handle(t: Transcript) -> None:
            nonlocal pending
            t_start = time.perf_counter()
            text_norm = t.text.strip().lower()

            await vox.set(active_page, "listening", t.text)

            if pending and pending["kind"] == "confirm":
                if text_norm in AFFIRM:
                    await run_action(pending["decision"], pending.get("target_name", ""))
                    pending = None
                    return
                if text_norm in DENY:
                    print("  ✗ cancelled")
                    await vox.set(active_page, "idle")
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
                        await run_action(d, candidates[idx].name)
                        return
                pending = None  # fall through and treat this as a new command

            await vox.set(active_page, "thinking")
            state = await get_page_state(active_page)
            cands = shortlist(state, t.text)
            viewport = active_page.viewport_size
            vp = (viewport["width"], viewport["height"]) if viewport else None
            prompt_text = to_prompt_text(cands, vp)

            decide_t0 = time.perf_counter()
            d = await decide(t.text, t.is_final, prompt_text, cands, recent_actions)
            decide_ms = (time.perf_counter() - decide_t0) * 1000

            outcome = policy.decide_outcome(d, t.is_final)
            target_name = _target_name(d, cands)
            logger.info(
                "transcript=%r final=%s -> %s intent=%s target=%s conf=%.2f decide_ms=%.0f total_ms=%.0f",
                t.text, t.is_final, outcome, d.intent, d.target_id, d.confidence,
                decide_ms, (time.perf_counter() - t_start) * 1000,
            )

            if outcome == "IGNORE":
                await vox.set(active_page, "idle")
                return
            if outcome == "WAIT":
                return  # still "thinking" from above; nothing new to show yet
            if outcome == "PREVIEW":
                if d.target_id:
                    await highlight(active_page, d.target_id)
                    await vox.set(active_page, "thinking", f"Looking for {target_name}…")
                return
            if outcome == "CONFIRM":
                if d.target_id:
                    await highlight(active_page, d.target_id)
                print(f'  ⚠ "{target_name}" looks irreversible -- say "yes" to confirm or "no" to cancel.')
                await vox.set(active_page, "unsure", f"{target_name}? Say yes or no")
                pending = {"kind": "confirm", "decision": d, "target_name": target_name}
                return
            if outcome == "DISAMBIGUATE":
                top = cands[:3]
                print("  ❓ Which one did you mean?")
                for i, e in enumerate(top, 1):
                    print(f'     {i}) {e.role} "{e.name}"')
                options = "   ".join(f"{i} · {e.name}" for i, e in enumerate(top, 1))
                await vox.set(active_page, "unsure", f"Did you mean: {options}")
                pending = {"kind": "disambiguate", "decision": d, "candidates": top}
                return
            if outcome == "COMMIT":
                await run_action(d, target_name)

        sched = Scheduler(handle)
        # install the overlay's init-script BEFORE creating/navigating the first page --
        # add_init_script only affects pages/navigations registered after this point, so
        # doing this after the initial page.goto() would miss the first page entirely.
        await vox.install(ctx, on_command=on_vox_command)

        if cdp:
            active_page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        else:
            active_page = await ctx.new_page()
            await active_page.goto(START_URL)

        try:
            async for t in TypedTranscriber().stream():
                sched.submit(t)
                await asyncio.sleep(0)
        finally:
            await jev.aclose()


if __name__ == "__main__":
    asyncio.run(main())
