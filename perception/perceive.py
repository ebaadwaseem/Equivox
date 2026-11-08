"""PERSON 2: perception & parsing.

Turns the live page into a compact PageState, then a shortlist of elements the
model can choose from, then compact text (or Jev "choice" criteria) to send it.
"""
import logging
import re
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import Page

from shared.protocol import Element, PageState

OBSERVER_JS = (Path(__file__).parent / "observer.js").read_text()

logger = logging.getLogger("perception")


async def get_page_state(page: Page, with_screenshot: bool = False) -> PageState:
    t0 = time.perf_counter()
    raw = await page.evaluate(OBSERVER_JS)
    elements = [Element(**e) for e in raw]
    shot = None
    if with_screenshot:
        png = await page.screenshot(type="jpeg", quality=40, scale="css")
        import base64
        shot = base64.b64encode(png).decode()
    state = PageState(url=page.url, title=await page.title(), elements=elements, screenshot_b64=shot)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.debug("get_page_state: %d elements in %.1f ms (%s)", len(elements), elapsed_ms, page.url)
    return state


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
    "option": {"radio", "select"}, "options": {"radio", "select"},
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


_RESULT_WORDS = {"result", "results"}


def _find_ordinal_target(elements: list[Element], transcript: str) -> Optional[Element]:
    """"second link", "third result", "last field" -> the Nth element of that role."""
    words = [w.strip(".,!?") for w in transcript.lower().split()]
    ordinal_n = next((ORDINALS[w] for w in words if w in ORDINALS), None)
    if ordinal_n is None:
        return None
    roles = _find_role_filter(transcript)
    candidates = [e for e in _dom_order(elements) if roles is None or e.role in roles]
    if any(w in _RESULT_WORDS for w in words):
        # "result" means a search/list result, never the header/nav/footer chrome around it
        # (e.g. a site logo link is technically the page's first <a>, but isn't "the first result")
        candidates = [e for e in candidates if not e.in_chrome]
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


def find_deterministic_target(elements: list[Element], transcript: str) -> Optional[Element]:
    """Ordinal ("second link") or "next field" matches: these are positional/structural,
    not semantic, so backend/model.py should trust this over the model's own target
    guess -- a flat choice list gives an LLM no way to know an element is "the second
    link" versus any other link.
    """
    return _find_ordinal_target(elements, transcript) or _find_next_field(elements, transcript)


def _lexical_score(e: Element, words: set[str]) -> int:
    name_tokens = set(_tokens(e.name))
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


def shortlist(state: PageState, transcript: str, limit: int = 24) -> list[Element]:
    """Ordinal / "next field" matches are pinned first, then role-word filtered + lexical overlap, then viewport."""
    elements = state.elements
    words = _transcript_words(transcript)
    role_filter = _find_role_filter(transcript)

    pinned: list[Element] = []
    if ordinal_match := _find_ordinal_target(elements, transcript):
        pinned.append(ordinal_match)
    if (next_field := _find_next_field(elements, transcript)) and next_field not in pinned:
        pinned.append(next_field)

    def score(e: Element) -> tuple:
        role_mismatch = role_filter is not None and e.role not in role_filter
        return (role_mismatch, -_lexical_score(e, words), not e.in_viewport)

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
        parts.append("options=[" + ",".join(e.options[:8]) + "]")
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
