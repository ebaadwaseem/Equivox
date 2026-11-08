from backend.spans import (
    extract_accessibility_action,
    extract_find_text,
    extract_new_tab_url,
    extract_scroll_amount,
    extract_search_query,
    extract_select_value,
    extract_tab_switch_query,
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


def test_extract_url_site_alias():
    assert extract_url("open youtube") == "youtube.com"


def test_extract_url_multiword_alias():
    assert extract_url("open google maps") == "maps.google.com"


def test_extract_url_check_verb_alias():
    assert extract_url("check my email") == "mail.google.com"


def test_extract_url_rejects_generic_phrase():
    assert extract_url("go to the settings page") is None


def test_extract_find_text_basic():
    assert extract_find_text("find refund policy") == "refund policy"


def test_extract_find_text_with_page_suffix():
    assert extract_find_text("find the refund policy on this page") == "the refund policy"


def test_extract_find_text_look_for_synonym():
    assert extract_find_text("look for free shipping") == "free shipping"


def test_extract_tab_switch_query_next():
    assert extract_tab_switch_query("next tab") == "next"


def test_extract_tab_switch_query_previous_phrased():
    assert extract_tab_switch_query("switch to the previous tab") == "previous"


def test_extract_tab_switch_query_named_with():
    assert extract_tab_switch_query("switch to the tab with gmail") == "gmail"


def test_extract_tab_switch_query_named_suffix():
    assert extract_tab_switch_query("switch to the gmail tab") == "gmail"


def test_extract_new_tab_url_present():
    assert extract_new_tab_url("open a new tab to wikipedia.org") == "wikipedia.org"


def test_extract_new_tab_url_absent():
    assert extract_new_tab_url("open a new tab") is None


def test_extract_accessibility_bigger():
    assert extract_accessibility_action("make the text bigger") == "zoom_in"


def test_extract_accessibility_smaller():
    assert extract_accessibility_action("make it smaller") == "zoom_out"


def test_extract_accessibility_high_contrast_on():
    assert extract_accessibility_action("turn on high contrast mode") == "high_contrast_on"


def test_extract_accessibility_high_contrast_off():
    assert extract_accessibility_action("turn off high contrast") == "high_contrast_off"


def test_extract_accessibility_reset():
    assert extract_accessibility_action("reset the zoom") == "reset"
