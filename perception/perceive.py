"""PERSON 2: perception & parsing.

Turns the live page into a compact PageState, then a shortlist of elements the
model can choose from, then compact text (or Jev "choice" criteria) to send it.
"""
import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import jellyfish
from rapidfuzz import fuzz
from playwright.async_api import Page

from shared.protocol import Element, PageState
from backend.choices import page_suggestions

OBSERVER_JS = (Path(__file__).parent / "observer.js").read_text()

logger = logging.getLogger("perception")


async def get_page_state(page: Page, with_screenshot: bool = False) -> PageState:
    t0 = time.perf_counter()
    async def observe(frame, index):
        try:
            raw = await frame.evaluate(OBSERVER_JS)
            return [Element(**(e | {"id": f"f{index}:{e['id']}"} if index else e)) for e in raw]
        except Exception:
            if index == 0:
                raise
            return []  # detached or inaccessible frame

    groups = await asyncio.gather(*(observe(frame, i) for i, frame in enumerate(page.frames)))
    elements = [e for group in groups for e in group]
    shot = None
    if with_screenshot:
        png = await page.screenshot(type="jpeg", quality=40, scale="css",
                                    style="#vox-host { visibility: hidden !important; }")
        import base64
        from io import BytesIO
        from PIL import Image
        image = Image.open(BytesIO(png))
        image.thumbnail((768, 512))
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=45)
        shot = base64.b64encode(buffer.getvalue()).decode()
    summary = await page.evaluate("""() => Array.from(document.querySelectorAll('h1,h2,[role=alert],[role=status]'))
        .filter(el => {const r=el.getBoundingClientRect(); return r.width && r.height && r.bottom>0 && r.top<innerHeight;})
        .slice(0,12).map(el => el.innerText.slice(0,120)).join(' | ')""")
    state = PageState(url=page.url, title=await page.title(), elements=elements, screenshot_b64=shot, summary=summary)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.debug("get_page_state: %d elements in %.1f ms (%s)", len(elements), elapsed_ms, page.url)
    return state


_VOCAB_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")


def distinctive_page_words(state: PageState, limit: int = 100) -> list[str]:
    """Up to `limit` distinctive words from this page's title and element names, sent
    to the Swift listener as speech-recognition vocabulary hints -- a misheard proper
    noun ("golisopod") is much less likely when that exact word is already on screen.
    """
    seen: set[str] = set()
    words: list[str] = []
    for text in (state.title, *(e.name for e in state.elements)):
        for w in _VOCAB_WORD_RE.findall(text or ""):
            key = w.lower()
            if key in seen or key in _STOPWORDS:
                continue
            seen.add(key)
            words.append(w)
            if len(words) >= limit:
                return words
    return words


# ---- shortlist -----------------------------------------------------------

ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10, "last": -1,
}

# words -> which Element.role values they refer to
ROLE_WORDS: dict[str, set[str]] = {
    "button": {"button"}, "buttons": {"button"},
    "link": {"link"}, "links": {"link"}, "result": {"link"}, "results": {"link"},
    "field": {"textbox", "select", "checkbox", "radio"}, "fields": {"textbox", "select", "checkbox", "radio"},
    "box": {"textbox", "checkbox"}, "boxes": {"textbox", "checkbox"},
    "input": {"textbox"}, "inputs": {"textbox"},
    "checkbox": {"checkbox"}, "checkboxes": {"checkbox"},
    "radio": {"radio"},
    "option": {"option", "radio", "select"}, "options": {"option", "radio", "select"},
    "video": {"link"}, "videos": {"link"},
    "dropdown": {"select"}, "select": {"select"}, "menu": {"select"},
}

_WORD_RE = re.compile(r"[a-z0-9]+")

# command verbs / filler words: strip these from the transcript before matching against
# element names, so "select tuesday" matches on "tuesday" and not the verb "select".
_STOPWORDS = {
    "the", "a", "an", "in", "on", "at", "to", "of", "for", "and", "is", "it",
    "this", "that", "i", "you", "your", "my", "me", "please",
    "click", "tap", "press", "type", "write", "select", "choose", "check",
    "go", "open", "search",
}


