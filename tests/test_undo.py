"""executor/undo.py: checkpoint-based "go back"/"undo".

Pure-function tests use plain sentinel objects standing in for Page identity (the
logic only needs `is`-identity, never calls Playwright methods on them). The
end-to-end tests at the bottom drive a real browser through the 5 scenarios called
out explicitly: JS redirect trap, meta-refresh trap, target=_blank popup,
site-opened popup, and a double "go back".
"""
import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from executor.undo import (
    Checkpoint,
    describe_candidate,
    describe_checkpoint,
    execute_undo,
    find_candidates,
    find_target,
    normalize_url,
    push_checkpoint,
)

PAGES = Path(__file__).parent / "fixtures" / "pages"


def _uri(name: str) -> str:
    return (PAGES / name).resolve().as_uri()


class _FakePage:
    """Stands in for Page identity in pure-logic tests; carries a .url for matching."""
    def __init__(self, url: str):
        self.url = url


def _cp(page, url=None, title="") -> Checkpoint:
    return Checkpoint(page=page, url=url or page.url, title=title)


# ---- normalize_url ---------------------------------------------------------

def test_normalize_url_strips_fragment():
    assert normalize_url("https://example.com/page#section") == "https://example.com/page"


def test_normalize_url_strips_utm_params():
    assert normalize_url("https://example.com/?utm_source=x&id=5") == "https://example.com/?id=5"


def test_normalize_url_keeps_non_tracking_params():
    assert normalize_url("https://example.com/?q=hello") == "https://example.com/?q=hello"


def test_normalize_url_equal_after_stripping():
    a = normalize_url("https://example.com/p?utm_campaign=x#top")
    b = normalize_url("https://example.com/p")
    assert a == b


# ---- find_target / find_candidates -----------------------------------------

def test_find_target_most_recent_differing_same_tab():
    page = _FakePage("https://x.com/c")
    checkpoints = [_cp(page, "https://x.com/a"), _cp(page, "https://x.com/b")]
    idx = find_target(checkpoints, page)
    assert idx == 1  # most recent (b), not a


def test_find_target_skips_identical_checkpoints():
    page = _FakePage("https://x.com/c")
    checkpoints = [_cp(page, "https://x.com/a"), _cp(page, "https://x.com/c")]  # same as current
    idx = find_target(checkpoints, page)
    assert idx == 0  # skips the identical one, finds 'a'


def test_find_target_none_when_nothing_differs():
    page = _FakePage("https://x.com/a")
    checkpoints = [_cp(page, "https://x.com/a")]
    assert find_target(checkpoints, page) is None


def test_find_target_cross_tab():
    tab_a = _FakePage("https://x.com/a")
    tab_b = _FakePage("https://x.com/b")
    checkpoints = [_cp(tab_a, "https://x.com/a")]
    idx = find_target(checkpoints, tab_b)
    assert idx == 0


def test_find_target_same_tab_only_ignores_other_tabs():
    tab_a = _FakePage("https://x.com/a")
    tab_b = _FakePage("https://x.com/b")
    checkpoints = [_cp(tab_a, "https://x.com/a"), _cp(tab_b, "https://x.com/prev-b")]
    idx = find_target(checkpoints, tab_b, same_tab_only=True)
    assert idx == 1  # the tab_a entry is ignored


def test_find_candidates_single_when_same_tab_only_option():
    page = _FakePage("https://x.com/c")
    checkpoints = [_cp(page, "https://x.com/a")]
    assert find_candidates(checkpoints, page) == [0]


def test_find_candidates_two_when_cross_tab_and_same_tab_both_plausible():
    tab_a = _FakePage("https://x.com/a")
    tab_b = _FakePage("https://x.com/b")
    checkpoints = [_cp(tab_a, "https://x.com/prev-a"), _cp(tab_b, "https://x.com/b")]
    # most recent checkpoint is for tab_b itself (index 1) but matches current URL,
    # so it's skipped by find_target; the cross-tab one (tab_a, index 0) is primary
    cands = find_candidates(checkpoints, tab_b)
    assert 0 in cands


def test_describe_checkpoint_prefers_title():
    page = _FakePage("https://example.com/")
    cp = _cp(page, title="My Page")
    assert describe_checkpoint(cp) == "My Page"


def test_describe_checkpoint_falls_back_to_hostname():
    page = _FakePage("https://example.com/path")
    cp = _cp(page, title="")
    assert describe_checkpoint(cp) == "example.com"


def test_describe_candidate_same_tab_phrasing():
    page = _FakePage("https://x.com/a")
    checkpoints = [_cp(page, "https://x.com/prev", title="Prev")]
    desc = describe_candidate(checkpoints, 0, page)
    assert "previous page" in desc
    assert "Prev" in desc


def test_describe_candidate_cross_tab_phrasing():
    tab_a = _FakePage("https://x.com/a")
    tab_b = _FakePage("https://x.com/b")
    checkpoints = [_cp(tab_a, title="Tab A")]
    desc = describe_candidate(checkpoints, 0, tab_b)
    assert "close this tab" in desc
    assert "Tab A" in desc


