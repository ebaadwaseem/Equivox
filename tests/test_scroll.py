"""executor.act.scroll(): page-based, smooth, container-aware scrolling."""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from backend.spans import DEFAULT_SCROLL_FRACTION
from executor.act import scroll

LONG_PAGE = (Path(__file__).parent / "fixtures" / "pages" / "long_page.html").resolve().as_uri()
INNER_SCROLL = (Path(__file__).parent / "fixtures" / "pages" / "inner_scroll.html").resolve().as_uri()


def _run(coro):
    return asyncio.run(coro)


async def _open(playwright, url, viewport=None):
    browser = await playwright.chromium.launch(headless=True)
    page = await browser.new_page(viewport=viewport or {"width": 1000, "height": 800})
    await page.goto(url)
    return browser, page


def test_default_scroll_moves_about_80_percent_of_viewport():
    async def scenario():
        async with async_playwright() as p:
            browser, page = await _open(p, LONG_PAGE)
            vh = await page.evaluate("() => document.scrollingElement.clientHeight")
            result = await scroll(page, DEFAULT_SCROLL_FRACTION, None)
            top = await page.evaluate("() => document.scrollingElement.scrollTop")
            await browser.close()
            return result, top, vh

    result, top, vh = _run(scenario())
    assert result == "Scrolling down"
    assert abs(top - 0.8 * vh) < 2


def test_a_bit_moves_about_30_percent_of_viewport():
    async def scenario():
        async with async_playwright() as p:
            browser, page = await _open(p, LONG_PAGE)
            vh = await page.evaluate("() => document.scrollingElement.clientHeight")
            await scroll(page, 0.3, None)
            top = await page.evaluate("() => document.scrollingElement.scrollTop")
            await browser.close()
            return top, vh

    top, vh = _run(scenario())
    assert abs(top - 0.3 * vh) < 2


def test_to_the_bottom_reaches_the_actual_end():
    async def scenario():
        async with async_playwright() as p:
            browser, page = await _open(p, LONG_PAGE)
            result = await scroll(page, 0, "bottom")
            top = await page.evaluate("() => document.scrollingElement.scrollTop")
            max_scroll = await page.evaluate(
                "() => document.scrollingElement.scrollHeight - document.scrollingElement.clientHeight"
            )
            await browser.close()
            return result, top, max_scroll

    result, top, max_scroll = _run(scenario())
    assert result == "Bottom of page"
    assert top == max_scroll


def test_to_the_top_reaches_zero_after_scrolling_down_first():
    async def scenario():
        async with async_playwright() as p:
            browser, page = await _open(p, LONG_PAGE)
            await scroll(page, 0, "bottom")
            result = await scroll(page, 0, "top")
            top = await page.evaluate("() => document.scrollingElement.scrollTop")
            await browser.close()
            return result, top

    result, top = _run(scenario())
    assert result == "Top of page"
    assert top == 0


def test_already_at_the_bottom_is_reported_not_silent():
    async def scenario():
        async with async_playwright() as p:
            browser, page = await _open(p, LONG_PAGE)
            await scroll(page, 0, "bottom")
            result = await scroll(page, DEFAULT_SCROLL_FRACTION, None)  # try to scroll further down
            await browser.close()
            return result

    assert _run(scenario()) == "Already at the bottom"


def test_already_at_the_top_is_reported():
    async def scenario():
        async with async_playwright() as p:
            browser, page = await _open(p, LONG_PAGE)
            result = await scroll(page, -DEFAULT_SCROLL_FRACTION, None)  # scroll up while already at top
            await browser.close()
            return result

    assert _run(scenario()) == "Already at the top"


def test_scroll_up_after_scrolling_down_moves_back_up():
    async def scenario():
        async with async_playwright() as p:
            browser, page = await _open(p, LONG_PAGE)
            await scroll(page, DEFAULT_SCROLL_FRACTION, None)
            top_after_down = await page.evaluate("() => document.scrollingElement.scrollTop")
            result = await scroll(page, -0.3, None)
            top_after_up = await page.evaluate("() => document.scrollingElement.scrollTop")
            await browser.close()
            return result, top_after_down, top_after_up

    result, down, up = _run(scenario())
    assert result == "Scrolling up"
    assert up < down


def test_inner_scroll_container_is_used_not_the_window():
    async def scenario():
        async with async_playwright() as p:
            browser, page = await _open(p, INNER_SCROLL)
            result = await scroll(page, DEFAULT_SCROLL_FRACTION, None)
            window_y = await page.evaluate("() => window.scrollY")
            inner_top = await page.evaluate("() => document.getElementById('inner-scroll').scrollTop")
            await browser.close()
            return result, window_y, inner_top

    result, window_y, inner_top = _run(scenario())
    assert result == "Scrolling down"
    assert window_y == 0  # the outer window never moved
    assert inner_top > 0  # the inner panel did


def test_inner_scroll_container_amount_relative_to_its_own_height():
    async def scenario():
        async with async_playwright() as p:
            browser, page = await _open(p, INNER_SCROLL)
            inner_height = await page.evaluate("() => document.getElementById('inner-scroll').clientHeight")
            await scroll(page, DEFAULT_SCROLL_FRACTION, None)
            inner_top = await page.evaluate("() => document.getElementById('inner-scroll').scrollTop")
            await browser.close()
            return inner_top, inner_height

    inner_top, inner_height = _run(scenario())
    assert abs(inner_top - 0.8 * inner_height) < 2


def test_inner_scroll_container_already_at_bottom_reported():
    async def scenario():
        async with async_playwright() as p:
            browser, page = await _open(p, INNER_SCROLL)
            await scroll(page, 0, "bottom")
            result = await scroll(page, DEFAULT_SCROLL_FRACTION, None)
            await browser.close()
            return result

    assert _run(scenario()) == "Already at the bottom"


def test_reduced_motion_uses_instant_behavior_but_still_scrolls_correctly():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(reduced_motion="reduce", viewport={"width": 1000, "height": 800})
            page = await ctx.new_page()
            await page.goto(LONG_PAGE)
            vh = await page.evaluate("() => document.scrollingElement.clientHeight")
            result = await scroll(page, DEFAULT_SCROLL_FRACTION, None)
            top = await page.evaluate("() => document.scrollingElement.scrollTop")
            await browser.close()
            return result, top, vh

    result, top, vh = _run(scenario())
    assert result == "Scrolling down"
    assert abs(top - 0.8 * vh) < 2
