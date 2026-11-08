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
_URL_RE = re.compile(r"^(?:go\s+to|open|navigate\s+to|visit|check)\s+(.+)$", re.I)
_SELECT_RE = re.compile(r"^(?:select|choose|pick)\s+(.+)$", re.I)

# common site names people say instead of a literal domain ("open youtube" vs "open youtube.com")
SITE_ALIASES = {
    "youtube": "youtube.com", "google": "google.com", "gmail": "mail.google.com",
    "my email": "mail.google.com", "email": "mail.google.com",
    "wikipedia": "wikipedia.org", "amazon": "amazon.com", "github": "github.com",
    "maps": "maps.google.com", "google maps": "maps.google.com",
    "facebook": "facebook.com", "twitter": "twitter.com", "reddit": "reddit.com",
    "netflix": "netflix.com", "linkedin": "linkedin.com",
}


def extract_type_text(transcript: str) -> Optional[str]:
    """"type Sarah Chen in the name field" -> "Sarah Chen" """
    m = _TYPE_RE.match(transcript.strip())
    return m.group(1).strip() if m else None


def extract_search_query(transcript: str) -> Optional[str]:
    """"search for pizza near me" -> "pizza near me" """
    m = _SEARCH_RE.match(transcript.strip())
    return m.group(1).strip() if m else None


def extract_url(transcript: str) -> Optional[str]:
    """"go to wikipedia.org" -> "wikipedia.org" ; "open youtube" -> "youtube.com" (alias).
    Returns None for phrases that are neither a known alias nor look like an actual domain,
    so a generic sentence like "go to the settings page" doesn't get treated as a URL.
    """
    m = _URL_RE.match(transcript.strip())
    if not m:
        return None
    captured = m.group(1).strip()
    key = captured.lower().rstrip(".,!?")
    if key in SITE_ALIASES:
        return SITE_ALIASES[key]
    if " " not in captured and "." in captured:
        return captured
    return None


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


# ---- find: voice Ctrl+F -- scroll to/highlight arbitrary page TEXT, not an element ------

_FIND_RE = re.compile(r"^(?:find|look for|highlight)\s+(.+?)(?:\s+on\s+(?:this|the)\s+page)?$", re.I)


def extract_find_text(transcript: str) -> Optional[str]:
    """"find refund policy on this page" -> "refund policy" """
    m = _FIND_RE.match(transcript.strip())
    return m.group(1).strip().strip("\"'") if m else None


# ---- tabs ------------------------------------------------------------------------------

_SWITCH_TAB_DIRECTION_RE = re.compile(r"^(?:switch to (?:the )?)?(next|previous|prev)\s*tab$", re.I)
_SWITCH_TAB_NAMED_WITH_RE = re.compile(r"^switch to (?:the )?tab (?:with|called|named)\s+(.+)$", re.I)
_SWITCH_TAB_NAMED_SUFFIX_RE = re.compile(r"^switch to (?:the )?(.+?)\s+tab$", re.I)
_NEW_TAB_URL_RE = re.compile(r"\bto\s+(\S+\.\S+)", re.I)


def extract_tab_switch_query(transcript: str) -> Optional[str]:
    """"next tab" -> "next" ; "switch to the tab with gmail" / "...the gmail tab" -> "gmail" """
    t = transcript.strip()
    if m := _SWITCH_TAB_DIRECTION_RE.match(t):
        word = m.group(1).lower()
        return "previous" if word in ("previous", "prev") else "next"
    if m := _SWITCH_TAB_NAMED_WITH_RE.match(t):
        return m.group(1).strip()
    if m := _SWITCH_TAB_NAMED_SUFFIX_RE.match(t):
        candidate = m.group(1).strip()
        if candidate.lower() not in ("next", "previous", "prev"):
            return candidate
    return None


def extract_new_tab_url(transcript: str) -> Optional[str]:
    """"open a new tab to wikipedia.org" -> "wikipedia.org" ; "open a new tab" -> None"""
    m = _NEW_TAB_URL_RE.search(transcript)
    return m.group(1).strip(".,!?") if m else None


# ---- accessibility -----------------------------------------------------------------------

def extract_accessibility_action(transcript: str) -> Optional[str]:
    """Maps phrasing to one of: zoom_in, zoom_out, high_contrast_on, high_contrast_off, reset."""
    t = transcript.lower()
    if "high contrast" in t or "high-contrast" in t or "contrast mode" in t:
        return "high_contrast_off" if any(w in t for w in ("off", "normal", "disable", "remove")) else "high_contrast_on"
    if "reset" in t or "normal size" in t or "default size" in t:
        return "reset"
    if any(w in t for w in ("bigger", "larger", "increase", "zoom in")):
        return "zoom_in"
    if any(w in t for w in ("smaller", "decrease", "zoom out")):
        return "zoom_out"
    return None
