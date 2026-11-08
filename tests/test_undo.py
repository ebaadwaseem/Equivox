"""executor/undo.py: "go back"/"undo" consults the action log instead of always
calling page.go_back() on whatever tab happens to be active."""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from executor.undo import ActionLogEntry, undo_last

FORM_URL = "data:text/html,<a id='l' href='%23two'>go</a><div id='two'>two</div>"
POPUP_FIXTURE = (Path(__file__).parent / "fixtures" / "pages" / "popup.html").resolve().as_uri()


def _run(coro):
    return asyncio.run(coro)


def test_undo_with_empty_log_falls_back_to_go_back():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto("data:text/html,<p>a</p>")
            await page.goto("data:text/html,<p>b</p>")
            new_page, desc = await undo_last(ctx, [], page)
            await browser.close()
            return new_page is page, desc

    same_page, desc = _run(scenario())
    assert same_page
    assert desc == "Going back"


def test_undo_new_tab_closes_it_and_returns_to_opener():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            opener = await ctx.new_page()
            await opener.goto("data:text/html,<p>opener</p>")
            new_tab = await ctx.new_page()
            await new_tab.goto("data:text/html,<p>new tab</p>")
            log = [ActionLogEntry(kind="new_tab", page=new_tab, previous_page=opener, label="the previous tab")]

            active, desc = await undo_last(ctx, log, new_tab)
            tabs_remaining = len(ctx.pages)
            await browser.close()
            return active is opener, tabs_remaining, desc, log

    is_opener, remaining, desc, log = _run(scenario())
    assert is_opener
    assert remaining == 1  # the new tab got closed
    assert "Closing that tab" in desc
    assert log == []  # entry was popped


def test_undo_popup_same_as_new_tab():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            opener = await ctx.new_page()
            popup = await ctx.new_page()
            log = [ActionLogEntry(kind="popup", page=popup, previous_page=opener, label="DuckDuckGo")]
            active, desc = await undo_last(ctx, log, popup)
            remaining = len(ctx.pages)
            await browser.close()
            return active is opener, remaining, desc

    is_opener, remaining, desc = _run(scenario())
    assert is_opener
    assert remaining == 1
    assert "DuckDuckGo" in desc


def test_undo_switch_tab_switches_back_without_closing():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            page_a = await ctx.new_page()
            page_b = await ctx.new_page()
            log = [ActionLogEntry(kind="switch_tab", page=page_b, previous_page=page_a, label="tab A")]
            active, desc = await undo_last(ctx, log, page_b)
            remaining = len(ctx.pages)
            await browser.close()
            return active is page_a, remaining, desc

    is_a, remaining, desc = _run(scenario())
    assert is_a
    assert remaining == 2  # nothing closed
    assert "Switching back" in desc


def test_undo_close_tab_reopens_at_url_before():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            page = await ctx.new_page()
            url = "data:text/html,<p>revived</p>"
            log = [ActionLogEntry(kind="close_tab", url_before=url, label="the old tab")]
            active, desc = await undo_last(ctx, log, page)
            content = await active.content()
            await browser.close()
            return "revived" in content, desc

    found, desc = _run(scenario())
    assert found
    assert "Reopening" in desc


def test_undo_click_goes_back_on_that_tab():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto("data:text/html,<p>first</p>")
            await page.goto("data:text/html,<p>second</p>")
            log = [ActionLogEntry(kind="click", page=page, label="first")]
            active, desc = await undo_last(ctx, log, page)
            content = await active.content()
            await browser.close()
            return "first" in content, desc

    found, desc = _run(scenario())
    assert found
    assert "Going back" in desc


# ---- end-to-end: link opening a new tab vs. a normal same-tab link --------
# Mirrors main.py's actual wiring: ctx.on("page") detects popups via Page.opener(),
# pushes an ActionLogEntry, and "go back" reverses whichever kind of action was last.

async def _detect_popup_and_log(ctx, action_log, active_page_box):
    async def on_new_page(new_page):
        opener = await new_page.opener()
        if opener is None:
            return
        previous = active_page_box[0]
        await new_page.wait_for_load_state("domcontentloaded")
        active_page_box[0] = new_page
        action_log.append(ActionLogEntry(kind="popup", page=new_page, previous_page=previous, label=previous.url))

    ctx.on("page", lambda p: asyncio.ensure_future(on_new_page(p)))


def test_link_opening_new_tab_then_go_back_returns_to_original_tab():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(POPUP_FIXTURE)
            action_log: list[ActionLogEntry] = []
            active_page_box = [page]
            await _detect_popup_and_log(ctx, action_log, active_page_box)

            await page.click("#blank-link")
            await asyncio.sleep(0.3)  # let the "page" event handler run
            assert len(ctx.pages) == 2
            assert active_page_box[0] is not page  # popup became active

            new_active, desc = await undo_last(ctx, action_log, active_page_box[0])
            tabs_remaining = len(ctx.pages)
            is_original = new_active is page
            await browser.close()
            return is_original, tabs_remaining, desc

    is_original, tabs_remaining, desc = _run(scenario())
    assert is_original
    assert tabs_remaining == 1
    assert "Closing that tab" in desc


def test_normal_link_then_go_back_goes_back_in_same_tab():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await page.goto(POPUP_FIXTURE)
            action_log: list[ActionLogEntry] = []
            active_page_box = [page]
            await _detect_popup_and_log(ctx, action_log, active_page_box)

            url_before = page.url
            await page.click("#normal-link")
            await page.wait_for_load_state("load")
            assert len(ctx.pages) == 1  # no new tab opened
            action_log.append(ActionLogEntry(kind="click", page=page, url_before=url_before, label="Normal link"))

            new_active, desc = await undo_last(ctx, action_log, page)
            back_url = new_active.url
            tabs_remaining = len(ctx.pages)
            await browser.close()
            return back_url, tabs_remaining, desc, url_before

    back_url, tabs_remaining, desc, url_before = _run(scenario())
    assert back_url == url_before  # went back within the SAME tab
    assert tabs_remaining == 1  # nothing closed
    assert "Going back" in desc
