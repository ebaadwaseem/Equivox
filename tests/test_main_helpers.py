"""Pure-function helpers from main.py."""
from main import _consumed_prefix, _parse_ordinal_reply


def test_parse_ordinal_reply_bare_digit():
    assert _parse_ordinal_reply("2", 3) == 2


def test_parse_ordinal_reply_the_second_one():
    assert _parse_ordinal_reply("the second one", 3) == 2


def test_parse_ordinal_reply_bare_ordinal_word():
    assert _parse_ordinal_reply("second", 3) == 2


def test_parse_ordinal_reply_option_n_phrasing():
    assert _parse_ordinal_reply("option 2", 3) == 2


def test_parse_ordinal_reply_first():
    assert _parse_ordinal_reply("the first one", 3) == 1


def test_parse_ordinal_reply_last():
    assert _parse_ordinal_reply("last", 3) == 3


def test_parse_ordinal_reply_out_of_range_digit_rejected():
    assert _parse_ordinal_reply("5", 3) is None


def test_parse_ordinal_reply_out_of_range_ordinal_rejected():
    assert _parse_ordinal_reply("fifth", 3) is None


def test_parse_ordinal_reply_non_numeric_rejected():
    assert _parse_ordinal_reply("yes", 3) is None
    assert _parse_ordinal_reply("no", 3) is None
    assert _parse_ordinal_reply("click delete account", 3) is None


def test_parse_ordinal_reply_zero_candidates():
    assert _parse_ordinal_reply("2", 0) is None


def test_consumed_prefix_stops_at_the_committed_clause():
    text = "search for pizza, and scroll down"
    assert _consumed_prefix(text, text, "clause_complete", False) == "search for pizza,"


def test_consumed_prefix_keeps_the_whole_final():
    text = "search for pizza, and scroll down"
    assert _consumed_prefix(text, text, "utterance_complete", True) == text


def test_consumed_prefix_finds_the_sliced_remainder_after_the_old_prefix():
    text = "search for pizza, and scroll down"
    assert _consumed_prefix(text, "scroll down", "utterance_complete", True, start_at=17) == text


def test_consumed_prefix_falls_back_to_the_current_text_on_a_rewrite():
    assert _consumed_prefix("scroll down", "scroll up", None, True) == "scroll up"
