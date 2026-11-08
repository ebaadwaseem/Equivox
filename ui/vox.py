"""Drives the on-screen Vox overlay (ui/overlay.js) from Python.

The overlay's DOM is rebuilt by the browser on every new document, so this module
remembers the single current (state, text) and re-applies it whenever a page needs
it: right after `set()`, after that page's own navigations, and after main.py switches
which page is "active" (new tab / tab switch). Never raises -- a missing overlay
(not mounted yet, page mid-navigation) just means the call is a no-op.
"""
import asyncio
import logging
import re
import weakref
from pathlib import Path

from playwright.async_api import Page

from shared.protocol import Decision

logger = logging.getLogger("vox")

OVERLAY_JS = (Path(__file__).parent / "overlay.js").read_text()

# deterministic, no Jev -- these are UI-only meta-commands about Vox itself, not
# browser actions, so they're intercepted before the transcript ever reaches decide().
_MINIMIZE_PHRASES = [
    "minimize", "minimize yourself", "vox minimize", "hide", "hide vox", "hide yourself",
    "go away", "go small", "get out of the way", "shrink",
]
_SHOW_PHRASES = [
    "show vox", "show yourself", "come back", "vox come back", "expand", "restore",
    "vox", "where are you",
]


def _build_vox_command_re(phrases: list[str]) -> re.Pattern:
    # optional "vox"/"hey vox" prefix and optional "please" (leading or trailing)
    alts = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"^(?:please\s+)?(?:hey\s+vox\s+|vox\s+)?(?:{alts})(?:\s+please)?$", re.I)


_MINIMIZE_RE = _build_vox_command_re(_MINIMIZE_PHRASES)
_SHOW_RE = _build_vox_command_re(_SHOW_PHRASES)


def is_hide_command(text: str) -> bool:
    return bool(_MINIMIZE_RE.match(text.strip()))


def is_show_command(text: str) -> bool:
    return bool(_SHOW_RE.match(text.strip()))


_current: tuple[str, str] = ("idle", "")
_minimized = False
_version = 0
_wired_pages = weakref.WeakSet()
_history: list[dict] = []


async def set_history(page: Page, turns, context_turns) -> None:
    """Retain the session trail across documents, with the exact model window marked."""
    global _history
    context_ids = {id(t) for t in context_turns}
    _history = [dict(said=t.said, action=t.intent, target=t.target, text=t.text,
                     outcome=t.outcome, in_context=id(t) in context_ids) for t in turns]
    await _apply(page)


async def install(context) -> None:
    """Call once per BrowserContext: injects the overlay into every current/future page.
    The overlay's typed-command box calls window.voxCommand(text) directly -- main.py
    wires that up itself via context.expose_binding(), once, at the context level (so
    it works on every tab and survives navigation without any per-page plumbing here).
    """
    try:
        await context.add_init_script(OVERLAY_JS)
    except Exception as e:
        logger.warning("overlay install failed: %s", e)


async def set(page: Page, state: str, text: str = "") -> None:
    """Update the overlay's visible state/text on `page`."""
    global _current, _version
    _current = (state, text)
    _version += 1
    await _wire(page)
    await _apply(page)


async def set_then_idle_after(page: Page, state: str, text: str, delay: float = 1.5) -> None:
    """Shows `state`/`text`, then reverts to idle after `delay`s -- unless a newer
    call to set()/set_then_idle_after() has already changed the state by then."""
    await set(page, state, text)
    my_version = _version

    async def _revert() -> None:
        await asyncio.sleep(delay)
        if _version == my_version:
            await set(page, "idle")

    asyncio.ensure_future(_revert())


async def hide(page: Page) -> None:
    """"hide vox" -- minimizes to a small droplet. Remembered across navigation/tabs."""
    global _minimized
    _minimized = True
    await _wire(page)
    await _apply(page)


async def show(page: Page) -> None:
    """"show vox" -- restores the panel."""
    global _minimized
    _minimized = False
    await _wire(page)
    await _apply(page)


