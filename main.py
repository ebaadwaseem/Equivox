"""Run: python main.py   (opens a browser; type commands in the terminal)

  transcript (audio/) -> page state (perception/) -> decision (backend/) -> action (executor/)
"""
import asyncio
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import urlsplit

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from audio.apple_transcriber import AppleSpeechTranscriber
from audio.apple_transcriber import is_available as apple_speech_available
from audio.transcriber import TypedTranscriber
from backend import jev, policy, spans
from backend.context import ConversationContext, Turn
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


def _site_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").removeprefix("www.")
    except Exception:
        return ""


def _short_url(url: str) -> str:
    return url.removeprefix("https://").removeprefix("http://").removeprefix("www.")[:80]


def _target_name(d: Decision, cands: list[Element]) -> str:
    return next((e.name for e in cands if e.id == d.target_id), d.target_id or "")


def _consumed_prefix(raw_text: str, current: str, act_now: Optional[str], is_final: bool,
                     start_at: int = 0) -> str:
    """Physical-text prefix handled by this decision. A comma-bounded clause committed
    before the utterance ends consumes only up to that comma, so the final transcript
    can still be sliced into the next command."""
    full = " ".join(raw_text.split())
    current_n = " ".join(current.split())
    idx = full.lower().find(current_n.lower(), start_at)
    if idx < 0:
        idx = full.lower().find(current_n.lower())
    if idx < 0:
        return current_n
    end = idx + len(current_n)
    if act_now == "clause_complete" and not is_final:
        boundary = re.search(r"[,;]", full[idx:end])
        if boundary:
            end = idx + boundary.end()
    return full[:end]


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
        checkpoints: list[undo.Checkpoint] = []  # for smart "go back"/"undo"
        last_command_ts = 0.0  # when the most recent user command STARTED executing
        last_scroll: Optional[Decision] = None  # for "keep scrolling"/"more"/"again"
        pending: dict | None = None  # {"kind": "confirm"|"disambiguate"|"undo_disambiguate", ...}
        listening_paused = False
        apple_transcriber: Optional[AppleSpeechTranscriber] = None

        context = ConversationContext()
        latest_raw: dict[int, str] = {}
        action_busy = False
        action_queue: list = []
        action_wake = asyncio.Event()
        buffered_transcript: Optional[Transcript] = None
        silence_task: Optional[asyncio.Task] = None

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

        async def run_action(d: Decision, target_name: str = "", turn: Optional[Turn] = None) -> None:
            nonlocal active_page, last_scroll
            await mark_user_command()
            try:
                page_before = {"url": active_page.url, "title": await active_page.title()}
            except Exception:
                page_before = {"url": active_page.url if active_page else "", "title": ""}
            outcome = "done"

            try:
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
                        outcome = f"did {d.intent} {d.text or ''}".strip()
                        await vox.show_on(active_page)
                        await vox.set_then_idle_after(active_page, "acting", vox.describe_action(d, target_name))
                    except Exception as e:
                        outcome = "failed"
                        print("  ✗", e)
                        await vox.set_then_idle_after(
                            active_page, "error", vox.describe_failure(d.intent.replace("_", " ")), delay=ERROR_REVERT_S)
                    return

                if d.target_id:
                    await highlight(active_page, d.target_id)
                try:
                    result = await execute(active_page, d)
                    print("  ✓", result)
                    if result.startswith("no match"):
                        outcome = "failed"
                        await vox.set_then_idle_after(
                            active_page, "error", vox.describe_not_found(d.text or target_name), delay=ERROR_REVERT_S)
                    elif result.startswith("could not find") or result.startswith("skipped"):
                        outcome = "failed"
                        await vox.set_then_idle_after(
                            active_page, "error", vox.describe_failure(d.intent.replace("_", " ")), delay=ERROR_REVERT_S)
                    elif d.intent == "scroll":
                        outcome = result
                        if result.startswith("Already"):
                            await vox.set_then_idle_after(active_page, "error", result, delay=ERROR_REVERT_S)
                        else:
                            await vox.set_then_idle_after(active_page, "acting", result)
                        if d.scroll_to is None:
                            last_scroll = d
                    else:
                        outcome = result
                        await vox.set_then_idle_after(active_page, "acting", vox.describe_action(d, target_name))
                except Exception:
                    outcome = "failed"
                    print("  ✗ action failed")
                    await vox.set_then_idle_after(
                        active_page, "error", vox.describe_failure(d.intent.replace("_", " ")), delay=ERROR_REVERT_S)
            finally:
                if active_page and d.intent not in TAB_INTENTS and active_page.url != page_before["url"]:
                    context.remember_page(page_before["url"], page_before["title"])
                    if outcome != "failed":
                        outcome = f"navigated to {_short_url(active_page.url)}"
                context.finish(turn, outcome)

        def submit_call(fn) -> None:
            action_queue.append(fn)
            action_wake.set()

        def submit_action(d: Decision, target_name: str, turn: Optional[Turn] = None) -> None:
            submit_call(lambda: run_action(d, target_name, turn))

        async def action_worker() -> None:
            nonlocal action_busy, buffered_transcript
            while True:
                await action_wake.wait()
                action_wake.clear()
                while action_queue:
                    item = action_queue.pop(0)
                    action_busy = True
                    try:
                        await item()
                    finally:
                        action_busy = False
                if buffered_transcript is not None:
                    t, buffered_transcript = buffered_transcript, None
                    sched.submit(t, force=True)

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

        def schedule_silence(t: Transcript, raw_text: str, retry_in_ms: int, utterance_id: Optional[int]) -> None:
            nonlocal silence_task
            if utterance_id is None or t.is_final or t.silent_ms >= policy.SILENCE_COMPLETE_MS or retry_in_ms <= 0:
                return
            if silence_task and not silence_task.done():
                silence_task.cancel()

            async def fire() -> None:
                nonlocal silence_task
                try:
                    await asyncio.sleep(retry_in_ms / 1000)
                except asyncio.CancelledError:
                    return
                silence_task = None
                if latest_raw.get(utterance_id) != raw_text:
                    return
                sched.submit(
                    Transcript(
                        text=raw_text, is_final=False, confidence=t.confidence,
                        id=utterance_id, silent_ms=float(policy.SILENCE_COMPLETE_MS),
                    ),
                    force=True,
                )

            silence_task = asyncio.create_task(fire())

        async def handle(t: Transcript) -> None:
            nonlocal pending, listening_paused, buffered_transcript, silence_task
            t_start = time.perf_counter()
            if silence_task and not silence_task.done():
                silence_task.cancel()
                silence_task = None

            physical_id = t.id
            raw_text = t.text
            sliced = False
            if physical_id is not None:
                current, virtual_id = context.slice(physical_id, raw_text)
                if current is None:
                    if t.is_final and vox.current_state() == "listening":
                        await vox.set(active_page, "idle")
                    return
                sliced = current != raw_text
                if sliced:
                    t = Transcript(
                        text=current, is_final=t.is_final, confidence=t.confidence,
                        id=virtual_id, silent_ms=t.silent_ms,
                    )

            text_norm = t.text.strip().lower()
            display = f"{context.active_prefix} · {t.text}" if sliced and context.active_prefix else t.text
            await vox.set(active_page, "listening", display)

            # Captured before the confirm branch below may clear `pending`, so the model
            # still sees what the user was asked to confirm when they reply with something
            # that isn't a plain yes/no.
            pending_confirmation = None
            if pending and pending["kind"] == "confirm":
                pending_confirmation = vox.describe_action(pending["decision"], pending.get("target_name", ""))

            def _executed_prefix(t: Transcript, d: Decision) -> str:
                start = len(context.active_prefix or "") if context.consumed_id == physical_id else 0
                return _consumed_prefix(raw_text, t.text, d.act_now, t.is_final, start)

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
                turn = context.commit(t.text, Decision(True, "switch_tab", text="previous"), "previous tab")
                context.consume(physical_id, t.text)
                submit_action(Decision(True, "switch_tab", text="previous"), "previous tab", turn)
                pending = None
                return
            if text_norm in PREV_PAGE_PHRASES:
                turn = context.commit(t.text, Decision(True, "back"), "previous page")
                context.consume(physical_id, t.text)

                async def undo_same_tab() -> None:
                    try:
                        await resolve_and_execute_undo(same_tab_only=True)
                        context.finish(turn, "done")
                    except Exception:
                        context.finish(turn, "failed")

                submit_call(undo_same_tab)
                pending = None
                return
            if text_norm in UNDO_PHRASES:
                turn = context.commit(t.text, Decision(True, "back"), "undo")
                context.consume(physical_id, t.text)

                async def undo_last() -> None:
                    try:
                        await resolve_and_execute_undo()
                        context.finish(turn, "done")
                    except Exception:
                        context.finish(turn, "failed")

                submit_call(undo_last)
                pending = None
                return
            if spans.is_scroll_repeat(t.text):
                repeat_decision = last_scroll or Decision(True, "scroll", scroll_amount=spans.DEFAULT_SCROLL_FRACTION)
                turn = context.commit(t.text, repeat_decision)
                context.consume(physical_id, t.text)
                submit_action(repeat_decision, "", turn)
                pending = None
                return

            if pending and pending["kind"] == "confirm":
                if text_norm in AFFIRM:
                    d = pending["decision"]
                    said = pending.get("said", t.text)
                    turn = context.commit(said, d, pending.get("target_name", ""))
                    context.consume(pending.get("physical_id"), pending.get("prefix") or said)
                    submit_action(d, pending.get("target_name", ""), turn)
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
                    said = pending.get("said", t.text)
                    turn = context.commit(said, d, candidates[n - 1].name)
                    context.consume(pending.get("physical_id"), pending.get("prefix") or said)
                    pending = None
                    submit_action(d, candidates[n - 1].name, turn)
                    return
                pending = None  # fall through and treat this as a new command

            if pending and pending["kind"] == "undo_disambiguate":
                candidates = pending["candidates"]
                n = _parse_ordinal_reply(t.text, len(candidates))
                if n is not None:
                    d = Decision(True, "back")
                    turn = context.commit(t.text, d, "undo")
                    pending = None

                    async def undo_choice() -> None:
                        try:
                            await go_back_to(candidates[n - 1])
                            context.finish(turn, "done")
                        except Exception:
                            context.finish(turn, "failed")

                    submit_call(undo_choice)
                    return
                pending = None  # fall through and treat this as a new command

            if action_busy or action_queue:
                buffered_transcript = t
                return

            # Only switch to "thinking" once the utterance is final, and keep its words
            # on screen -- doing this per partial made the panel strobe between
            # Listening/Thinking (and blank the transcript) while the user was talking.
            if t.is_final:
                await vox.set(active_page, "thinking", t.text)
            state = await get_page_state(active_page)
            cands = shortlist(state, t.text)
            viewport = active_page.viewport_size
            vp = (viewport["width"], viewport["height"]) if viewport else None
            prompt_text = to_prompt_text(cands, vp)
            page_info = {"url": state.url, "title": state.title, "site": _site_of(state.url)}

            decide_t0 = time.perf_counter()
            d = await decide(
                t.text, t.is_final, prompt_text, cands,
                page=page_info, context=context,
                pending_confirmation=pending_confirmation, silent_ms=t.silent_ms,
            )
            decide_ms = (time.perf_counter() - decide_t0) * 1000

            target_name = _target_name(d, cands)
            stale = False
            if physical_id is not None:
                latest = latest_raw.get(physical_id)
                stale = latest is not None and " ".join(latest.split()) != " ".join(raw_text.split())
            if physical_id is not None and context.was_consumed(physical_id, t.text):
                return

            result = policy.evaluate(d, t.is_final, silent_ms=t.silent_ms, stale=stale)
            outcome = result.outcome
            logger.info(
                "transcript=%r final=%s stale=%s -> %s intent=%s target=%s conf=%.2f decide_ms=%.0f total_ms=%.0f",
                t.text, t.is_final, stale, outcome, d.intent, d.target_id, d.confidence,
                decide_ms, (time.perf_counter() - t_start) * 1000,
            )

            if outcome == "IGNORE":
                if t.is_final:
                    await vox.set(active_page, "idle")
                return
            if outcome == "WAIT":
                if t.is_final:
                    await vox.set(active_page, "idle")
                else:
                    schedule_silence(t, raw_text, result.retry_in_ms, physical_id)
                return
            if outcome == "PREVIEW":
                if d.target_id:
                    await highlight(active_page, d.target_id)
                return
            if outcome == "CONFIRM":
                if d.target_id:
                    await highlight(active_page, d.target_id)
                print(f'  ⚠ "{target_name}" looks irreversible -- say "yes" to confirm or "no" to cancel.')
                await vox.set(active_page, "unsure", f"{target_name}? Say yes or no")
                pending = {
                    "kind": "confirm", "decision": d, "target_name": target_name,
                    "said": t.text, "physical_id": physical_id,
                    "prefix": _executed_prefix(t, d),
                }
                return
            if outcome == "DISAMBIGUATE":
                top = cands[:3]
                if not top:
                    print("  ✗ no matching element on this page")
                    await vox.set_then_idle_after(
                        active_page, "error", vox.describe_not_found(t.text), delay=ERROR_REVERT_S)
                    return
                print("  ❓ Which one did you mean?")
                for i, e in enumerate(top, 1):
                    print(f'     {i}) {e.role} "{e.name}"')
                options = "   ".join(f"{i} · {e.name}" for i, e in enumerate(top, 1))
                await vox.set(active_page, "unsure", f"Did you mean: {options}")
                pending = {
                    "kind": "disambiguate", "decision": d, "candidates": top,
                    "said": t.text, "physical_id": physical_id,
                    "prefix": _executed_prefix(t, d),
                }
                return
            if outcome == "COMMIT":
                pending = None  # a fresh command supersedes any confirmation still open
                turn = context.commit(t.text, d, target_name)
                context.consume(physical_id, _executed_prefix(t, d))
                submit_action(d, target_name, turn)

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

        tasks = [asyncio.create_task(feed_terminal()), asyncio.create_task(action_worker())]

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
                # Echo mic partials immediately, outside the decision pipeline: a
                # cancelled stale decision never reaches vox.set(), so the live
                # transcript would otherwise lag exactly when speech is fastest.
                if t.id is not None:
                    latest_raw[t.id] = t.text
                    if not t.is_final:
                        await vox.set(active_page, "listening", t.text)
                sched.submit(t)
        finally:
            for task in tasks:
                task.cancel()
            await jev.aclose()


if __name__ == "__main__":
    asyncio.run(main())