def _tokens(text: str) -> list[str]:
    return [_normalize(w) for w in _WORD_RE.findall(text.lower()) if len(w) > 1]


def _transcript_words(transcript: str) -> set[str]:
    return {w for w in _tokens(transcript) if w not in _STOPWORDS}


def _normalize(tok: str) -> str:
    """Crude depluralize: "buttons" -> "button". Good enough for voice commands."""
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _find_role_filter(transcript: str) -> Optional[set[str]]:
    """"...search box", "...the buttons" -> the set of Element.role values that phrase refers to."""
    words = [w.strip(".,!?") for w in transcript.lower().split()]
    return next((ROLE_WORDS[w] for w in words if w in ROLE_WORDS), None)


def _id_num(elem_id: str) -> int:
    m = re.search(r"(\d+)$", elem_id)
    return int(m.group(1)) if m else 0


def _dom_order(elements: list[Element]) -> list[Element]:
    """observer.js assigns ids sequentially in DOM order, so sorting by id number
    recovers true page order even if the caller passed an already-reordered/shortlisted
    list -- ordinals and "next field" are positional and need real page order, not
    whatever order a lexical-relevance sort happened to produce.
    """
    return sorted(elements, key=lambda e: _id_num(e.id))


_RESULT_WORDS = {"result", "results", "one", "ones", "link", "links"}


def _find_ordinal_target(elements: list[Element], transcript: str) -> Optional[Element]:
    """"second link", "third result", "last field" -> the Nth element of that role."""
    words = [w.strip(".,!?") for w in transcript.lower().split()]
    ordinal_n = next((ORDINALS[w] for w in words if w in ORDINALS), None)
    if ordinal_n is None:
        return None
    roles = _find_role_filter(transcript)

    # "open the first one"/"click the first result" on a search-results page (DDG,
    # Google, or a generic results container) means an actual result title, never
    # the search engine's own logo/nav link -- which otherwise sits earlier in DOM
    # order than any real result. observer.js already excludes the site's own
    # root/search-page links from is_search_result, so no further filtering is
    # needed once this branch applies.
    search_results = [e for e in _dom_order(elements) if e.is_search_result]
    if search_results and (roles is None or "link" in roles) and any(w in words for w in _RESULT_WORDS):
        candidates = search_results
    else:
        candidates = [e for e in _dom_order(elements) if roles is None or e.role in roles]
        if "video" in words or "videos" in words:
            candidates = [e for e in candidates if e.is_video_link]
        elif any(w in words for w in ("option", "options", "choose", "select")):
            suggestions = page_suggestions(elements) or [e for e in candidates if e.role == "option" and e.in_viewport]
            if suggestions:
                candidates = suggestions
        if roles is not None and "link" in roles:
            # "first link"/"second result" etc. means a real, nameable piece of content --
            # never the header/nav/footer chrome around it, an icon-only link with no text,
            # or a logo link back to the site's own homepage. A site logo is technically the
            # page's first <a>, but nobody means that when they say "the first link".
            candidates = [e for e in candidates if not e.in_chrome and e.name.strip() and not e.is_homepage_link]
    if not candidates:
        return None
    if ordinal_n == -1:
        return candidates[-1]
    if ordinal_n <= len(candidates):
        return candidates[ordinal_n - 1]
    return None


_NEXT_FIELD_PHRASES = ("next field", "next input", "next box")
_FIELD_ROLES = {"textbox", "select", "checkbox", "radio"}


def _find_next_field(elements: list[Element], transcript: str) -> Optional[Element]:
    """"next field" -> the next input after whichever one is currently focused."""
    t = transcript.lower()
    if not any(p in t for p in _NEXT_FIELD_PHRASES):
        return None
    ordered = _dom_order(elements)
    fields = [(i, e) for i, e in enumerate(ordered) if e.role in _FIELD_ROLES]
    if not fields:
        return None
    active_idx = next((i for i, e in enumerate(ordered) if e.active), None)
    if active_idx is None:
        return fields[0][1]
    for i, e in fields:
        if i > active_idx:
            return e
    return fields[0][1]  # wrap around


