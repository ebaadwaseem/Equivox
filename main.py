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
from audio.moonshine_transcriber import MoonshineTranscriber, is_available as moonshine_available
from backend import jev, policy, spans, vision
from backend.commands import ordinal_reply, shortcut, inspection_request, explicit_option
from backend.choices import Choice, ChoiceSession, ranked_targets, page_suggestions
from backend.context import ConversationContext, Turn
from backend.clauses import first_clause
from backend.forms import field_names
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
    return ordinal_reply(text, count)


async def main() -> None:
    async with async_playwright() as p:
        cdp = os.getenv("CDP_URL")  # e.g. http://localhost:9222 to attach to your real Chrome
        if cdp:
            browser = await p.chromium.connect_over_cdp(cdp)
            ctx = browser.contexts[0]
        else:
            # --start-maximized opens at the full available window size
            browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
            # browser.new_page() is a shortcut that creates a context which can't have
            # more tabs added to it later ("Please use browser.new_context()") -- tab
            # management needs a real context we can call .new_page() on repeatedly.
            # bypass_csp: some sites' Content-Security-Policy (e.g. html.duckduckgo.com)
            # blocks ALL inline styling -- both <style> tags and style="" attributes -- for
            # every script running in that page, including our injected overlay, which
            # would otherwise render huge and unstyled with every state shown at once.
            # no_viewport: do NOT emulate a fixed 1280x720 viewport. The page follows the
            # real window size, so resizing/maximizing/fullscreen (green button or
            # Ctrl+Cmd+F) reflows normally instead of being locked to a fixed aspect ratio.
            ctx = await browser.new_context(bypass_csp=True, no_viewport=True)

        active_page = None  # tab commands can change which Page subsequent commands act on
        checkpoints: list[undo.Checkpoint] = []  # for smart "go back"/"undo"
        last_command_ts = 0.0  # when the most recent user command STARTED executing
        last_scroll: Optional[Decision] = None  # for "keep scrolling"/"more"/"again"
        pending: dict | None = None  # {"kind": "confirm"|"disambiguate"|"undo_disambiguate", ...}
        listening_paused = False
        apple_transcriber = None  # selected local speech adapter; both expose pause/resume

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
            watch_page(new_page)
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

        async def mark_user_command(page=None) -> None:
            nonlocal last_command_ts
            last_command_ts = time.time()
            await undo.push_checkpoint(checkpoints, page or active_page)

        async def run_action(d: Decision, target_name: str = "", turn: Optional[Turn] = None) -> None:
            nonlocal active_page, last_scroll, pending
            action_page = active_page
            await vox.set_options(action_page, [])
            action_started = time.perf_counter()
            await mark_user_command(action_page)
            try:
                page_before = {"url": action_page.url, "title": await action_page.title()}
            except Exception:
                page_before = {"url": action_page.url if action_page else "", "title": ""}
            outcome = "done"

            try:
                if d.intent in TAB_INTENTS:
                    try:
                        if d.intent == "new_tab":
                            active_page = await act.new_tab(ctx, d.text)
                            print("  ✓ opened new tab", active_page.url)
                        elif d.intent == "close_tab":
                            active_page = await act.close_tab(ctx, action_page)
                            print("  ✓ closed tab, now on", active_page.url)
                        elif d.intent == "switch_tab":
                            active_page = await act.switch_tab(ctx, action_page, d.text or "next")
                            print("  ✓ switched tab ->", active_page.url)
                        outcome = f"did {d.intent} {d.text or ''}".strip()
                        await vox.show_on(active_page)
                        await vox.set_then_idle_after(active_page, "acting", vox.describe_action(d, target_name))
                    except Exception as e:
                        outcome = "failed"
                        print("  ✗", e)
                        await vox.set_then_idle_after(
                            active_page, "error", str(e) if isinstance(e, ValueError) else vox.describe_failure(d.intent.replace("_", " ")), delay=ERROR_REVERT_S)
                    return

                if d.target_id:
                    await highlight(action_page, d.target_id)
                try:
                    result = await execute(action_page, d)
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
                    elif result == "opened native dropdown":
                        outcome = result
                        await inspect_page("show options", "options", target_name)
                    else:
                        outcome = result
                        await vox.set_then_idle_after(active_page, "acting", vox.describe_action(d, target_name))
                except Exception:
                    outcome = "failed"
                    print("  ✗ action failed")
                    await vox.set_then_idle_after(
                        active_page, "error", vox.describe_failure(d.intent.replace("_", " ")), delay=ERROR_REVERT_S)
            finally:
                if action_page and d.intent not in TAB_INTENTS and action_page.url != page_before["url"]:
                    context.remember_page(page_before["url"], page_before["title"])
                    if outcome != "failed":
                        outcome = f"navigated to {_short_url(action_page.url)}"
                context.finish(turn, outcome)
                logger.info("action=%s outcome=%s action_ms=%.0f", d.intent, outcome,
                            (time.perf_counter() - action_started) * 1000)

        def submit_call(fn) -> None:
            nonlocal page_epoch
            page_epoch += 1
            action_queue.append(fn)
            action_wake.set()

        def submit_action(d: Decision, target_name: str, turn: Optional[Turn] = None) -> None:
            expected_page = active_page
            async def run_reserved():
                if active_page is not expected_page:
                    context.finish(turn, "failed: active tab changed")
                    await vox.set(active_page, "unsure", "The active tab changed. Please repeat the command.")
                    return
                await run_action(d, target_name, turn)
            submit_call(run_reserved)

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

        async def inspect_page(goal, mode, said):
            nonlocal pending
            page = active_page
            await vox.set(page, "thinking", said)
            state = await get_page_state(page, with_screenshot=bool(os.getenv("VB_VLM_MODEL")))
            viewport = await page.evaluate("() => [innerWidth,innerHeight]")
            revision = await vision.scene_revision(page)
            suggestions = page_suggestions(state.elements)
            focused_select = next((e for e in state.elements if e.active and e.role == "select" and e.options), None)
            result = None if (suggestions or focused_select) and mode == "options" else await vision.observe(state, goal, viewport, context=context.encode())
            if page is not active_page or page.url != state.url:
                return
            if result is not None and await vision.scene_revision(page) != revision:
                await vox.set(page, "unsure", "The page changed while I was looking. Say show options again.")
                return
            visible = [e for e in shortlist(state, goal, limit=100) if e.in_viewport and e.name.strip() and e.role in {"button", "link", "option", "select", "textbox", "checkbox", "radio", "tab", "menuitem"}]
            visible.sort(key=lambda e: (not e.active, e.in_chrome or e.is_homepage_link))
            choices = result["choices"] if result is not None else [
                Choice(e.name, Decision(True, "click", target_id=e.id), detail=e.description or e.role, element=e) for e in visible[:8]]
            if suggestions and mode == "options":
                choices = [Choice(e.name, Decision(True, "click", target_id=e.id),
                                  detail=e.description, element=e) for e in suggestions]
            if mode == "options" and focused_select:
                choices = [Choice(label, Decision(True, "select", target_id=focused_select.id, text=label),
                                  detail=focused_select.name, element=focused_select) for label in focused_select.options[:30]]
            if result is not None:
                description = result["description"]
                if result["plan"]:
                    description += "\nSuggested steps: " + " ".join(f"{i+1}. {step}" for i,step in enumerate(result["plan"]))
            else:
                description = f"From page text: {state.title or 'Current page'}. {state.summary}"
                if os.getenv("VB_VLM_MODEL"):
                    description += " The visual model was unavailable."
                if mode != "options":
                    description += " Available controls: " + ", ".join(e.name for e in visible[:6]) + "."
                    if mode == "plan":
                        description += " Visual planning needs VB_VLM_MODEL. You can choose one of the visible controls below."
            if not choices:
                pending = None
                await vox.set_options(page, [])
                await vox.set(page, "acting", description.strip() or "No labelled controls are visible. Configure VB_VLM_MODEL to inspect the screenshot.")
                return
            session = ChoiceSession(page, page.url, choices, revision=revision if any(c.point for c in choices) else None,
                                    screenshot=state.screenshot_b64 if any(c.point for c in choices) else None)
            pending = {"kind": "choices", "session": session, "said": said, "page": page, "url": page.url}
            await vox.set(page, "unsure", description.strip() if mode != "options" else "Choose a visible control. Say click option one.")
            await vox.set_options(page, session.rows())

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
            await vox.set(active_page, "unsure", "Which page? Say click option one or click option two.")
            await vox.set_options(active_page, [dict(label=undo.describe_candidate(checkpoints, idx, active_page)) for idx in candidates])
            pending = {"kind": "undo_disambiguate", "candidates": candidates, "page": active_page, "url": active_page.url}

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
            form_state = None
            field_labels = []
            if not action_busy and not action_queue and (sliced or re.match(r"^(?:fill|set)\b", remainder, re.I)):
                form_state = await get_page_state(active_page)
                field_labels = [label for e in getattr(form_state, "elements", []) if e.role == "textbox" for label in field_names(e)]
            clause, clause_end, bounded = first_clause(remainder, field_labels)
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
                await vox.set_options(active_page, [])
                return
            if text_norm in RESUME_LISTENING_PHRASES or (
                text_norm in TOGGLE_LISTENING_PHRASES and listening_paused
            ):
                if apple_transcriber is not None:
                    await apple_transcriber.resume()
                listening_paused = False
                print("  ✓ resumed listening")
                await vox.set(active_page, "idle")
                await vox.set_options(active_page, [])
                pending = None
                return
            if vox.is_hide_command(t.text):
                await vox.hide(active_page)
                print("  ✓ hid Vox")
                await vox.set_options(active_page, [])
                pending = None
                return
            if vox.is_show_command(t.text):
                await vox.show(active_page)
                print("  ✓ showed Vox")
                await vox.set_options(active_page, [])
                pending = None
                return

            # A webpage's open autocomplete owns "click option N", even when an
            # earlier disambiguation question listed unrelated page controls.
            if explicit_option(t.text):
                fresh = await get_page_state(active_page)
                suggestions = page_suggestions(getattr(fresh, "elements", []))
                if suggestions:
                    choices = [Choice(e.name, Decision(True, "click", target_id=e.id),
                                      detail=e.description, element=e) for e in suggestions]
                    session = ChoiceSession(active_page, active_page.url, choices)
                    pending = {"kind": "choices", "session": session, "said": t.text,
                               "page": active_page, "url": active_page.url}
                    await vox.set_options(active_page, session.rows())

            # Options stay bound to their original page and survive ASR finalization
            # of the command that produced them. Spoken numbers never go through Jev.
            if pending and pending["kind"] in {"choices", "disambiguate", "undo_disambiguate"}:
                if pending.get("page", active_page) is not active_page or pending.get("url", active_page.url) != active_page.url:
                    pending = None
                    await vox.set_options(active_page, [])
                    await vox.set(active_page, "unsure", "Those options belong to the previous page. Say show options.")
                    return
                count = len(pending["session"].choices) if pending["kind"] == "choices" else len(pending["candidates"])
                number = ordinal_reply(t.text, count)
                if number is not None and not (t.is_final or bounded or quiet_ms(physical_id) >= policy.PAYLOAD_SILENCE_MS):
                    schedule_silence(t, raw_text, max(50, int(policy.PAYLOAD_SILENCE_MS - quiet_ms(physical_id))), physical_id)
                    return
                if number is None and not explicit_option(t.text) and not t.is_final and not bounded:
                    # A noisy intermediate hypothesis is not a reason to discard
                    # the question before the recognizer finishes "option two".
                    return
                if pending["kind"] == "choices":
                    session = pending["session"]
                    n = ordinal_reply(t.text, len(session.choices))
                    if n is not None:
                        revision = await vision.scene_revision(active_page) if session.revision is not None else None
                        if not session.valid(active_page, revision):
                            pending = None
                            await vox.set_options(active_page, [])
                            await vox.set(active_page, "unsure", "The page changed. Say show options again.")
                            return
                        choice = session.choices[n-1]
                        if choice.element:
                            fresh = await get_page_state(active_page)
                            current = next((e for e in fresh.elements if e.id == choice.element.id), None)
                            if (current is None or current.name != choice.element.name or
                                not session.valid(active_page, revision) or
                                (choice.decision.intent == "select" and current.options and choice.decision.text not in current.options)):
                                pending = None
                                await vox.set_options(active_page, [])
                                await vox.set(active_page, "unsure", "That control changed. Say show options again.")
                                return
                        pending = None
                        await vox.set_options(active_page, [])
                        if policy.looks_destructive(choice.label):
                            pending = {"kind":"confirm", "decision":choice.decision, "target_name":choice.label,
                                       "said":t.text, "physical_id":physical_id, "prefix":prefix,
                                       "page":active_page, "url":active_page.url}
                            context.consume(physical_id, prefix)
                            await vox.set(active_page, "unsure", f"{choice.label}? Say yes or no.")
                            return
                        turn = context.commit(t.text, choice.decision, choice.label)
                        consume_and_continue()
                        if choice.point:
                            async def visual_click():
                                page = session.page
                                if not session.valid(active_page, await vision.scene_revision(page)):
                                    context.finish(turn, "failed: page changed")
                                    await vox.set(active_page, "unsure", "The page changed. Say show options again.")
                                    return
                                fresh = await get_page_state(page, with_screenshot=True)
                                if fresh.screenshot_b64 != session.screenshot or active_page is not page:
                                    context.finish(turn, "failed: screenshot changed")
                                    await vox.set(active_page, "unsure", "The visible map changed. Say show map options again.")
                                    return
                                await mark_user_command(page)
                                outcome = await act.click_visual(page, choice.point)
                                context.finish(turn, outcome)
                                await vox.set_then_idle_after(active_page, "acting", outcome)
                            submit_call(visual_click)
                        else:
                            submit_action(choice.decision, choice.label, turn)
                        return
                    if explicit_option(t.text):
                        await vox.set(active_page, "unsure", f"Choose an option from 1 to {len(session.choices)}.")
                        return
                count = len(pending.get("candidates", []))
                if explicit_option(t.text) and ordinal_reply(t.text, count) is None:
                    await vox.set(active_page, "unsure", f"Choose an option from 1 to {count}.")
                    return

            if explicit_option(t.text) and pending is None:
                await vox.set_options(active_page, [])
                await vox.set(active_page, "unsure", "There are no options yet. Say show options.")
                return

            if mode := inspection_request(t.text):
                if not (t.is_final or bounded or quiet_ms(physical_id) >= 350):
                    schedule_silence(t, raw_text, 350, physical_id)
                    return
                consume_and_continue()
                pending = None
                await vox.set_options(active_page, [])
                submit_call(lambda: inspect_page(t.text, mode, raw_text))
                return

            if quick := shortcut(t.text):
                if not (t.is_final or bounded or quiet_ms(physical_id) >= 200):
                    schedule_silence(t, raw_text, 200, physical_id)
                    return
                pending = None
                turn = context.commit(t.text, quick)
                consume_and_continue()
                submit_action(quick, "", turn)
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
                if pending.get("page", active_page) is not active_page or pending.get("url", active_page.url) != active_page.url:
                    pending = None
                    await vox.set(active_page, "unsure", "The tab changed. Please repeat the action you wanted.")
                    return
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
                    d.destructive = d.destructive or policy.looks_destructive(candidates[n - 1].name)
                    await vox.set_options(active_page, [])
                    if d.destructive:
                        pending = dict(pending, kind="confirm", decision=d, target_name=candidates[n-1].name)
                        context.consume(physical_id, prefix)
                        await vox.set(active_page, "unsure", f"{candidates[n-1].name}? Say yes or no.")
                        return
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
                if not t.is_final and not bounded:
                    return
                pending = None  # a complete new command replaces the question
                await vox.set_options(active_page, [])

            if pending and pending["kind"] == "undo_disambiguate":
                candidates = pending["candidates"]
                n = _parse_ordinal_reply(t.text, len(candidates))
                if n is not None:
                    d = Decision(True, "back")
                    turn = context.commit(t.text, d, "undo")
                    consume_and_continue()
                    pending = None
                    await vox.set_options(active_page, [])

                    async def undo_choice() -> None:
                        try:
                            await go_back_to(candidates[n - 1])
                            context.finish(turn, "done")
                        except Exception:
                            context.finish(turn, "failed")

                    submit_call(undo_choice)
                    return
                if not t.is_final and not bounded:
                    return
                pending = None  # a complete new command replaces the question
                await vox.set_options(active_page, [])

            # Only switch to "thinking" once the utterance is final, and keep its words
            # on screen -- doing this per partial made the panel strobe between
            # Listening/Thinking (and blank the transcript) while the user was talking.
            if t.is_final:
                await vox.set(active_page, "thinking", raw_text)
            snapshot_started = time.perf_counter()
            if os.getenv("VB_VISION") == "1":
                state = await get_page_state(request_page, with_screenshot=True)
            else:
                state = form_state or await get_page_state(request_page)
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
                    t.text, t.is_final or bounded, prompt_text, cands,
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
                latest_clause = first_clause(latest_current, field_labels)[0] if latest_current else None
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
                    "page": active_page, "url": active_page.url,
                }
                context.consume(physical_id, prefix)
                return
            if outcome == "DISAMBIGUATE":
                suggestions = page_suggestions(getattr(state, "elements", [])) if d.intent == "click" else []
                if not suggestions and act.is_map_url(active_page.url) and os.getenv("VB_VLM_MODEL"):
                    consume_and_continue()
                    submit_call(lambda: inspect_page(t.text, "options", raw_text))
                    return
                top = suggestions or ranked_targets(d, cands)
                if not top:
                    print("  ✗ no matching element on this page")
                    await vox.set_then_idle_after(
                        active_page, "error", vox.describe_not_found(t.text), delay=ERROR_REVERT_S)
                    return
                print("  ❓ Which one did you mean?")
                for i, e in enumerate(top, 1):
                    print(f'     {i}) {e.role} "{e.name}"')
                await vox.set(active_page, "unsure", "Which control? Say click option one or click option two.")
                await vox.set_options(active_page, [dict(label=e.name or e.role, detail=e.description or e.role) for e in top])
                pending = {
                    "kind": "disambiguate", "decision": d, "candidates": top,
                    "said": t.text, "physical_id": physical_id,
                    "prefix": _executed_prefix(t, d), "page": active_page, "url": active_page.url,
                }
                # This clause now lives in the pending question, not in the next
                # transcript slice. The recognizer may append the spoken reply
                # to this same physical utterance instead of producing a new id.
                context.consume(physical_id, prefix)
                return
            if outcome == "COMMIT":
                await vox.set_options(active_page, [])
                pending = None  # a fresh command supersedes any confirmation still open
                turn = context.commit(t.text, d, target_name)
                consume_and_continue()
                submit_action(d, target_name, turn)

        sched = Scheduler(handle)

        # ONE input queue, multiple sources (terminal, the Vox panel's typed-command
        # box, a real mic later) -- all of them just produce Transcripts here; a single
        # consumer loop below drains the queue into the scheduler.
        input_queue: asyncio.Queue[Transcript] = asyncio.Queue()

        def watch_page(page):
            if not hasattr(page, "on") or getattr(page, "_vox_close_watched", False):
                return
            page._vox_close_watched = True
            def closed(*args):
                nonlocal active_page, page_epoch, pending
                remaining = [p for p in ctx.pages if not p.is_closed()]
                if not remaining:
                    input_queue.put_nowait(None)
                elif page is active_page:
                    active_page = remaining[-1]
                    page_epoch += 1
                    pending = None
                    asyncio.create_task(vox.show_on(active_page))
            page.on("close", closed)

        if hasattr(browser, "on"):
            browser.on("disconnected", lambda *args: input_queue.put_nowait(None))
        ctx.on("close", lambda *args: input_queue.put_nowait(None))

        def on_vox_command(source, text: str) -> None:
            # must return immediately -- just enqueue, never await anything slow here.
            text = (text or "").strip()
            if text:
                input_queue.put_nowait(Transcript(text=text, is_final=True))

        # expose_binding is registered on the CONTEXT (not a page), so it applies to
        # every current/future tab and survives navigation with no per-page plumbing.
        await ctx.expose_binding("voxCommand", on_vox_command)
        async def on_vox_active(source) -> None:
            nonlocal active_page, page_epoch
            page = source.get("page")
            if (page is not None and page is not active_page and not page.is_closed()
                    and await page.evaluate("() => document.visibilityState === 'visible'")):
                active_page = page
                page_epoch += 1
                await vox.show_on(page)
        await ctx.expose_binding("voxActiveTab", on_vox_active)
        # install the overlay's init-script BEFORE creating/navigating the first page --
        # add_init_script only affects pages/navigations registered after this point, so
        # doing this after the initial page.goto() would miss the first page entirely.
        await vox.install(ctx)

        if cdp:
            active_page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        else:
            active_page = await ctx.new_page()
            await active_page.goto(START_URL)
        for page in getattr(ctx, "pages", [active_page]):
            watch_page(page)

        async def feed_terminal() -> None:
            async for t in TypedTranscriber().stream():
                input_queue.put_nowait(t)

        tasks = [asyncio.create_task(feed_terminal()), asyncio.create_task(action_worker())]

        # VB_TRANSCRIBER=apple|typed selects the mic transcriber; defaults to apple if
        # the Swift listener binary has been built, else falls back to typed-only. The
        # terminal (and the Vox panel's typed-command box, via voxCommand above) stay
        # active regardless -- they're the fallback this whole thing must not break.
        transcriber_choice = os.getenv("VB_TRANSCRIBER", "auto")
        if transcriber_choice == "auto":
            transcriber_choice = "moonshine" if moonshine_available() else "apple" if apple_speech_available() else "typed"
        if transcriber_choice in {"apple", "moonshine"}:
            if transcriber_choice == "apple" and not apple_speech_available():
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
                        await vox.set(active_page, "error", detail)
                    elif kind == "loading":
                        await vox.set(active_page, "idle", detail)
                    else:
                        logger.info("voice listener status: %s %s", kind, detail)

                adapter = MoonshineTranscriber if transcriber_choice == "moonshine" else AppleSpeechTranscriber
                apple_transcriber = adapter(on_level=on_mic_level, on_status=on_mic_status)

                async def feed_apple() -> None:
                    async for t in apple_transcriber.stream():
                        if not listening_paused:
                            input_queue.put_nowait(t)

                tasks.append(asyncio.create_task(feed_apple()))

        input_serial = 0
        try:
            while True:
                t = await input_queue.get()
                if t is None:
                    break
                if getattr(active_page, "is_closed", lambda: False)():
                    remaining = [p for p in ctx.pages if not p.is_closed()]
                    if not remaining:
                        break
                    active_page = remaining[-1]
                    page_epoch += 1
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
            if not cdp and hasattr(browser, "close"):
                await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
