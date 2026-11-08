from backend.spans import (
    DEFAULT_SCROLL_FRACTION,
    extract_accessibility_action,
    extract_find_text,
    extract_new_tab_url,
    extract_scroll_direction,
    extract_scroll_fraction,
    extract_scroll_to,
    extract_search_query,
    extract_select_value,
    extract_tab_switch_query,
    extract_type_text,
    extract_url,
    is_scroll_repeat,
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


def test_extract_scroll_direction_down_default():
    assert extract_scroll_direction("scroll down") == "down"


def test_extract_scroll_direction_up():
    assert extract_scroll_direction("scroll up a bit") == "up"


def test_extract_scroll_direction_no_keyword_defaults_down():
    assert extract_scroll_direction("scroll") == "down"


def test_extract_scroll_fraction_bit_words():
    for phrase in ("scroll down a bit", "a little", "slightly down", "a tad"):
        assert extract_scroll_fraction(phrase) == 0.3


def test_extract_scroll_fraction_lot_words():
    for phrase in ("a lot", "a full page", "way down", "way up"):
        assert extract_scroll_fraction(phrase) == 1.0


def test_extract_scroll_fraction_half_words():
    assert extract_scroll_fraction("half a page") == 0.5
    assert extract_scroll_fraction("scroll down halfway") == 0.5


def test_extract_scroll_fraction_none_when_no_magnitude_word():
    assert extract_scroll_fraction("scroll down") is None


def test_extract_scroll_to_top():
    assert extract_scroll_to("scroll to the top") == "top"


def test_extract_scroll_to_bottom():
    assert extract_scroll_to("scroll to the bottom") == "bottom"


def test_extract_scroll_to_all_the_way_down():
    assert extract_scroll_to("scroll all the way down") == "bottom"


def test_extract_scroll_to_all_the_way_up():
    assert extract_scroll_to("scroll all the way up") == "top"


def test_extract_scroll_to_none_for_plain_scroll():
    assert extract_scroll_to("scroll down") is None


def test_is_scroll_repeat_phrases():
    for phrase in ("keep scrolling", "more", "again", "scroll more"):
        assert is_scroll_repeat(phrase) is True


def test_is_scroll_repeat_rejects_unrelated():
    assert is_scroll_repeat("scroll down") is False
    assert is_scroll_repeat("click more info") is False


def test_default_scroll_fraction_is_point_eight():
    assert DEFAULT_SCROLL_FRACTION == 0.8


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