PHONETIC_ROLES = {"button", "link", "option", "tab", "menuitem"}
PHONETIC_SCORE_THRESHOLD = 75  # rapidfuzz ratio between metaphone keys, 0-100
PHONETIC_GAP_THRESHOLD = 15    # winner must clear the runner-up by this much


def _content_words(transcript: str) -> list[str]:
    """Command verbs/filler stripped, original word order kept (unlike
    _transcript_words's unordered set) -- metaphone is position-sensitive, and a
    leading "click"/"type" contributes phonemes of its own that just pollute the key."""
    return [w for w in _WORD_RE.findall(transcript.lower()) if w not in _STOPWORDS]


def _phonetic_key(text: str) -> str:
    return jellyfish.metaphone(re.sub(r"[^a-zA-Z]", "", text))


def _find_phonetic_target(elements: list[Element], transcript: str) -> Optional[Element]:
    """A misheard word often shares no SUBSTRING with what it actually sounds like
    ("goalie so pod" vs "Golisopod") -- _lexical_score alone scores every candidate 0
    in that case. Falls back to a metaphone + rapidfuzz ratio match, but only when
    lexical overlap found nothing at all, and only returns a target when there's one
    CLEAR winner (score above threshold AND a real gap over the runner-up) -- anything
    less certain is left to the normal ambiguous/DISAMBIGUATE path rather than
    guessing blind.
    """
    words = _transcript_words(transcript)
    if not words:
        return None
    candidates = [e for e in elements if e.name.strip() and e.role in PHONETIC_ROLES]
    if not candidates:
        return None
    # >=2 means at least one real exact-token hit (overlap*2) already exists; a lone
    # partial-substring coincidence (worth 1, e.g. "pod" ⊂ "golisopod") is exactly the
    # "only weak matches" case this fallback exists for, not a real lexical match.
    if max(_lexical_score(e, words) for e in candidates) >= 2:
        return None
    query = _phonetic_key(" ".join(_content_words(transcript)))
    if not query:
        return None
    scored = [(e, fuzz.ratio(query, _phonetic_key(e.name))) for e in candidates]
    scored = [(e, s) for e, s in scored if s > 0]
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[1], reverse=True)
    best_e, best_score = scored[0]
    runner_up = scored[1][1] if len(scored) > 1 else 0
    if best_score >= PHONETIC_SCORE_THRESHOLD and best_score - runner_up >= PHONETIC_GAP_THRESHOLD:
        return best_e
    return None


def find_deterministic_target(elements: list[Element], transcript: str) -> Optional[Element]:
    """Ordinal ("second link"), "next field", or a clear phonetic match: these are
    positional/structural/acoustic, not semantic, so backend/model.py should trust
    this over the model's own target guess -- a flat choice list gives an LLM no way
    to know an element is "the second link" versus any other link, and Jev never
    hears the audio, so it can't itself reason about what a misheard word sounded like.
    """
    return (_find_ordinal_target(elements, transcript) or _find_next_field(elements, transcript)
            or _find_phonetic_target(elements, transcript))


# ---- the current page's own search box (vs. an external web search) --------------------

_GENERIC_SEARCH_ENGINES = (
    "duckduckgo.com", "google.com", "bing.com", "search.brave.com", "yahoo.com",
    "ecosia.org", "startpage.com",
)
_SEARCH_BOX_NAMES = {"q", "query", "search", "search_query"}


def _is_generic_search_engine(url: str) -> bool:
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    return any(host == eng or host.endswith("." + eng) for eng in _GENERIC_SEARCH_ENGINES)


def find_own_search_box(elements: list[Element], url: str) -> Optional[Element]:
    """"search for X" types into the CURRENT page's own search box (input[type=search],
    or an input named q/query/search/search_query) instead of leaving to an external web
    search -- but never on a page that IS itself a generic search engine, since "search
    for X" already defaults to a web search there."""
    if _is_generic_search_engine(url):
        return None
    candidates = [
        e for e in elements
        if e.role == "textbox" and (e.input_type == "search" or (e.input_name or "").lower() in _SEARCH_BOX_NAMES)
    ]
    if not candidates:
        return None
    return next((e for e in candidates if e.active), candidates[0])


