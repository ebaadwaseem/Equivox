from backend.spans import extract_text_candidates, extract_url_candidates, is_comma_bounded


def test_chain_search_payload_is_bounded_before_the_comma():
    candidates = extract_text_candidates(
        "and search for physiotherapy services in edmonton, and scroll down"
    )
    assert candidates[0] == "physiotherapy services in edmonton"
    assert all("search for" not in c.lower() for c in candidates)


def test_payload_with_field_destination_keeps_only_the_value():
    assert extract_text_candidates("type Sarah Chen in the name field, and scroll down") == ["Sarah Chen"]


def test_find_payload_drops_on_this_page():
    assert extract_text_candidates("find refund policy on this page") == ["refund policy"]


def test_quoted_span_comes_first():
    candidates = extract_text_candidates('type "hello world" into the search box')
    assert candidates[0] == "hello world"


def test_never_falls_back_to_the_whole_transcript():
    assert extract_text_candidates("hello there") == []
    assert extract_text_candidates("click the button") == []


def test_url_candidates_find_domains_and_aliases():
    assert extract_url_candidates("and open youtube, then scroll") == ["youtube.com"]
    assert extract_url_candidates("go to wikipedia.org") == ["wikipedia.org"]


def test_comma_bounded_payload():
    assert is_comma_bounded("search for pizza, and scroll down", "pizza") is True
    assert is_comma_bounded("search for pizza near me", "pizza") is False
