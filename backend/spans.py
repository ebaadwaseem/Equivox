"""Pure text-extraction helpers. Jev can't generate text, so whatever gets typed,
searched, navigated to, or selected is pulled out of the transcript here -- the
model only ever picks WHICH element, never WHAT text. No side effects, easy to test.
"""
import re
from typing import Optional

_TYPE_RE = re.compile(
    r"^(?:type|write|enter|input)\s+(.+?)(?:\s+(?:in|into)\s+the\s+.+)?$", re.I
)
# "search the web for"/"search online for"/"search duckduckgo for" are explicit
# web-search phrasings (is_forced_web_search below); all collapse to the same bare query.
_SEARCH_RE = re.compile(
    r"^(?:search(?:\s+(?:for|the\s+web\s+for|online\s+for|duckduckgo\s+for))?|google|look\s+up)\s+(.+)$", re.I
)
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

_TLDS = "com|org|net|io|ai|dev|co|edu|gov|de|uk|us|app|xyz|info|me|tv|ch|at|fr|nl|es|it"
_URL_CANDIDATE_RE = re.compile(rf"(?:https?://)?(?:[a-z0-9-]+\.)+(?:{_TLDS})(?:/[^\s]*)?", re.I)
_URL_VERB_RE = re.compile(r"\b(?:go\s+to|open|navigate\s+to|visit|check)\s+", re.I)

_PAYLOAD_VERBS = [
    re.compile(r"\b(?:search|look)\s+(?:for|up)\s+", re.I),
    re.compile(r"\bsearch\s+(?:on\s+)?(?:google|duckduckgo|wikipedia|youtube|github|amazon|reddit|twitter|x|hacker\s+news|the\s+web)\s+for\s+", re.I),
    re.compile(r"\bsearch\s+", re.I),
    re.compile(r"\bgoogle\s+", re.I),
    re.compile(r"\bfind\s+", re.I),
    re.compile(r"\btype\s+(?:in\s+)?", re.I),
    re.compile(r"\benter\s+", re.I),
    re.compile(r"\bwrite\s+", re.I),
    re.compile(r"\bput\s+", re.I),
    re.compile(r"\bfill\s+(?:in\s+)?", re.I),
]

_TRAILING_DEST_RE = re.compile(
    r"\s+(?:in|into|on|inside|to)\s+(?:the\s+)?(?:[\w-]+\s+){0,4}?"
    r"(?:box|field|input|bar|form|textarea|search|wikipedia|youtube|google|duckduckgo|github|amazon|reddit|twitter|x|web)\b.*$",
    re.I,
)
_TRAILING_PAGE_RE = re.compile(r"\s+(?:on|in|over)\s+(?:this|the)\s+(?:page|site)\b.*$", re.I)
_LEADING_SITE_RE = re.compile(
    r"^(?:on\s+|in\s+)?(?:google|duckduckgo|wikipedia|youtube|github|amazon|reddit|twitter|x|hacker\s+news|the\s+web)\s+(?:for\s+)?",
    re.I,
)
_LEADING_FILLER_RE = re.compile(r"^(?:(?:and|now|then|also|please)\b[\s,;]*)+", re.I)


