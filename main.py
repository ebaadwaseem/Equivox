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
from executor import act, undo
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
UNDO_PHRASES = {"go back", "back", "undo", "undo that"}
TAB_INTENTS = {"new_tab", "close_tab", "switch_tab"}
NAV_INTENTS = {"click", "navigate", "search"}
ERROR_REVERT_S = 4.0


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
            # bypass_csp: some sites' Content-Security-Policy (e.g. html.duckduckgo.com)
            # blocks ALL inline styling -- both <style> tags and style="" attributes -- for
            # every script running in that page, including our injected overlay, which
            # would otherwise render huge and unstyled with every state shown at once.
            ctx = await browser.new_context(bypass_csp=True)

        active_page = None  # tab commands can change which Page subsequent commands act on
        recent_actions: list[str] = []
        action_log: list[undo.ActionLogEntry] = []  # for smart "go back"/"undo"
        pending: dict | None = None  # {"kind": "confirm"|"disambiguate", "decision": Decision, ...}

        async def on_new_page(new_page) -> None:
            """Detects popups/new tabs the PAGE itself opened (target=_blank, window.open)
            -- our own act.new_tab() calls also fire this event but have no opener, so
            they're skipped here; run_action() logs those itself."""
            nonlocal active_page
            try:
                opener = await new_page.opener()
            except Exception:
                opener = None
            if opener is None:
                return
            previous = active_page
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            active_page = new_page
            action_log.append(undo.ActionLogEntry(kind="popup", page=new_page, previous_page=previous, label=previous.url))
            print("  ↗ popup opened:", new_page.url)
            await vox.show_on(active_page)

        ctx.on("page", lambda p: asyncio.ensure_future(on_new_page(p)))

        async def run_action(d: Decision, target_name: str = "") -> None:
            nonlocal active_page
            if d.intent in TAB_INTENTS:
                previous_page = active_page
                try:
                    if d.intent == "new_tab":
                        active_page = await act.new_tab(ctx, d.text)
                        action_log.append(undo.ActionLogEntry(
                            kind="new_tab", page=active_page, previous_page=previous_page, label=previous_page.url))
                        print("  ✓ opened new tab", active_page.url)
                    elif d.intent == "close_tab":
                        closing_url = active_page.url
                        active_page = await act.close_tab(ctx, active_page)
                        action_log.append(undo.ActionLogEntry(kind="close_tab", url_before=closing_url, label=closing_url))
                        print("  ✓ closed tab, now on", active_page.url)
                    elif d.intent == "switch_tab":
                        active_page = await act.switch_tab(ctx, active_page, d.text or "next")
                        action_log.append(undo.ActionLogEntry(
                            kind="switch_tab", page=active_page, previous_page=previous_page, label=previous_page.url))
                        print("  ✓ switched tab ->", active_page.url)
                    recent_actions.append(f"did {d.intent} {d.text or ''}".strip())
                    del recent_actions[:-5]
                    await vox.show_on(active_page)  # bring_to_front + persist current state on the NEW tab
                    await vox.set_then_idle_after(active_page, "acting", vox.describe_action(d, target_name))
                except Exception as e:
                    print("  ✗", e)
                    await vox.set_then_idle_after(
                        active_page, "error", vox.describe_failure(d.intent.replace("_", " ")), delay=ERROR_REVERT_S)
                return
            if d.target_id:
                await highlight(active_page, d.target_id)
            url_before = active_page.url
            try:
                result = await execute(active_page, d)
                print("  ✓", result)
                recent_actions.append(result)
                del recent_actions[:-5]
                if d.intent in NAV_INTENTS and active_page.url != url_before:
                    action_log.append(undo.ActionLogEntry(
                        kind="click" if d.intent == "click" else "navigate",
                        page=active_page, url_before=url_before, label=target_name or url_before))
                if result.startswith("no match"):
                    await vox.set_then_idle_after(
                        active_page, "error", vox.describe_not_found(d.text or target_name), delay=ERROR_REVERT_S)
                elif result.startswith("could not find") or result.startswith("skipped"):
                    await vox.set_then_idle_after(
                        active_page, "error", vox.describe_failure(d.intent.replace("_", " ")), delay=ERROR_REVERT_S)
                else:
                    await vox.set_then_idle_after(active_page, "acting", vox.describe_action(d, target_name))
            except Exception:
                print("  ✗ action failed")
                await vox.set_then_idle_after(
                    active_page, "error", vox.describe_failure(d.intent.replace("_", " ")), delay=ERROR_REVERT_S)

        async def on_vox_command(text: str) -> None:
            sched.submit(Transcript(text=text, is_final=True))

        async def handle(t: Transcript) -> None:
            nonlocal pending, active_page
            t_start = time.perf_counter()
            text_norm = t.text.strip().lower()

            await vox.set(active_page, "listening", t.text)

            # UI-only meta-commands: deterministic, never go through Jev.
            if vox.is_hide_command(t.text):
                await vox.hide(active_page)
                print("  ✓ hid Vox")
                pending = None
                return
            if vox.is_show_command(t.text):
                await vox.show(active_page)
                print("  ✓ showed Vox")
                pending = None
                return
            if text_norm in UNDO_PHRASES:
                new_page, desc = await undo.undo_last(ctx, action_log, active_page)
                active_page = new_page
                print("  ✓", desc)
                await vox.set_then_idle_after(active_page, "acting", desc)
                pending = None
                return

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