# ---- end-to-end: the 5 explicit scenarios ----------------------------------

def _run(coro):
    return asyncio.run(coro)


def test_js_redirect_trap_go_back_lands_on_a_not_b():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(_uri("redirect_a.html"))

            checkpoints: list[Checkpoint] = []
            await push_checkpoint(checkpoints, page)  # user is about to click
            await page.click("#link-to-b")
            await page.wait_for_load_state("load")
            await asyncio.sleep(0.5)  # let the JS redirect to C fire
            assert "redirect_c" in page.url  # landed on C, past the B redirect trap

            idx = find_target(checkpoints, page)
            assert idx is not None
            new_page, desc = await execute_undo(ctx, checkpoints, idx, page)
            landed_on_a = "redirect_a" in new_page.url
            await browser.close()
            return landed_on_a, desc

    landed_on_a, desc = _run(scenario())
    assert landed_on_a
    assert "Going back" in desc


def test_meta_refresh_trap_go_back_lands_on_a_not_b():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(_uri("redirect_a.html"))

            checkpoints: list[Checkpoint] = []
            await push_checkpoint(checkpoints, page)
            await page.click("#link-to-meta-b")
            await page.wait_for_load_state("load")
            await asyncio.sleep(0.5)
            assert "redirect_c" in page.url

            idx = find_target(checkpoints, page)
            new_page, desc = await execute_undo(ctx, checkpoints, idx, page)
            landed_on_a = "redirect_a" in new_page.url
            await browser.close()
            return landed_on_a

    assert _run(scenario())


def test_target_blank_link_go_back_closes_new_tab_returns_to_opener():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            opener = await ctx.new_page()
            await opener.goto(_uri("popup.html"))

            checkpoints: list[Checkpoint] = []
            active_box = [opener]
            popup_ready = asyncio.Event()

            async def on_new_page(new_page):
                o = await new_page.opener()
                if o is None:
                    return
                await new_page.wait_for_load_state("domcontentloaded")
                active_box[0] = new_page
                popup_ready.set()

            ctx.on("page", lambda p: asyncio.ensure_future(on_new_page(p)))

            await push_checkpoint(checkpoints, opener)  # user about to click
            await opener.click("#blank-link")
            await asyncio.wait_for(popup_ready.wait(), timeout=3)
            assert len(ctx.pages) == 2
            popup = active_box[0]
            assert popup is not opener

            idx = find_target(checkpoints, popup)
            new_page, desc = await execute_undo(ctx, checkpoints, idx, popup)
            is_opener = new_page is opener
            tabs_remaining = len(ctx.pages)
            await browser.close()
            return is_opener, tabs_remaining, desc

    is_opener, remaining, desc = _run(scenario())
    assert is_opener
    assert remaining == 1
    assert "Going back" in desc


def test_site_opened_popup_on_load_go_back_closes_it():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            opener = await ctx.new_page()

            checkpoints: list[Checkpoint] = []
            active_box = [opener]

            async def on_new_page(new_page):
                o = await new_page.opener()
                if o is None:
                    return
                new_page.site_opened = True  # no recent user command drove this
                await new_page.wait_for_load_state("domcontentloaded")
                active_box[0] = new_page

            ctx.on("page", lambda p: asyncio.ensure_future(on_new_page(p)))

            await push_checkpoint(checkpoints, opener)  # user navigates to the auto-popup page
            await opener.goto(_uri("popup_on_load.html"))
            await asyncio.sleep(0.5)  # let the page's own window.open() fire
            assert len(ctx.pages) == 2
            popup = active_box[0]
            assert popup is not opener
            assert getattr(popup, "site_opened", False) is True

            idx = find_target(checkpoints, popup)
            new_page, desc = await execute_undo(ctx, checkpoints, idx, popup)
            is_opener = new_page is opener
            tabs_remaining = len(ctx.pages)
            await browser.close()
            return is_opener, tabs_remaining

    is_opener, remaining = _run(scenario())
    assert is_opener
    assert remaining == 1


def test_go_back_twice_walks_two_checkpoints():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            page = await ctx.new_page()

            checkpoints: list[Checkpoint] = []

            await page.goto(_uri("redirect_a.html"))
            await push_checkpoint(checkpoints, page)  # checkpoint 1: on A
            await page.goto(_uri("redirect_c.html"))  # pretend-command: navigate to C
            await push_checkpoint(checkpoints, page)  # checkpoint 2: on C
            await page.goto(_uri("second.html"))  # pretend-command: navigate to "second"

            # first "go back": from second.html -> should land on C (checkpoint 2)
            idx1 = find_target(checkpoints, page)
            page, desc1 = await execute_undo(ctx, checkpoints, idx1, page)
            first_url = page.url

            # second "go back again": from C -> should land on A (checkpoint 1)
            idx2 = find_target(checkpoints, page)
            page, desc2 = await execute_undo(ctx, checkpoints, idx2, page)
            second_url = page.url

            await browser.close()
            return first_url, second_url

    first_url, second_url = _run(scenario())
    assert "redirect_c" in first_url
    assert "redirect_a" in second_url
