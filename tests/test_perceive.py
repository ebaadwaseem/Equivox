"""Tests for perception/perceive.py (pure-function shortlist/prompt logic + the
observer.js extraction behavior against tests/fixtures/pages/form.html)."""
import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from perception.perceive import build_choice_criteria, find_deterministic_target, get_page_state, shortlist, to_prompt_text
from shared.protocol import Element, PageState

FORM_FIXTURE = (Path(__file__).parent / "fixtures" / "pages" / "form.html").resolve().as_uri()
RESULTS_FIXTURE = (Path(__file__).parent / "fixtures" / "pages" / "results.html").resolve().as_uri()
OVERLAY_JS = (Path(__file__).parent.parent / "ui" / "overlay.js").read_text()


def make_state(*elements: Element) -> PageState:
    return PageState(url="https://example.com", title="t", elements=list(elements))


# ---- shortlist: pure-function behavior -----------------------------------

def test_shortlist_lexical_overlap_ranks_first():
    e_name = Element(id="e1", role="textbox", name="Full name")
    e_email = Element(id="e2", role="textbox", name="Email")
    e_decoy = Element(id="e3", role="link", name="Home", in_viewport=False)
    state = make_state(e_decoy, e_email, e_name)
    result = shortlist(state, "type Sarah Chen in the name field")
    assert result[0].id == "e1"


def test_shortlist_stopwords_dont_dominate():
    # "the" and "field" shouldn't make an unrelated element with "the" in its name outrank
    # a directly-named match.
    e_terms = Element(id="e1", role="checkbox", name="I agree to the terms")
    e_name = Element(id="e2", role="textbox", name="Full name")
    state = make_state(e_terms, e_name)
    result = shortlist(state, "type Sarah Chen in the name field")
    assert result[0].id == "e2"


def test_shortlist_ordinal_second_link():
    links = [Element(id=f"e{i}", role="link", name=f"Link {i}") for i in range(1, 4)]
    state = make_state(*links)
    result = shortlist(state, "click the second link")
    assert result[0].id == "e2"


def test_shortlist_ordinal_last():
    links = [Element(id=f"e{i}", role="link", name=f"Link {i}") for i in range(1, 4)]
    state = make_state(*links)
    result = shortlist(state, "open the last link")
    assert result[0].id == "e3"


def test_shortlist_ordinal_respects_role_word():
    btn = Element(id="eb", role="button", name="Submit")
    links = [Element(id=f"e{i}", role="link", name=f"Link {i}") for i in range(1, 4)]
    state = make_state(btn, *links)
    # "second result" should count only links (role word "result" -> link), skipping the button
    result = shortlist(state, "open the second result")
    assert result[0].id == "e2"


def test_shortlist_role_word_plural_and_box_synonym():
    search_box = Element(id="e1", role="textbox", name="Search")
    other = Element(id="e2", role="button", name="Go")
    state = make_state(other, search_box)
    result = shortlist(state, "click the search box")
    assert result[0].id == "e1"


def test_shortlist_box_also_matches_checkbox():
    # colloquially a checkbox is a "box" too: "check the I agree box"
    agree_box = Element(id="e1", role="checkbox", name="I agree to the terms")
    name_field = Element(id="e2", role="textbox", name="Full name")
    state = make_state(name_field, agree_box)
    result = shortlist(state, "check the i agree box")
    assert result[0].id == "e1"


def test_shortlist_next_field_after_active():
    name_f = Element(id="e1", role="textbox", name="Name", active=True)
    email_f = Element(id="e2", role="textbox", name="Email")
    phone_f = Element(id="e3", role="textbox", name="Phone")
    state = make_state(name_f, email_f, phone_f)
    result = shortlist(state, "next field")
    assert result[0].id == "e2"


def test_shortlist_next_field_wraps_around_when_last_is_active():
    name_f = Element(id="e1", role="textbox", name="Name")
    email_f = Element(id="e2", role="textbox", name="Email", active=True)
    state = make_state(name_f, email_f)
    result = shortlist(state, "next field")
    assert result[0].id == "e1"


