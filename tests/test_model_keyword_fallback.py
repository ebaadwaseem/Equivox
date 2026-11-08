"""decide() without an API key must handle every demo-task command via the keyword
fallback, and must never crash. (The Jev path is exercised in test_jev.py /
tests/run_demo_checks.py --live since it needs a real key.)"""
import asyncio

from backend.model import decide
from shared.protocol import Element


def _run(transcript, elements, is_final=True):
    return asyncio.run(decide(transcript, is_final, "", elements, []))


def _elements():
    return [
        Element(id="e01", role="link", name="Home"),
        Element(id="e04", role="textbox", name="Full name"),
        Element(id="e07", role="select", name="Preferred date", options=["Monday", "Tuesday"]),
        Element(id="e10", role="checkbox", name="I agree to the terms"),
        Element(id="e13", role="button", name="Delete account"),
        Element(id="e12", role="button", name="Book Appointment"),
    ]


def test_back(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("go back", _elements())
    assert d.is_command and d.intent == "back"


def test_scroll_down(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("scroll down", _elements())
    assert d.is_command and d.intent == "scroll" and d.scroll_amount > 0


def test_scroll_up(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("scroll up", _elements())
    assert d.scroll_amount < 0


def test_navigate(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("go to wikipedia.org", _elements())
    assert d.is_command and d.intent == "navigate" and d.text == "wikipedia.org"


def test_search(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("search for pizza near me", _elements())
    assert d.is_command and d.intent == "search" and d.text == "pizza near me"


def test_type_into_textbox(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("type Sarah Chen in the name field", _elements())
    assert d.is_command and d.intent == "type" and d.target_id == "e04" and d.text == "Sarah Chen"


def test_select_dropdown(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("select tuesday", _elements())
    assert d.is_command and d.intent == "select" and d.target_id == "e07" and d.text == "tuesday"


def test_click_delete_account_is_flagged_destructive(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("click delete account", _elements())
    assert d.is_command and d.intent == "click" and d.target_id == "e13"
    assert d.destructive is True


def test_click_non_destructive_button_not_flagged(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("click book appointment", _elements())
    assert d.is_command and d.intent == "click" and d.target_id == "e12"
    assert d.destructive is False


def test_unrelated_speech_is_not_a_command(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("um so anyway I was thinking about lunch", _elements())
    assert d.is_command is False


def test_next_field_uses_deterministic_pin(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    elements = [
        Element(id="e04", role="textbox", name="Full name", active=True),
        Element(id="e05", role="textbox", name="Email"),
    ]
    d = _run("next field", elements)
    assert d.is_command and d.intent == "click" and d.target_id == "e05"


def test_ordinal_link_uses_deterministic_pin_not_word_matching(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    elements = [
        Element(id="e01", role="link", name="Home"),
        Element(id="e02", role="link", name="Clinics"),
        Element(id="e10", role="checkbox", name="I agree to the terms"),  # contains "the"
    ]
    d = _run("click the second link", elements)
    assert d.is_command and d.intent == "click" and d.target_id == "e02"


def test_never_crashes_on_empty_element_list(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("click submit", [])
    assert d.is_command is False  # no elements to match, falls through safely


def test_find_text(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("find refund policy", _elements())
    assert d.is_command and d.intent == "find" and d.text == "refund policy"


def test_site_alias_navigate(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("open youtube", _elements())
    assert d.is_command and d.intent == "navigate" and d.text == "youtube.com"


def test_new_tab(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("open a new tab", _elements())
    assert d.is_command and d.intent == "new_tab"


def test_new_tab_with_url(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("open a new tab to wikipedia.org", _elements())
    assert d.is_command and d.intent == "new_tab" and d.text == "wikipedia.org"


def test_close_tab(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("close this tab", _elements())
    assert d.is_command and d.intent == "close_tab"


def test_switch_tab_next(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("next tab", _elements())
    assert d.is_command and d.intent == "switch_tab" and d.text == "next"


def test_switch_tab_named(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("switch to the gmail tab", _elements())
    assert d.is_command and d.intent == "switch_tab" and d.text == "gmail"


def test_accessibility_bigger_text(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("make the text bigger", _elements())
    assert d.is_command and d.intent == "accessibility" and d.text == "zoom_in"


def test_accessibility_high_contrast(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("turn on high contrast mode", _elements())
    assert d.is_command and d.intent == "accessibility" and d.text == "high_contrast_on"


def test_switch_tab_next_not_confused_with_new_tab(monkeypatch):
    # regression: Jev once confused "next tab" (switch) with "new tab" (open new) --
    # lexically near-identical. The keyword fallback's explicit ordering must not repeat it.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("next tab", _elements())
    assert d.intent == "switch_tab"
    d2 = _run("open a new tab", _elements())
    assert d2.intent == "new_tab"


def test_type_with_accessibility_keyword_still_types(monkeypatch):
    # verb-anchored "type" extraction must win over the loose accessibility matcher
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    d = _run("type increase in the name field", _elements())
    assert d.is_command and d.intent == "type" and d.text == "increase"
