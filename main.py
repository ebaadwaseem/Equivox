"""Run: python main.py   (opens a browser; type commands in the terminal)

  transcript (audio/) -> page state (perception/) -> decision (backend/) -> action (executor/)
"""
import asyncio
import logging
import os
import time
from typing import Optional

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from audio.apple_transcriber import AppleSpeechTranscriber
from audio.apple_transcriber import is_available as apple_speech_available
from audio.transcriber import TypedTranscriber
from backend import jev, policy, spans
from backend.early_commit import EarlyCommitTracker
from backend.model import decide
from backend.scheduler import Scheduler
from executor import act, undo
from executor.act import execute, highlight
from perception.perceive import ORDINALS, get_page_state, shortlist, to_prompt_text
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
UNDO_PHRASES = {"go back", "back", "undo", "undo that", "go back again"}
PREV_TAB_PHRASES = {"previous tab", "last tab"}
PREV_PAGE_PHRASES = {"previous page"}
SITE_OPENED_WINDOW_S = 2.0  # no user command running in this window -> an unprompted popup
TAB_INTENTS = {"new_tab", "close_tab", "switch_tab"}
ERROR_REVERT_S = 4.0

PAUSE_LISTENING_PHRASES = {"stop listening", "pause listening"}
RESUME_LISTENING_PHRASES = {"start listening", "resume listening"}
TOGGLE_LISTENING_PHRASES = {"toggle listening"}


def _target_name(d: Decision, cands: list[Element]) -> str:
    return next((e.name for e in cands if e.id == d.target_id), d.target_id or "")