def clean_transcript(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _strip_payload(text: str) -> str:
    text = _LEADING_FILLER_RE.sub("", text)
    text = _LEADING_SITE_RE.sub("", text)
    text = _TRAILING_PAGE_RE.sub("", text)
    text = _TRAILING_DEST_RE.sub("", text)
    return text.strip(" \t,;.!?\"'")


def _push_unique(out: list[str], value: str) -> None:
    value = _strip_payload(value)
    if not value or len(value) > 120:
        return
    if any(existing.lower() == value.lower() for existing in out):
        return
    out.append(value)


def extract_text_candidates(transcript: str) -> list[str]:
    """Over-generate payload spans; Jev only picks one. Never returns the whole transcript."""
    text = clean_transcript(transcript)
    if not text:
        return []
    out: list[str] = []

    for match in re.finditer(r"[\"“”']([^\"“”']{1,120})[\"“”']", text):
        _push_unique(out, match.group(1))

    verb_matches = []
    for pattern in _PAYLOAD_VERBS:
        verb_matches.extend(pattern.finditer(text))
    verb_matches.sort(key=lambda m: (m.start(), -len(m.group())))

    seen_starts: set[int] = set()
    for match in verb_matches:
        if match.start() in seen_starts:
            continue
        seen_starts.add(match.start())
        tail = text[match.end():]
        stripped = _TRAILING_DEST_RE.sub("", tail)
        comma = re.search(r"[,;]", tail)
        if comma:
            _push_unique(out, _TRAILING_DEST_RE.sub("", tail[:comma.start()]))
        _push_unique(out, stripped)
        if stripped != tail:
            _push_unique(out, tail)

    for_idx = text.lower().find(" for ")
    if for_idx >= 0:
        _push_unique(out, text[for_idx + 5:])

    return out[:8]


def _normalize_spoken_url(text: str) -> str:
    text = clean_transcript(text).lower()
    text = re.sub(r"\s+dot\s+", ".", text)
    text = re.sub(r"\s*\.\s*", ".", text)
    text = re.sub(r"\s+slash\s+", "/", text)
    text = re.sub(r"\bwww\s+", "www.", text)
    return text


def extract_url_candidates(transcript: str) -> list[str]:
    text = _normalize_spoken_url(transcript)
    if not text:
        return []
    out: list[str] = []

    for match in _URL_CANDIDATE_RE.finditer(text):
        value = match.group(0).rstrip(".,!?")
        if value not in out:
            out.append(value)

    for match in _URL_VERB_RE.finditer(text):
        tail = text[match.end():]
        tail = re.split(r"[,;]", tail, maxsplit=1)[0]
        key = tail.strip(" \t.!?\"'")
        if key in SITE_ALIASES and SITE_ALIASES[key] not in out:
            out.append(SITE_ALIASES[key])

    return out[:6]


def is_comma_bounded(transcript: str, text: str) -> bool:
    if not text:
        return False
    start = transcript.lower().find(text.lower())
    if start < 0:
        return False
    tail = transcript[start + len(text):]
    return bool(re.search(r"[,;]", tail))


def extract_type_text(transcript: str) -> Optional[str]:
    """"type Sarah Chen in the name field" -> "Sarah Chen" """
    m = _TYPE_RE.match(transcript.strip())
    return m.group(1).strip() if m else None


def extract_search_query(transcript: str) -> Optional[str]:
    """"search for pizza near me" -> "pizza near me" """
    m = _SEARCH_RE.match(transcript.strip())
    return m.group(1).strip() if m else None


# ---- search routing: the page's own search box vs. an explicit site vs. the web --------

SEARCH_SITES = ("youtube", "wikipedia", "amazon")
_SITE_SEARCH_RE = re.compile(rf"^search\s+({'|'.join(SEARCH_SITES)})\s+for\s+(.+)$", re.I)
_FORCE_WEB_SEARCH_RE = re.compile(r"^(?:search\s+(?:the\s+web|online|duckduckgo)\s+for\s+|google\s+)", re.I)


def extract_site_search(transcript: str) -> Optional[tuple[str, str]]:
    """"search youtube for lofi music" -> ("youtube", "lofi music") -- an explicit
    site search, built as a URL in code (executor/act.py), never left to guess which
    search box on the current page (if any) the user meant."""
    m = _SITE_SEARCH_RE.match(transcript.strip())
    return (m.group(1).lower(), m.group(2).strip()) if m else None


def is_forced_web_search(transcript: str) -> bool:
    """"search the web for X" / "search online for X" / "search duckduckgo for X" /
    "google X" -- always an external web search, even when the current page has its
    own search box that plain "search for X" would otherwise type into."""
    return bool(_FORCE_WEB_SEARCH_RE.match(transcript.strip()))


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
    if not m:
        return None
    return re.split(r"\s+(?:in|from|for)\s+(?:the\s+)?", m.group(1), maxsplit=1, flags=re.I)[0].strip(" \"'")


# ---- scroll: amount is a FRACTION of the scroll container's visible height, not
# pixels -- the container itself isn't even known until execute() time, since many
# sites scroll an inner element (Gmail, Docs, Discord), not the window.

DEFAULT_SCROLL_FRACTION = 0.8  # "scroll down" with no qualifier -- like Page Down

_SCROLL_BIT_WORDS = ("a bit", "a little", "slightly", "a tad")
_SCROLL_HALF_WORDS = ("half a page", "halfway")
_SCROLL_LOT_WORDS = ("a lot", "a page", "full page", "way down", "way up")


def extract_scroll_fraction(transcript: str) -> Optional[float]:
    """Explicit magnitude words -> a fraction of the container's visible height.
    None if the transcript doesn't name a magnitude (caller should fall back to
    DEFAULT_SCROLL_FRACTION, optionally refined by Jev's small/medium/large)."""
    t = transcript.lower()
    if any(w in t for w in _SCROLL_HALF_WORDS):
        return 0.5
    if any(w in t for w in _SCROLL_BIT_WORDS):
        return 0.3
    if any(w in t for w in _SCROLL_LOT_WORDS):
        return 1.0
    return None


def extract_scroll_direction(transcript: str) -> str:
    """"up" or "down" -- always read straight from the words, regardless of magnitude."""
    return "up" if "up" in transcript.lower() else "down"


def extract_scroll_to(transcript: str) -> Optional[str]:
    """"to the top"/"to the bottom"/"all the way [up/down]" -> "top"/"bottom"."""
    t = transcript.lower()
    if "to the top" in t:
        return "top"
    if "to the bottom" in t:
        return "bottom"
    if "all the way" in t:
        return "top" if "up" in t else "bottom"
    return None


_SCROLL_REPEAT_PHRASES = {"keep scrolling", "more", "again", "scroll more"}


def is_scroll_repeat(transcript: str) -> bool:
    """"keep scrolling"/"more"/"again"/"scroll more" -> redo the last scroll."""
    return transcript.strip().lower() in _SCROLL_REPEAT_PHRASES


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
