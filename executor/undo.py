"""Smart "go back"/"undo": consults a chronological action log instead of always
calling page.go_back() on whatever tab happens to be active. Undoing a popup/new
tab should close it and return to the opener; undoing a tab switch should switch
back; undoing a same-tab click/navigate should go back within that tab; undoing a
closed tab should reopen it. Every action gets pushed onto ONE stack in order, so
"undo" always just pops the last entry and reverses whatever it was.
"""
from dataclasses import dataclass
from typing import Optional

from playwright.async_api import BrowserContext, Page

Kind = str  # click | navigate | new_tab | popup | switch_tab | close_tab


@dataclass
class ActionLogEntry:
    kind: Kind
    page: Optional[Page] = None            # the tab this action happened on/created
    previous_page: Optional[Page] = None   # for switch_tab/new_tab/popup: the tab we came from
    url_before: Optional[str] = None       # for close_tab: what to reopen
    label: str = ""                        # short description for Vox, e.g. a site name


async def undo_last(ctx: BrowserContext, action_log: list[ActionLogEntry], active_page: Page) -> tuple[Page, str]:
    """Pops and reverses the last logged action. Returns (new_active_page, description)."""
    if not action_log:
        try:
            await active_page.go_back()
        except Exception:
            pass
        return active_page, "Going back"

    entry = action_log.pop()
    where = entry.label or "the previous page"

    if entry.kind in ("new_tab", "popup"):
        target = entry.previous_page or active_page
        try:
            await entry.page.close()
        except Exception:
            pass
        await target.bring_to_front()
        return target, f"Closing that tab, back to {where}"

    if entry.kind == "switch_tab":
        target = entry.previous_page or active_page
        await target.bring_to_front()
        return target, f"Switching back to {where}"

    if entry.kind == "close_tab":
        new_page = await ctx.new_page()
        if entry.url_before:
            try:
                await new_page.goto(entry.url_before)
            except Exception:
                pass
        await new_page.bring_to_front()
        return new_page, f"Reopening {entry.label or entry.url_before or 'that tab'}"

    if entry.kind in ("click", "navigate"):
        page = entry.page or active_page
        await page.bring_to_front()
        try:
            await page.go_back()
        except Exception:
            pass
        return page, f"Going back to {where}"

    return active_page, "Going back"
