"""Smart "go back"/"undo": a naive page.go_back() is one raw history step, but users
mean "back to what I was looking at before my last command" -- which breaks on
JS/meta-refresh redirect traps (the step right after your command isn't where you
want to be), site-initiated navigations, and site-opened popups.

The fix is CHECKPOINTS: before executing every user-issued command, we snapshot
where they were (tab, url, title, scroll position). Site-initiated navigations,
redirects, and popups never create a checkpoint. "go back" finds the most recent
checkpoint that actually differs from where we are now and reverses to it --
skipping straight past any redirect chain or popup in between, since those never
got their own checkpoint.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import BrowserContext, Page

from backend import jev

logger = logging.getLogger("undo")

AMBIGUITY_CONFIDENCE_THRESHOLD = 0.7
MAX_BACK_STEPS = 5
SETTLE_DELAY_S = 1.0


def normalize_url(url: str) -> str:
    """Strip #fragments and utm_* tracking params so near-identical URLs compare equal."""
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))


@dataclass
class Checkpoint:
    page: Page
    url: str
    title: str
    scroll_y: float = 0
    ts: float = field(default_factory=time.time)


async def push_checkpoint(checkpoints: list["Checkpoint"], page: Page) -> None:
    """Call right before executing a user-issued command -- never for site-initiated
    navigation/redirects/popups."""
    try:
        scroll_y = await page.evaluate("() => window.scrollY")
    except Exception:
        scroll_y = 0
    try:
        title = await page.title()
    except Exception:
        title = ""
    checkpoints.append(Checkpoint(page=page, url=page.url, title=title, scroll_y=scroll_y or 0, ts=time.time()))


def _differs(cp: Checkpoint, active_page: Page) -> bool:
    if cp.page is not active_page:
        return True
    return normalize_url(cp.url) != normalize_url(active_page.url)


def find_target(checkpoints: list[Checkpoint], active_page: Page, same_tab_only: bool = False) -> Optional[int]:
    """Index into `checkpoints` of the most recent entry that differs (by tab or
    normalized URL) from the current state. None if nothing qualifies."""
    for i in range(len(checkpoints) - 1, -1, -1):
        cp = checkpoints[i]
        if same_tab_only and cp.page is not active_page:
            continue
        if _differs(cp, active_page):
            return i
    return None


def find_candidates(checkpoints: list[Checkpoint], active_page: Page) -> list[int]:
    """Up to 2 plausible "go back" targets, for ambiguity detection: the single most
    recent differing checkpoint, plus -- if that one is on a different tab -- the
    most recent differing checkpoint on the CURRENT tab, if one also exists."""
    primary = find_target(checkpoints, active_page)
    if primary is None:
        return []
    candidates = [primary]
    if checkpoints[primary].page is not active_page:
        same_tab = find_target(checkpoints, active_page, same_tab_only=True)
        if same_tab is not None and same_tab != primary:
            candidates.append(same_tab)
    return candidates


def describe_checkpoint(cp: Checkpoint) -> str:
    return cp.title or urlsplit(cp.url).hostname or cp.url


def describe_candidate(checkpoints: list[Checkpoint], index: int, active_page: Page) -> str:
    cp = checkpoints[index]
    label = describe_checkpoint(cp)
    if cp.page is active_page:
        return f'previous page: "{label}"'
    return f'close this tab and return to "{label}"'


async def resolve_ambiguity(
    model: str, checkpoints: list[Checkpoint], candidate_indices: list[int], active_page: Page
) -> tuple[Optional[int], float]:
    """Asks Jev to pick between 2+ plausible "go back" targets. Returns
    (chosen_index, confidence); confidence 0.0 if Jev is unavailable/fails, in which
    case the caller should treat it as ambiguous and ask the user."""
    if not candidate_indices:
        return None, 1.0
    if len(candidate_indices) == 1:
        return candidate_indices[0], 1.0

    criteria = {str(idx): describe_candidate(checkpoints, idx, active_page) for idx in candidate_indices}
    answers = await jev.ask(
        model,
        'The user said "go back" or "undo".',
        {"target": {
            "type": "choice",
            "instructions": 'Which of these did the user most likely mean by "go back"?',
            "criteria": criteria,
        }},
    )
    if not answers:
        return candidate_indices[0], 0.0
    choice_key, confidence, _ = jev.choice(answers, "target")
    try:
        idx = int(choice_key)
    except (TypeError, ValueError):
        return candidate_indices[0], 0.0
    if idx not in candidate_indices:
        return candidate_indices[0], 0.0
    return idx, confidence


def _tab_is_disposable(checkpoints: list[Checkpoint], page: Page) -> bool:
    """True if the user never issued a command while this tab was active -- i.e. it's
    a popup/new tab nobody actually did anything with, safe to close on "go back"."""
    if getattr(page, "site_opened", False):
        return True
    return not any(cp.page is page for cp in checkpoints)


async def execute_undo(ctx: BrowserContext, checkpoints: list[Checkpoint], index: int, active_page: Page) -> tuple[Page, str]:
    """Pops checkpoint `index` (and everything after it, so "go back again" continues
    further back) and reverses to it."""
    target = checkpoints[index]
    cross_tab = target.page is not active_page
    disposable = cross_tab and _tab_is_disposable(checkpoints, active_page)
    del checkpoints[index:]
    label = describe_checkpoint(target)

    if cross_tab:
        if disposable:
            try:
                await active_page.close()
            except Exception as e:
                logger.debug("could not close disposable tab: %s", e)
        try:
            await target.page.bring_to_front()
        except Exception as e:
            logger.debug("bring_to_front failed: %s", e)
        return target.page, f"Going back to {label}"

    page = active_page
    for _ in range(MAX_BACK_STEPS):
        try:
            await page.go_back(timeout=5000)
        except Exception:
            break
        try:
            await page.wait_for_load_state("load", timeout=5000)
        except Exception:
            pass
        await asyncio.sleep(SETTLE_DELAY_S)  # let a same-page redirect bounce settle
        if normalize_url(page.url) == normalize_url(target.url):
            break
    if normalize_url(page.url) != normalize_url(target.url):
        try:
            await page.goto(target.url, timeout=10000)
        except Exception as e:
            logger.debug("goto fallback failed: %s", e)
    if target.scroll_y:
        try:
            await page.evaluate("y => window.scrollTo(0, y)", target.scroll_y)
        except Exception:
            pass
    return page, f"Going back to {label}"