def _parse_ordinal_reply(text: str, count: int) -> Optional[int]:
    """"2", "option 2", "the second one", "second" -> 2 (1-indexed), for replying to a
    numbered confirm/disambiguate prompt from either the terminal or the Vox panel."""
    for w in (w.strip(".,!?") for w in text.strip().lower().split()):
        if w.isdigit():
            n = int(w)
            if 1 <= n <= count:
                return n
        elif w in ORDINALS:
            n = ORDINALS[w]
            if n == -1:  # "last"
                return count
            if 1 <= n <= count:
                return n
    return None


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
        checkpoints: list[undo.Checkpoint] = []  # for smart "go back"/"undo"
        last_command_ts = 0.0  # when the most recent user command STARTED executing
        last_scroll: Optional[Decision] = None  # for "keep scrolling"/"more"/"again"
        pending: dict | None = None  # {"kind": "confirm"|"disambiguate"|"undo_disambiguate", ...}
        listening_paused = False
        apple_transcriber: Optional[AppleSpeechTranscriber] = None

        # early-commit-on-partials: keyed internally by Transcript.id (None for
        # typed/panel input, which naturally opts out of all of this).
        early_commit = EarlyCommitTracker()
        first_partial_ts: dict[int, float] = {}

        async def on_new_page(new_page) -> None:
            """Detects popups/new tabs the PAGE itself opened (target=_blank, window.open)
            -- our own act.new_tab() calls also fire this event but have no opener, so
            they're skipped here. Never creates a checkpoint: popups are site-initiated
            by definition, which is exactly what checkpoints must NOT capture."""
            nonlocal active_page
            try:
                opener = await new_page.opener()
            except Exception:
                opener = None
            if opener is None:
                return
            new_page.site_opened = (time.time() - last_command_ts) > SITE_OPENED_WINDOW_S
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            active_page = new_page
            print("  ↗ popup opened:", new_page.url, "(unprompted)" if new_page.site_opened else "")
            await vox.show_on(active_page)

        ctx.on("page", lambda p: asyncio.ensure_future(on_new_page(p)))

        async def mark_user_command() -> None:
            nonlocal last_command_ts
            last_command_ts = time.time()
            await undo.push_checkpoint(checkpoints, active_page)

        async def run_action(d: Decision, target_name: str = "") -> None:
            nonlocal active_page, last_scroll
            await mark_user_command()

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
                    await vox.show_on(active_page)  # bring_to_front + persist current state on the NEW tab
                    await vox.set_then_idle_after(active_page, "acting", vox.describe_action(d, target_name))
                except Exception as e:
                    print("  ✗", e)
                    await vox.set_then_idle_after(
                        active_page, "error", vox.describe_failure(d.intent.replace("_", " ")), delay=ERROR_REVERT_S)
                return
            if d.target_id:
                await highlight(active_page, d.target_id)
            try:
                result = await execute(active_page, d)
                print("  ✓", result)
                recent_actions.append(result)
                del recent_actions[:-5]
                if result.startswith("no match"):
                    await vox.set_then_idle_after(
                        active_page, "error", vox.describe_not_found(d.text or target_name), delay=ERROR_REVERT_S)
                elif result.startswith("could not find") or result.startswith("skipped"):
                    await vox.set_then_idle_after(
                        active_page, "error", vox.describe_failure(d.intent.replace("_", " ")), delay=ERROR_REVERT_S)
                elif d.intent == "scroll":
                    # executor.act.scroll()'s own result text knows whether it actually
                    # moved, hit an edge, or reached an explicit top/bottom target --
                    # describe_action() can only guess from the Decision alone.
                    if result.startswith("Already"):
                        await vox.set_then_idle_after(active_page, "error", result, delay=ERROR_REVERT_S)
                    else:
                        await vox.set_then_idle_after(active_page, "acting", result)
                    if d.scroll_to is None:
                        last_scroll = d
                else:
                    await vox.set_then_idle_after(active_page, "acting", vox.describe_action(d, target_name))
            except Exception:
                print("  ✗ action failed")
                await vox.set_then_idle_after(
                    active_page, "error", vox.describe_failure(d.intent.replace("_", " ")), delay=ERROR_REVERT_S)

        async def go_back_to(index: int) -> None:
            nonlocal active_page
            new_page, desc = await undo.execute_undo(ctx, checkpoints, index, active_page)
            active_page = new_page
            print("  ✓", desc)
            await vox.set_then_idle_after(active_page, "acting", desc)

        async def resolve_and_execute_undo(same_tab_only: bool = False) -> None:
            nonlocal pending
            if same_tab_only:
                idx = undo.find_target(checkpoints, active_page, same_tab_only=True)
                if idx is None:
                    await vox.set_then_idle_after(
                        active_page, "error", "There's no previous page on this tab to go back to.", delay=ERROR_REVERT_S)
                    return
                await go_back_to(idx)
                return

            candidates = undo.find_candidates(checkpoints, active_page)
            if not candidates:
                try:
                    await active_page.go_back()
                except Exception:
                    pass
                print("  ✓ Going back")
                await vox.set_then_idle_after(active_page, "acting", "Going back")
                return
            if len(candidates) == 1:
                await go_back_to(candidates[0])
                return

            model = os.getenv("VB_MODEL", "")
            idx, confidence = await undo.resolve_ambiguity(model, checkpoints, candidates, active_page)
            if idx is not None and confidence >= undo.AMBIGUITY_CONFIDENCE_THRESHOLD:
                await go_back_to(idx)
                return

            print("  ❓ Which one did you mean?")
            for i, cand_idx in enumerate(candidates, 1):
                print(f"     {i}) {undo.describe_candidate(checkpoints, cand_idx, active_page)}")
            options = "   ".join(
                f"{i} · {undo.describe_candidate(checkpoints, cand_idx, active_page)}"
                for i, cand_idx in enumerate(candidates, 1)
            )
            await vox.set(active_page, "unsure", f"Did you mean: {options}")
            pending = {"kind": "undo_disambiguate", "candidates": candidates}

        async def handle(t: Transcript) -> None:
            nonlocal pending, active_page, listening_paused
            t_start = time.perf_counter()
            text_norm = t.text.strip().lower()

            if t.id is not None:
                if early_commit.already_committed(t.id):
                    return  # already acted on this utterance via early commit -- ignore
                first_partial_ts.setdefault(t.id, t_start)
                if t.is_final:
                    # the partial-tracking part of this utterance's life is over either way
                    early_commit.forget(t.id)
                    first_partial_ts.pop(t.id, None)

            await vox.set(active_page, "listening", t.text)

            # UI-only meta-commands: deterministic, never go through Jev.
            if text_norm in PAUSE_LISTENING_PHRASES or (
                text_norm in TOGGLE_LISTENING_PHRASES and not listening_paused
            ):
                if apple_transcriber is not None:
                    await apple_transcriber.pause()
                listening_paused = True
                print("  ✓ paused listening")
                await vox.set(active_page, "idle", "Paused")
                pending = None
                return
            if text_norm in RESUME_LISTENING_PHRASES or (
                text_norm in TOGGLE_LISTENING_PHRASES and listening_paused
            ):
                if apple_transcriber is not None:
                    await apple_transcriber.resume()
                listening_paused = False
                print("  ✓ resumed listening")
                await vox.set(active_page, "idle")
                pending = None
                return
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
            # explicit phrases skip inference entirely
            if text_norm in PREV_TAB_PHRASES:
                await mark_user_command()
                active_page = await act.switch_tab(ctx, active_page, "previous")
                await vox.show_on(active_page)
                await vox.set_then_idle_after(active_page, "acting", "Switching to the previous tab")
                pending = None
                return
            if text_norm in PREV_PAGE_PHRASES:
                await resolve_and_execute_undo(same_tab_only=True)
                pending = None
                return
            if text_norm in UNDO_PHRASES:
                await resolve_and_execute_undo()
                pending = None
                return
            if spans.is_scroll_repeat(t.text):
                repeat_decision = last_scroll or Decision(True, "scroll", scroll_amount=spans.DEFAULT_SCROLL_FRACTION)
                await run_action(repeat_decision)
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
                candidates = pending["candidates"]
                n = _parse_ordinal_reply(t.text, len(candidates))
                if n is not None:
                    d = pending["decision"]
                    d.target_id = candidates[n - 1].id
                    d.ambiguous = False
                    pending = None
                    await run_action(d, candidates[n - 1].name)
                    return
                pending = None  # fall through and treat this as a new command

            if pending and pending["kind"] == "undo_disambiguate":
                candidates = pending["candidates"]
                n = _parse_ordinal_reply(t.text, len(candidates))
                if n is not None:
                    pending = None
                    await go_back_to(candidates[n - 1])
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

            target_name = _target_name(d, cands)

            # EARLY COMMIT: act on a partial, before the ~700ms endpoint-triggered final,
            # for intents with no free text to get wrong -- only once confidence is high
            # AND the same target has held steady across 2 consecutive partials.
            if early_commit.should_commit(t.id, d, t.is_final):
                latency_ms = (t_start - first_partial_ts.pop(t.id, t_start)) * 1000
                logger.info(
                    "early commit: id=%s intent=%s target=%s first_partial_to_action_ms=%.0f",
                    t.id, d.intent, d.target_id, latency_ms,
                )
                await run_action(d, target_name)
                return

            outcome = policy.decide_outcome(d, t.is_final)
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

        # ONE input queue, multiple sources (terminal, the Vox panel's typed-command
        # box, a real mic later) -- all of them just produce Transcripts here; a single
        # consumer loop below drains the queue into the scheduler.
        input_queue: asyncio.Queue[Transcript] = asyncio.Queue()

        def on_vox_command(source, text: str) -> None:
            # must return immediately -- just enqueue, never await anything slow here.
            text = (text or "").strip()
            if text:
                input_queue.put_nowait(Transcript(text=text, is_final=True))

        # expose_binding is registered on the CONTEXT (not a page), so it applies to
        # every current/future tab and survives navigation with no per-page plumbing.
        await ctx.expose_binding("voxCommand", on_vox_command)
        # install the overlay's init-script BEFORE creating/navigating the first page --
        # add_init_script only affects pages/navigations registered after this point, so
        # doing this after the initial page.goto() would miss the first page entirely.
        await vox.install(ctx)

        if cdp:
            active_page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        else:
            active_page = await ctx.new_page()
            await active_page.goto(START_URL)

        async def feed_terminal() -> None:
            async for t in TypedTranscriber().stream():
                input_queue.put_nowait(t)

        tasks = [asyncio.create_task(feed_terminal())]

        # VB_TRANSCRIBER=apple|typed selects the mic transcriber; defaults to apple if
        # the Swift listener binary has been built, else falls back to typed-only. The
        # terminal (and the Vox panel's typed-command box, via voxCommand above) stay
        # active regardless -- they're the fallback this whole thing must not break.
        transcriber_choice = os.getenv("VB_TRANSCRIBER", "apple" if apple_speech_available() else "typed")
        if transcriber_choice == "apple":
            if not apple_speech_available():
                print("  ⚠ VB_TRANSCRIBER=apple but bin/vox-listen is missing -- run scripts/build_listener.sh. "
                      "Falling back to typed input only.")
            else:
                async def on_mic_level(value: float) -> None:
                    await vox.set_level(active_page, value)

                async def on_mic_status(kind: str, detail: str) -> None:
                    if kind == "error":
                        logger.warning("voice listener: %s", detail)
                        print("  ⚠ voice listener:", detail)
                    else:
                        logger.info("voice listener status: %s %s", kind, detail)

                apple_transcriber = AppleSpeechTranscriber(on_level=on_mic_level, on_status=on_mic_status)

                async def feed_apple() -> None:
                    async for t in apple_transcriber.stream():
                        if not listening_paused:
                            input_queue.put_nowait(t)

                tasks.append(asyncio.create_task(feed_apple()))

        try:
            while True:
                t = await input_queue.get()
                sched.submit(t)
        finally:
            for task in tasks:
                task.cancel()
            await jev.aclose()


if __name__ == "__main__":
    asyncio.run(main())