async def set_level(page: Page, value: float) -> None:
    """High-frequency mic-level updates (~10-15/s while listening) -- a lightweight
    path that only touches the --level CSS var, skipping the full state/text pipeline."""
    try:
        await page.evaluate("v => window.__vox && window.__vox.setLevel && window.__vox.setLevel(v)", value)
    except Exception as e:
        logger.debug("level update skipped: %s", e)


async def show_on(page: Page) -> None:
    """Call after switching the active tab (new_tab/switch_tab/close_tab/undo) so the
    overlay on the newly-active page reflects whatever state (and minimized flag) is
    already current, and bring_to_front()s it so the browser UI actually switches tabs."""
    try:
        await page.bring_to_front()
    except Exception as e:
        logger.debug("bring_to_front skipped: %s", e)
    await _wire(page)
    await _apply(page)


def describe_action(d: Decision, target_name: str) -> str:
    """Human-readable one-liner for the "acting" state, e.g. 'Clicking "Sign up"'."""
    name = target_name or "that"
    if d.intent == "click":
        return f'Clicking "{name}"'
    if d.intent == "type":
        return f'Typing "{d.text}" into "{name}"'
    if d.intent == "select":
        return f'Selecting "{d.text}" in "{name}"'
    if d.intent == "scroll":
        # main.py actually uses executor.act.scroll()'s own result text instead of this
        # (it knows whether the scroll really moved, hit an edge, etc.) -- kept correct
        # here too in case this is ever the only text available (e.g. a PREVIEW).
        if d.scroll_to == "top":
            return "Scrolling to the top"
        if d.scroll_to == "bottom":
            return "Scrolling to the bottom"
        return f'Scrolling {"up" if d.scroll_amount < 0 else "down"}'
    if d.intent == "navigate":
        return f"Going to {d.text}"
    if d.intent == "back":
        return "Going back"
    if d.intent == "forward":
        return "Going forward"
    if d.intent == "search":
        return f"Searching: {d.text}"
    if d.intent == "find":
        return f'Looking for "{d.text}" on the page'
    if d.intent == "new_tab":
        return "Opening a new tab"
    if d.intent == "close_tab":
        return "Closing this tab"
    if d.intent == "switch_tab":
        return "Switching tabs"
    if d.intent == "accessibility":
        return {
            "zoom_in": "Making text bigger", "zoom_out": "Making text smaller",
            "high_contrast_on": "Turning on high contrast", "high_contrast_off": "Turning off high contrast",
            "reset": "Resetting the view",
        }.get(d.text or "", "Adjusting accessibility")
    return f"Doing {d.intent}"


def describe_not_found(what: str) -> str:
    """Error message that says what failed AND suggests a next step."""
    return f'I couldn\'t find "{what}" on this page. Try saying it differently?'


def describe_failure(action: str) -> str:
    return f"I couldn't {action}. Try saying it differently?"


def current_state() -> str:
    """The state currently shown on the overlay (e.g. "listening"), for callers that
    need to know whether a live transcript is still on screen."""
    return _current[0]


async def _apply(page: Page) -> None:
    state, text = _current
    try:
        await page.evaluate(
            "([s, t, m, h]) => { if (window.__vox) { window.__vox.set(s, t, undefined, m); "
            "if (window.__vox.setHistory) window.__vox.setHistory(h); } }",
            [state, text, _minimized, _history],
        )
    except Exception as e:
        logger.debug("overlay apply skipped: %s", e)


async def _wire(page: Page) -> None:
    if page in _wired_pages:
        return
    _wired_pages.add(page)
    try:
        await page.evaluate(OVERLAY_JS)
    except Exception as e:
        logger.debug("overlay mount skipped: %s", e)

    def _on_load(_page: Page) -> None:
        asyncio.ensure_future(_apply(page))

    def _on_framenavigated(frame) -> None:
        if frame == page.main_frame:
            asyncio.ensure_future(_apply(page))

    page.on("load", _on_load)
    page.on("framenavigated", _on_framenavigated)
