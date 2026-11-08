"""Drives the on-screen Vox overlay (ui/overlay.js) from Python.

The overlay's DOM is rebuilt by the browser on every new document, so this module
remembers the single current (state, text) and re-applies it whenever a page needs
it: right after `set()`, after that page's own navigations, and after main.py switches
which page is "active" (new tab / tab switch). Never raises -- a missing overlay
(not mounted yet, page mid-navigation) just means the call is a no-op.
"""
import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

from playwright.async_api import Page

from shared.protocol import Decision

logger = logging.getLogger("vox")

OVERLAY_JS = (Path(__file__).parent / "overlay.js").read_text()

_current: tuple[str, str] = ("idle", "")
_version = 0
_wired_pages: set[int] = set()
_on_command: Optional[Callable[[str], Awaitable[None]]] = None


async def install(context, on_command: Optional[Callable[[str], Awaitable[None]]] = None) -> None:
    """Call once per BrowserContext: injects the overlay into every current/future page.
    If `on_command` is given, the overlay's typed-command box will call it with the
    text the user typed (wired via page.expose_function, per page)."""
    global _on_command
    _on_command = on_command
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


async def show_on(page: Page) -> None:
    """Call after switching the active tab (new_tab/switch_tab/close_tab) so the
    overlay on the newly-active page reflects whatever state is already current."""
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


async def _apply(page: Page) -> None:
    state, text = _current
    try:
        await page.evaluate("([s, t]) => window.__vox && window.__vox.set(s, t)", [state, text])
    except Exception as e:
        logger.debug("overlay apply skipped: %s", e)


async def _wire(page: Page) -> None:
    if id(page) in _wired_pages:
        return
    _wired_pages.add(id(page))

    def _on_load(_page: Page) -> None:
        asyncio.ensure_future(_apply(page))

    def _on_framenavigated(frame) -> None:
        if frame == page.main_frame:
            asyncio.ensure_future(_apply(page))

    page.on("load", _on_load)
    page.on("framenavigated", _on_framenavigated)

    if _on_command is not None:
        async def _handle_command(text: str) -> None:
            try:
                await _on_command(text)
            except Exception as e:
                logger.warning("vox command handler failed: %s", e)

        try:
            await page.expose_function("voxCommand", _handle_command)
        except Exception as e:
            logger.debug("expose_function skipped: %s", e)
