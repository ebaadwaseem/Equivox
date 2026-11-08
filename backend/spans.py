"""Pure text-extraction helpers. Jev can't generate text, so whatever gets typed,
searched, navigated to, or selected is pulled out of the transcript here -- the
model only ever picks WHICH element, never WHAT text. No side effects, easy to test.
"""
import re
from typing import Optional

_TYPE_RE = re.compile(
    r"^(?:type|write|enter|input)\s+(.+?)(?:\s+(?:in|into)\s+the\s+.+)?$", re.I
)
_SEARCH_RE = re.compile(r"^(?:search(?:\s+for)?|google|look\s+up)\s+(.+)$", re.I)
_URL_RE = re.compile(r"^(?:go\s+to|open|navigate\s+to|visit)\s+(\S+)$", re.I)
_SELECT_RE = re.compile(r"^(?:select|choose|pick)\s+(.+)$", re.I)


def extract_type_text(transcript: str) -> Optional[str]:
    """"type Sarah Chen in the name field" -> "Sarah Chen" """
    m = _TYPE_RE.match(transcript.strip())
    return m.group(1).strip() if m else None


def extract_search_query(transcript: str) -> Optional[str]:
    """"search for pizza near me" -> "pizza near me" """
    m = _SEARCH_RE.match(transcript.strip())
    return m.group(1).strip() if m else None


def extract_url(transcript: str) -> Optional[str]:
    """"go to wikipedia.org" -> "wikipedia.org" """
    m = _URL_RE.match(transcript.strip())
    return m.group(1).strip() if m else None


def extract_select_value(transcript: str) -> Optional[str]:
    """"select Tuesday" -> "Tuesday" """
    m = _SELECT_RE.match(transcript.strip())
    return m.group(1).strip() if m else None


_SCROLL_UNIT = 600  # px per "medium" scroll


def extract_scroll_amount(transcript: str, size: str = "medium") -> int:
    """Direction from the transcript, magnitude from `size` ("small"/"medium"/"large")."""
    t = transcript.lower()
    direction = -1 if "up" in t else 1
    magnitude = {"small": _SCROLL_UNIT // 3, "medium": _SCROLL_UNIT, "large": _SCROLL_UNIT * 2}.get(
        size, _SCROLL_UNIT
    )
    return direction * magnitude
