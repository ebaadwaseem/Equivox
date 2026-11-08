from backend.spans import (
    extract_scroll_amount,
    extract_search_query,
    extract_select_value,
    extract_type_text,
    extract_url,
)


def test_extract_type_text_strips_field_hint():
    assert extract_type_text("type Sarah Chen in the name field") == "Sarah Chen"


def test_extract_type_text_without_field_hint():
    assert extract_type_text("type hello world") == "hello world"


def test_extract_type_text_write_synonym():
    assert extract_type_text("write my email address") == "my email address"


def test_extract_type_text_no_match_returns_none():
    assert extract_type_text("click the button") is None


def test_extract_search_query_for():
    assert extract_search_query("search for physiotherapy clinics in edmonton") == "physiotherapy clinics in edmonton"


def test_extract_search_query_bare():
    assert extract_search_query("search pizza near me") == "pizza near me"


def test_extract_url_go_to():
    assert extract_url("go to wikipedia.org") == "wikipedia.org"


def test_extract_url_open():
    assert extract_url("open github.com") == "github.com"


def test_extract_select_value():
    assert extract_select_value("select tuesday") == "tuesday"


def test_extract_select_value_choose_synonym():
    assert extract_select_value("choose Wednesday") == "Wednesday"


def test_extract_scroll_amount_down_medium():
    assert extract_scroll_amount("scroll down") > 0


def test_extract_scroll_amount_up_is_negative():
    assert extract_scroll_amount("scroll up a bit", size="small") < 0


def test_extract_scroll_amount_large_bigger_than_small():
    small = abs(extract_scroll_amount("scroll down", size="small"))
    large = abs(extract_scroll_amount("scroll down", size="large"))
    assert large > small
