"""Site-aware "search for X": types into the CURRENT page's own search box when it has
one (and isn't itself a generic search engine), unless explicitly forced to the web, or
an explicit site is named. Pure extraction is tested first; the bottom half hits the
REAL wikipedia.org and youtube.com, as explicitly requested -- both have a well-known,
stable search box (Wikipedia: type=search name=search; YouTube: name=search_query),
confirmed against their live markup while writing this test.
"""
import asyncio

import pytest
from playwright.async_api import async_playwright

from backend.model import decide
from backend.spans import extract_site_search, is_forced_web_search
from executor.act import execute
from perception.perceive import find_own_search_box, get_page_state, shortlist, to_prompt_text
from shared.protocol import Element


# ---- pure extraction -------------------------------------------------------------------

def test_extract_site_search_youtube():
    assert extract_site_search("search youtube for lofi music") == ("youtube", "lofi music")


def test_extract_site_search_wikipedia():
    assert extract_site_search("search wikipedia for golisopod") == ("wikipedia", "golisopod")


def test_extract_site_search_amazon():
    assert extract_site_search("search amazon for desk lamp") == ("amazon", "desk lamp")


def test_extract_site_search_none_for_plain_search():
    assert extract_site_search("search for pizza") is None


def test_is_forced_web_search_variants():
    assert is_forced_web_search("search the web for golisopod")
    assert is_forced_web_search("search online for golisopod")
    assert is_forced_web_search("search duckduckgo for golisopod")
    assert is_forced_web_search("google golisopod")


def test_is_forced_web_search_false_for_plain_search():
    assert not is_forced_web_search("search for golisopod")


# ---- find_own_search_box: pure, synthetic elements -------------------------------------

def test_find_own_search_box_by_input_type():
    box = Element(id="e1", role="textbox", name="", input_type="search")
    assert find_own_search_box([box], "https://wikipedia.org/wiki/X") is box


def test_find_own_search_box_by_name_attribute():
    box = Element(id="e1", role="textbox", name="", input_name="search_query")
    assert find_own_search_box([box], "https://www.youtube.com") is box


def test_find_own_search_box_none_on_generic_search_engine():
    box = Element(id="e1", role="textbox", name="", input_type="search")
    assert find_own_search_box([box], "https://duckduckgo.com/html/") is None
    assert find_own_search_box([box], "https://www.google.com/") is None


def test_find_own_search_box_none_when_no_candidates():
    other = Element(id="e1", role="textbox", name="Name", input_type="text")
    assert find_own_search_box([other], "https://example.com") is None


def test_find_own_search_box_prefers_focused():
    a = Element(id="e1", role="textbox", name="", input_type="search", active=False)
    b = Element(id="e2", role="textbox", name="", input_type="search", active=True)
    assert find_own_search_box([a, b], "https://example.com") is b


# ---- live: real wikipedia.org and youtube.com ------------------------------------------

def _run(coro):
    return asyncio.run(coro)


async def _decide_and_execute(page, text: str):
    state = await get_page_state(page)
    cands = shortlist(state, text)
    vp = page.viewport_size
    prompt_text = to_prompt_text(cands, (vp["width"], vp["height"]) if vp else None)
    page_info = {"url": state.url, "title": state.title, "site": "", "summary": "", "tabs": []}
    d = await decide(text, True, prompt_text, cands, page=page_info)
    result = await execute(page, d)
    return d, result


def test_search_for_on_wikipedia_uses_its_own_search_box():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto("https://en.wikipedia.org/wiki/Golisopod", wait_until="domcontentloaded")
            d, result = await _decide_and_execute(page, "search for shield bug")
            await browser.close()
            return d, result, page.url

    d, result, url = _run(scenario())
    assert d.intent == "search"
    assert d.search_site is None
    assert "duckduckgo" not in url
    assert "wikipedia.org" in url
    assert "Searched this page" in result


def test_search_the_web_for_on_wikipedia_still_forces_external_search():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto("https://en.wikipedia.org/wiki/Golisopod", wait_until="domcontentloaded")
            d, result = await _decide_and_execute(page, "search the web for shield bug")
            await browser.close()
            return d, page.url

    d, url = _run(scenario())
    assert d.intent == "search"
    assert "duckduckgo.com" in url


def test_search_for_on_youtube_uses_its_own_search_box():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto("https://www.youtube.com", wait_until="networkidle")  # heavy SPA; the search box hydrates late
            d, result = await _decide_and_execute(page, "search for lofi music")
            await browser.close()
            return d, result, page.url

    d, result, url = _run(scenario())
    assert d.intent == "search"
    assert d.search_site is None
    assert "duckduckgo" not in url
    assert "youtube.com" in url
    assert "Searched this page" in result


def test_search_youtube_for_from_an_unrelated_page_builds_the_youtube_url():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto("https://en.wikipedia.org/wiki/Golisopod", wait_until="domcontentloaded")
            d, result = await _decide_and_execute(page, "search youtube for lofi music")
            await browser.close()
            return d, page.url

    d, url = _run(scenario())
    assert d.intent == "search"
    assert d.search_site == "youtube"
    assert "youtube.com/results" in url
    assert "search_query=lofi+music" in url
