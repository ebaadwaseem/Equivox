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
from backend.clauses import first_clause
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
        latest_transcripts: dict[int, Transcript] = {}
        last_heard: dict[int, float] = {}
        last_audio_at = 0.0
        page_epoch = 0
        decision_cache = {}
        action_busy = False
        action_queue: list = []
        action_wake = asyncio.Event()
        buffered_transcripts: dict[int, Transcript] = {}
        silence_task: Optional[asyncio.Task] = None

        def quiet_ms(utterance_id) -> float:
            # Mic levels and transcript changes are separate signals. Missing ASR
            # updates must not be mistaken for silence in the middle of a query.
            changed = last_heard.get(utterance_id, time.monotonic())
            if utterance_id is not None and utterance_id >= 0:
                changed = max(changed, last_audio_at)
            return max(0, (time.monotonic() - changed) * 1000)

        async def on_new_page(new_page) -> None:
            """Detects popups/new tabs the PAGE itself opened (target=_blank, window.open)
            -- our own act.new_tab() calls also fire this event but have no opener, so
            they're skipped here. Never creates a checkpoint: popups are site-initiated
            by definition, which is exactly what checkpoints must NOT capture."""
            nonlocal active_page, page_epoch
            try:
                opener = await new_page.opener()
            except Exception:
                opener = None
            if opener is None:
                return
            new_page.site_opened = (time.time() - last_command_ts) > SITE_OPENED_WINDOW_S
            active_page = new_page
            page_epoch += 1
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            print("  ↗ popup opened:", new_page.url, "(unprompted)" if new_page.site_opened else "")
            if active_page is new_page:
                await vox.show_on(new_page)

        ctx.on("page", lambda p: asyncio.ensure_future(on_new_page(p)))

        async def mark_user_command() -> None:
            nonlocal last_command_ts
            last_command_ts = time.time()
            await undo.push_checkpoint(checkpoints, active_page)

        async def run_action(d: Decision, target_name: str = "", turn: Optional[Turn] = None) -> None:
            nonlocal active_page, last_scroll
            action_started = time.perf_counter()
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
                logger.info("action=%s outcome=%s action_ms=%.0f", d.intent, outcome,
                            (time.perf_counter() - action_started) * 1000)

        def submit_call(fn) -> None:
            nonlocal page_epoch
            page_epoch += 1
            action_queue.append(fn)
            action_wake.set()

        def submit_action(d: Decision, target_name: str, turn: Optional[Turn] = None) -> None:
            submit_call(lambda: run_action(d, target_name, turn))

        async def action_worker() -> None:
            nonlocal action_busy
            while True:
                await action_wake.wait()
                action_wake.clear()
                while action_queue:
                    item = action_queue.pop(0)
                    action_busy = True
                    try:
                        await vox.set_history(active_page, context.history, list(context.turns))
                        await item()
                    except Exception:
                        logger.exception("action worker failed")
                    finally:
                        await vox.set_history(active_page, context.history, list(context.turns))
                        action_busy = False
                buffered = list(buffered_transcripts.values())
                buffered_transcripts.clear()
                for t in buffered:
                    sched.submit(latest_transcripts.get(t.id, t), force=True)

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
                        id=utterance_id, silent_ms=quiet_ms(utterance_id),
                    ),
                    force=True,
                )

            silence_task = asyncio.create_task(fire())

        async def handle(t: Transcript) -> None:
            nonlocal pending, listening_paused, silence_task
            t_start = time.perf_counter()
            if silence_task and not silence_task.done():
                silence_task.cancel()
                silence_task = None

            physical_id = t.id
            raw_text = " ".join(t.text.split())
            t = Transcript(raw_text, t.is_final, t.confidence, t.id, t.silent_ms)
            # Keep the physical transcript throughout buffering and continuation.
            physical = t
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

            remainder = t.text
            clause, clause_end, bounded = first_clause(remainder)
            offset = len(" ".join(raw_text.split())) - len(remainder)
            prefix = " ".join(raw_text.split())[:offset + clause_end]
            t = Transcript(clause, t.is_final, t.confidence, physical_id, t.silent_ms)
            consumed_gen = context.generation(physical_id)
            if action_busy or action_queue:
                buffered_transcripts[physical_id] = physical
                return
            request_epoch = page_epoch
            request_page = active_page
            text_norm = t.text.strip().lower()
            await vox.set(active_page, "listening", raw_text)

            # Captured before the confirm branch below may clear `pending`, so the model
            # still sees what the user was asked to confirm when they reply with something
            # that isn't a plain yes/no.
            pending_confirmation = None
            if pending and pending["kind"] == "confirm":
                pending_confirmation = vox.describe_action(pending["decision"], pending.get("target_name", ""))

            def _executed_prefix(t: Transcript, d: Decision) -> str:
                return prefix

            def consume_and_continue() -> None:
                context.consume(physical_id, prefix)
                # Even a single final containing several clauses must drain itself.
                buffered_transcripts[physical_id] = latest_transcripts.get(physical_id, physical)

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
                consume_and_continue()
                submit_action(Decision(True, "switch_tab", text="previous"), "previous tab", turn)
                pending = None
                return
            if text_norm in PREV_PAGE_PHRASES:
                turn = context.commit(t.text, Decision(True, "back"), "previous page")
                consume_and_continue()

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
                consume_and_continue()

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
                consume_and_continue()
                submit_action(repeat_decision, "", turn)
                pending = None
                return

            if pending and pending["kind"] == "confirm":
                if text_norm in AFFIRM:
                    d = pending["decision"]
                    said = pending.get("said", t.text)
                    turn = context.commit(said, d, pending.get("target_name", ""))
                    confirmed_id = pending.get("physical_id")
                    context.consume(confirmed_id, pending.get("prefix") or said)
                    if confirmed_id in latest_transcripts:
                        buffered_transcripts[confirmed_id] = latest_transcripts[confirmed_id]
                    context.consume(physical_id, prefix)
                    submit_action(d, pending.get("target_name", ""), turn)
                    pending = None
                    return
                if text_norm in DENY:
                    cancelled_id = pending.get("physical_id")
                    context.consume(cancelled_id, latest_raw.get(cancelled_id, pending.get("said", "")))
                    context.consume(physical_id, prefix)
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
                    confirmed_id = pending.get("physical_id")
                    context.consume(confirmed_id, pending.get("prefix") or said)
                    if confirmed_id in latest_transcripts:
                        buffered_transcripts[confirmed_id] = latest_transcripts[confirmed_id]
                    context.consume(physical_id, prefix)
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

            # Only switch to "thinking" once the utterance is final, and keep its words
            # on screen -- doing this per partial made the panel strobe between
            # Listening/Thinking (and blank the transcript) while the user was talking.
            if t.is_final:
                await vox.set(active_page, "thinking", raw_text)
            snapshot_started = time.perf_counter()
            if os.getenv("VB_VISION") == "1":
                state = await get_page_state(request_page, with_screenshot=True)
            else:
                state = await get_page_state(request_page)
            cands = shortlist(state, t.text)
            viewport = request_page.viewport_size
            vp = (viewport["width"], viewport["height"]) if viewport else None
            prompt_text = to_prompt_text(cands, vp)
            snapshot_ms = (time.perf_counter() - snapshot_started) * 1000
            page_info = {"url": state.url, "title": state.title, "site": _site_of(state.url),
                         "summary": getattr(state, "summary", ""),
                         "screenshot_b64": getattr(state, "screenshot_b64", None),
                         "tabs": [p.url for p in getattr(ctx, "pages", [])]}

            decide_t0 = time.perf_counter()
            cache_key = (physical_id, consumed_gen, page_epoch, t.text, prompt_text,
                         state.url, state.title, page_info["summary"], page_info["screenshot_b64"],
                         pending_confirmation, context.encode())
            cached = decision_cache.get(physical_id)
            if t.silent_ms and cached and cached[0] == cache_key:
                d = cached[1]
            else:
                d = await decide(
                    t.text, t.is_final, prompt_text, cands,
                    page=page_info, context=context,
                    pending_confirmation=pending_confirmation, silent_ms=t.silent_ms,
                )
                # The no-key fallback deliberately waits for final/silence and
                # needs to be evaluated again when that input changes.
                if os.getenv("OPENROUTER_API_KEY") and os.getenv("VB_MODEL"):
                    decision_cache[physical_id] = (cache_key, d)
            decide_ms = (time.perf_counter() - decide_t0) * 1000

            if bounded:
                d.payload_bounded = True
                if d.act_now in {"clause_complete", "utterance_complete"}:
                    d.act_now = "clause_complete"
            # Epoch increments at reservation, before the browser action starts.
            # Reject decisions that saw a different page or an already consumed clause.
            if context.generation(physical_id) != consumed_gen:
                return
            if request_epoch != page_epoch or request_page is not active_page:
                buffered_transcripts[physical_id] = latest_transcripts.get(physical_id, physical)
                if not action_busy and not action_queue:
                    sched.submit(buffered_transcripts.pop(physical_id), force=True)
                return
            target_name = _target_name(d, cands)
            stale = False
            if physical_id is not None:
                latest = latest_raw.get(physical_id, raw_text)
                latest_current, _ = context.slice(physical_id, latest)
                # A later clause may arrive during inference. A bounded first
                # clause is unchanged and safe to commit; a revised payload isn't.
                latest_clause = first_clause(latest_current)[0] if latest_current else None
                stale = latest_clause != t.text
                if stale:
                    return

            silent_ms = quiet_ms(physical_id)
            result = policy.evaluate(d, t.is_final, silent_ms=silent_ms, stale=stale)
            outcome = result.outcome
            logger.info(
                "transcript=%r final=%s stale=%s -> %s intent=%s target=%s conf=%.2f snapshot_ms=%.0f decide_ms=%.0f total_ms=%.0f",
                t.text, t.is_final, stale, outcome, d.intent, d.target_id, d.confidence,
                snapshot_ms, decide_ms, (time.perf_counter() - t_start) * 1000,
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
                consume_and_continue()
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
                    nonlocal last_audio_at
                    if not listening_paused and value > 0.05:
                        last_audio_at = time.monotonic()
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

        input_serial = 0
        try:
            while True:
                t = await input_queue.get()
                if t.id is None:
                    input_serial -= 1
                    t.id = input_serial
                latest_transcripts[t.id] = t
                if latest_raw.get(t.id) != t.text:
                    last_heard[t.id] = time.monotonic()
                    if silence_task:
                        silence_task.cancel()
                        silence_task = None
                # Echo mic partials immediately, outside the decision pipeline: a
                # cancelled stale decision never reaches vox.set(), so the live
                # transcript would otherwise lag exactly when speech is fastest.
                if t.id is not None:
                    latest_raw[t.id] = t.text
                    if not t.is_final:
                        await vox.set(active_page, "listening", t.text)
                # The input path also buffers immediately; no scheduler coalescing
                # can drop a separate utterance while navigation is running.
                if action_busy or action_queue:
                    buffered_transcripts[t.id] = t
                else:
                    sched.submit(t)
                # Bound recognizer metadata without losing queued utterances.
                if len(latest_raw) > 128:
                    oldest = next(iter(latest_raw))
                    if oldest not in buffered_transcripts:
                        latest_raw.pop(oldest, None)
                        latest_transcripts.pop(oldest, None)
                        last_heard.pop(oldest, None)
                        decision_cache.pop(oldest, None)
        finally:
            for task in tasks:
                task.cancel()
            if silence_task:
                silence_task.cancel()
            await sched.aclose()
            await asyncio.gather(*tasks, return_exceptions=True)
            await jev.aclose()


if __name__ == "__main__":
    asyncio.run(main())