def test_shortlist_select_matches_option_text_not_just_value():
    date_select = Element(
        id="e1", role="select", name="Preferred date", value="Choose a date",
        options=["Choose a date", "Monday", "Tuesday", "Wednesday"],
    )
    decoy = Element(id="e2", role="link", name="Tuesday Market")
    state = make_state(decoy, date_select)
    result = shortlist(state, "select tuesday")
    assert result[0].id == "e1"


def test_shortlist_caps_at_limit():
    elements = [Element(id=f"e{i}", role="link", name=f"Link {i}") for i in range(40)]
    state = make_state(*elements)
    result = shortlist(state, "click something", limit=24)
    assert len(result) == 24


# ---- to_prompt_text / build_choice_criteria ------------------------------

def test_to_prompt_text_under_3kb_for_24_elements():
    elements = [
        Element(id=f"e{i:02d}", role="button", name=f"Some Button Label {i}", x=10 * i, y=20, w=100, h=30)
        for i in range(24)
    ]
    text = to_prompt_text(elements, viewport=(1280, 720))
    assert len(text.encode()) < 3072


def test_to_prompt_text_includes_geometry_word():
    e = Element(id="e1", role="button", name="Sign up", x=1000, y=10, w=50, h=20)
    text = to_prompt_text([e], viewport=(1280, 720))
    assert "top-right" in text


def test_to_prompt_text_shows_select_options():
    e = Element(id="e1", role="select", name="Date", value="Monday", options=["Monday", "Tuesday"])
    text = to_prompt_text([e])
    assert "options=[Monday,Tuesday]" in text


def test_build_choice_criteria_has_none_and_descriptions():
    e = Element(id="e07", role="button", name="Sign up", x=1000, y=10, w=50, h=20)
    criteria = build_choice_criteria([e], viewport=(1280, 720))
    assert criteria["none"] == "none of the above / not a command"
    assert criteria["e07"] == 'button "Sign up" (top-right)'


# ---- observer.js extraction against the real form.html fixture ----------

async def _load_form_state() -> PageState:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(FORM_FIXTURE)
        state = await get_page_state(page)
        await browser.close()
        return state


@pytest.fixture(scope="module")
def form_state() -> PageState:
    return asyncio.run(_load_form_state())


def test_observer_filters_out_distractors(form_state):
    names = {e.name for e in form_state.elements}
    for bad in ("Hidden button", "Invisible button", "Zero size button", "Disabled button", "Covered button"):
        assert bad not in names


def test_observer_dedupes_nested_interactive_elements(form_state):
    matches = [e for e in form_state.elements if e.name == "Need help?"]
    assert len(matches) == 1
    assert matches[0].role == "button"  # the more specific inner role wins


def test_observer_resolves_label_for_text_inputs(form_state):
    by_name = {e.name: e for e in form_state.elements}
    assert "Full name" in by_name
    assert by_name["Full name"].role == "textbox"
    assert by_name["Full name"].input_type == "text"


def test_observer_captures_select_options(form_state):
    date = next(e for e in form_state.elements if e.name == "Preferred date")
    assert date.role == "select"
    assert "Tuesday" in date.options


def test_observer_captures_checkbox_state(form_state):
    agree = next(e for e in form_state.elements if "agree" in e.name.lower())
    assert agree.role == "checkbox"
    assert agree.checked is False


def test_observer_captures_link_domain(form_state):
    home = next(e for e in form_state.elements if e.name == "Home")
    assert home.href_domain == "example.com"


def test_observer_finds_the_delete_account_button(form_state):
    delete_btn = next((e for e in form_state.elements if e.name == "Delete account"), None)
    assert delete_btn is not None
    assert delete_btn.role == "button"


# ---- observer.js extraction against a mock search-results page ----------
# Mirrors a real bug found on duckduckgo.com/html/: each result wraps the SAME href in
# several sibling <a> tags (title/icon/url-text), and the site "header" is a plain
# <div class="header"> with no semantic HTML -- not a <header> tag or ARIA landmark.

