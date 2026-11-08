"""Tests for perception/perceive.py (pure-function shortlist/prompt logic + the
observer.js extraction behavior against tests/fixtures/pages/form.html)."""
import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from perception.perceive import build_choice_criteria, get_page_state, shortlist, to_prompt_text
from shared.protocol import Element, PageState

FORM_FIXTURE = (Path(__file__).parent / "fixtures" / "pages" / "form.html").resolve().as_uri()


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