def _lexical_score(e: Element, words: set[str]) -> int:
    name_tokens = set(_tokens(e.name + " " + e.description))
    option_tokens = {t for opt in e.options for t in _tokens(opt)}
    overlap = len(words & name_tokens)
    option_overlap = len(words & option_tokens)
    # substring credit for partial words ("sign" ~ "signup"); exact matches are already
    # counted by `overlap`, so skip those here to avoid double-counting the same word.
    partial = sum(
        1 for w in words if len(w) >= 3
        for nt in name_tokens if len(nt) >= 3 and nt != w and (w in nt or nt in w)
    )
    return overlap * 2 + option_overlap * 3 + partial


def shortlist(state: PageState, transcript: str, limit: int = 40) -> list[Element]:
    """Ordinal / "next field" / clear phonetic matches are pinned first, then
    role-word filtered + lexical overlap, then viewport."""
    elements = state.elements
    words = _transcript_words(transcript)
    role_filter = _find_role_filter(transcript)

    pinned: list[Element] = []
    if ordinal_match := _find_ordinal_target(elements, transcript):
        pinned.append(ordinal_match)
    if (next_field := _find_next_field(elements, transcript)) and next_field not in pinned:
        pinned.append(next_field)
    if (phonetic_match := _find_phonetic_target(elements, transcript)) and phonetic_match not in pinned:
        pinned.append(phonetic_match)

    def score(e: Element) -> tuple:
        role_mismatch = role_filter is not None and e.role not in role_filter
        return (role_mismatch, -_lexical_score(e, words), not e.active, not e.in_viewport)

    ranked = sorted(elements, key=score)

    result: list[Element] = []
    seen: set[str] = set()
    for e in pinned + ranked:
        if e.id in seen:
            continue
        seen.add(e.id)
        result.append(e)
        if len(result) >= limit:
            break
    return result


# ---- prompt text / Jev criteria ------------------------------------------

def _geometry_word(e: Element, vw: Optional[int], vh: Optional[int]) -> str:
    if not e.in_viewport:
        return "below the fold"
    if not vw or not vh:
        return "viewport"
    cx, cy = e.x + e.w / 2, e.y + e.h / 2
    horiz = "left" if cx < vw / 3 else "right" if cx > vw * 2 / 3 else None
    vert = "top" if cy < vh / 3 else "bottom" if cy > vh * 2 / 3 else None
    if vert and horiz:
        return f"{vert}-{horiz}"
    return vert or horiz or "center"


def _state_suffix(e: Element) -> str:
    parts = []
    if e.input_type and e.role in ("textbox", "select"):
        parts.append(f"type={e.input_type}")
    if e.value:
        parts.append(f'value="{e.value[:30]}"')
    if e.checked is not None:
        parts.append(f"checked={str(e.checked).lower()}")
    if e.href_domain:
        parts.append(f"→{e.href_domain}")
    if e.role == "select" and e.options:
        parts.append("options=[" + ",".join(e.options[:30]) + "]")
    if e.description:
        parts.append(f'description="{e.description[:140]}"')
    return (" " + " ".join(parts)) if parts else ""


def to_prompt_text(elements: list[Element], viewport: Optional[tuple[int, int]] = None) -> str:
    """One line per element, e.g.: e07 button "Sign up" [top-right]"""
    vw, vh = viewport if viewport else (None, None)
    lines = []
    for e in elements:
        geo = _geometry_word(e, vw, vh)
        active = " [active]" if e.active else ""
        lines.append(f'{e.id} {e.role} "{e.name}"{_state_suffix(e)} [{geo}]{active}')
    return "\n".join(lines)


def build_choice_criteria(elements: list[Element], viewport: Optional[tuple[int, int]] = None) -> dict[str, str]:
    """Shortlist -> Jev "choice" criteria dict, e.g. {"e07": 'button "Sign up" (top-right)', ...}"""
    vw, vh = viewport if viewport else (None, None)
    criteria = {}
    for e in elements:
        geo = _geometry_word(e, vw, vh)
        desc = f'{e.role} "{e.name}"'
        if e.value:
            desc += f' value="{e.value[:30]}"'
        criteria[e.id] = f"{desc} ({geo})"
    criteria["none"] = "none of the above / not a command"
    return criteria