async def _load_results_state() -> PageState:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(RESULTS_FIXTURE)
        state = await get_page_state(page)
        await browser.close()
        return state


@pytest.fixture(scope="module")
def results_state() -> PageState:
    return asyncio.run(_load_results_state())


def test_observer_collapses_same_href_sibling_links_to_one(results_state):
    clinic_a_links = [e for e in results_state.elements if e.role == "link" and "clinic-a" in (e.name or "")]
    # the title link should survive; the icon (empty name) and url-text siblings should not
    # produce separate candidates for the same destination
    same_dest = [e for e in results_state.elements if e.role == "link"]
    hrefs_seen = [e.name for e in same_dest if "Clinic A" in e.name]
    assert len(hrefs_seen) == 1


def test_observer_only_one_link_per_result(results_state):
    titles = [e.name for e in results_state.elements if e.role == "link" and "Clinic" in e.name]
    assert titles == ["Clinic A - Physiotherapy", "Clinic B - Sports Medicine", "Clinic C - Rehab Center"]


def test_observer_flags_div_based_header_as_chrome(results_state):
    logo = next(e for e in results_state.elements if e.name == "ExampleSearch")
    assert logo.in_chrome is True


def test_observer_flags_div_based_footer_as_chrome(results_state):
    privacy = next(e for e in results_state.elements if e.name == "Privacy")
    assert privacy.in_chrome is True


def test_observer_result_links_are_not_chrome(results_state):
    clinic_a = next(e for e in results_state.elements if e.name == "Clinic A - Physiotherapy")
    assert clinic_a.in_chrome is False


def test_ordinal_result_excludes_header_chrome_link(results_state):
    # "first result" must not resolve to the header logo link, even though it's
    # technically the first <a> on the page
    first = find_deterministic_target(results_state.elements, "open the first result")
    assert first.name == "Clinic A - Physiotherapy"


def test_ordinal_result_second_and_third(results_state):
    second = find_deterministic_target(results_state.elements, "open the second result")
    third = find_deterministic_target(results_state.elements, "open the third result")
    assert second.name == "Clinic B - Sports Medicine"
    assert third.name == "Clinic C - Rehab Center"


def test_ordinal_plain_link_still_counts_header_links(results_state):
    # "second link" (no "result" word) should NOT apply the chrome filter -- it's generic
    first_link = find_deterministic_target(results_state.elements, "click the first link")
    assert first_link.name == "ExampleSearch"


# ---- the Vox overlay must never block or appear as a page element ------

async def _load_form_state_with_overlay() -> tuple[PageState, "object"]:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.add_init_script(OVERLAY_JS)
        await page.goto(FORM_FIXTURE)
        await page.wait_for_selector("#vox-host", timeout=3000, state="attached")
        state = await get_page_state(page)
        # verify the real page's own bottom-right button is still actually clickable,
        # not just "found" -- Playwright's click() would time out if the overlay
        # intercepted pointer events at that location
        corner = next(e for e in state.elements if e.name == "Corner Action")
        await page.locator(f'[data-vb-id="{corner.id}"]').click(timeout=3000)
        clicked = await page.evaluate(
            "() => document.getElementById('corner-btn').dataset.clicked === '1'"
        )
        await browser.close()
        return state, clicked


@pytest.fixture(scope="module")
def form_state_with_overlay():
    return asyncio.run(_load_form_state_with_overlay())


def test_overlay_host_not_extracted_as_element(form_state_with_overlay):
    state, _ = form_state_with_overlay
    assert all(e.id != "vox-host" for e in state.elements)
    assert not any("vox" in (e.name or "").lower() for e in state.elements)


def test_corner_button_found_despite_overlay(form_state_with_overlay):
    state, _ = form_state_with_overlay
    assert any(e.name == "Corner Action" for e in state.elements)


def test_corner_button_still_clickable_through_overlay(form_state_with_overlay):
    _, clicked = form_state_with_overlay
    assert clicked is True
