"""PERSON 3: model client.

decide() turns a transcript + shortlisted elements into a Decision:
  1. Jev (typed-question API via backend/jev.py), if OPENROUTER_API_KEY + VB_MODEL are set.
  2. Otherwise (or on any Jev failure/timeout/malformed response): a keyword matcher,
     so the pipeline always runs with no key and never crashes on a bad response.

Jev never generates free text -- "what to type/search/select" is pulled out of the
transcript in code (backend/spans.py). Jev only answers typed questions: is this a
command, what's the intent, which element, is the user done talking, is this
destructive, how far to scroll.
"""
import logging
import os
import re
import time
from typing import Optional, get_args

from backend import jev, spans
from backend.policy import is_ambiguous, looks_destructive
from backend.state_builder import build_state_text
from perception.perceive import build_choice_criteria, find_deterministic_target
from shared.protocol import Decision, Element, Intent

logger = logging.getLogger("backend.model")

VALID_INTENTS = set(get_args(Intent))
TARGET_INTENTS = {"click", "type", "select"}

INTENT_CRITERIA = {
    "click": "click a button or link",
    "type": "type text into a text field",
    "select": "choose an option from a dropdown menu",
    "scroll": "scroll the page up or down",
    "navigate": "go to a specific website/URL by name",
    "back": "go back to the previous page",
    "forward": "go forward to the next page",
    "search": "run a web search for something",
    "none": "not a browser command at all",
}

_SCROLL_PX = {
    "small_up": -200, "small_down": 200,
    "medium_up": -600, "medium_down": 600,
    "large_up": -1200, "large_down": 1200,
}


async def decide(
    transcript: str,
    is_final: bool,
    elements_text: str,
    elements: list[Element],
    recent_actions: Optional[list[str]] = None,
) -> Decision:
    key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("VB_MODEL", "")  # read lazily, not at import time -- load_dotenv() runs after imports
    if not key or not model:
        return _keyword_fallback(transcript, elements)

    t0 = time.perf_counter()
    state = build_state_text(elements_text, transcript, recent_actions)
    criteria = build_choice_criteria(elements)
    questions = {
        "is_command": {
            "type": "noul",
            "instructions": (
                "Is the speaker giving a command to interact with a web browser "
                "(click/type/scroll/navigate/search/select), as opposed to just "
                "talking, thinking aloud, or saying something unrelated?"
            ),
        },
        "complete": {
            "type": "noul",
            "instructions": "Has the speaker finished saying their command (vs. still mid-sentence)?",
        },
        "intent": {
            "type": "choice",
            "instructions": "What kind of browser action is the speaker asking for?",
            "criteria": INTENT_CRITERIA,
        },
        "target": {
            "type": "choice",
            "instructions": "Which page element (if any) is the speaker referring to?",
            "criteria": criteria,
        },
    }
    logger.debug(
        "jev request #1: model=%s state_bytes=%d questions=%s",
        model, len(state.encode()), list(questions.keys()),
    )
    answers = await jev.ask(model, state, questions)
    t1 = time.perf_counter()
    logger.info("jev call #1: %.0f ms, answers=%s", (t1 - t0) * 1000, "ok" if answers else "FAILED")
    if answers is None:
        return _keyword_fallback(transcript, elements)

    is_command_p = jev.noul(answers, "is_command")
    complete_p = jev.noul(answers, "complete", default=1.0)
    intent_key, _, _ = jev.choice(answers, "intent")
    target_key, target_conf, target_probs = jev.choice(answers, "target")

    intent: Intent = intent_key if intent_key in VALID_INTENTS else "none"
    target_id = target_key if (target_key and target_key != "none") else None
    target_id = target_id if intent in TARGET_INTENTS else None
    ambiguous = is_ambiguous(target_probs) if target_id else False

    # ordinals ("second link") / "next field" are positional, not semantic -- a flat
    # choice list gives the model no way to know an element is "the second link" vs.
    # any other link, so trust perception's pin over the model's own guess here.
    if pinned := find_deterministic_target(elements, transcript):
        target_id = pinned.id
        ambiguous = False
        target_conf = 1.0
        if intent not in TARGET_INTENTS:
            intent = "click"
        # a verb-less fragment like "next field" can score low on is_command from the
        # model alone; perception already being sure about the target is itself strong
        # evidence this was a command, so floor it rather than let policy IGNORE it.
        is_command_p = max(is_command_p, 0.85)

    target_name = next((e.name for e in elements if e.id == target_id), "")
    confidence = is_command_p if target_id is None else min(is_command_p, target_conf)

    text_value = _extract_text_for_intent(intent, transcript)
    scroll_amount = 0
    destructive = looks_destructive(target_name)

    about_to_commit = is_final and complete_p >= 0.5 and (target_id or intent in {"scroll", "navigate", "search", "back", "forward"})
    if about_to_commit:
        extra_questions: dict = {}
        if target_id:
            extra_questions["destructive"] = {
                "type": "noul",
                "instructions": (
                    f'Would activating "{target_name}" perform an irreversible or hard-to-undo '
                    "action (deleting an account, permanently removing data, a non-recoverable "
                    "submission)? Say no for routine actions like search, navigation, or filling "
                    "out normal form fields."
                ),
            }
        if intent == "scroll":
            extra_questions["scroll_amount"] = {
                "type": "choice",
                "instructions": "How far should the page scroll, and in which direction?",
                "criteria": {
                    "small_up": "a small amount up", "small_down": "a small amount down",
                    "medium_up": "a medium amount up", "medium_down": "a medium amount down",
                    "large_up": "a large amount up", "large_down": "a large amount down",
                },
            }
        if extra_questions:
            logger.debug("jev request #2 (commit-time): questions=%s", list(extra_questions.keys()))
            t2 = time.perf_counter()
            answers2 = await jev.ask(model, state, extra_questions)
            logger.info("jev call #2: %.0f ms, answers=%s", (time.perf_counter() - t2) * 1000, "ok" if answers2 else "FAILED")
            if answers2:
                if "destructive" in extra_questions:
                    destructive = destructive or jev.noul(answers2, "destructive") >= 0.5
                if "scroll_amount" in extra_questions:
                    scroll_choice, _, _ = jev.choice(answers2, "scroll_amount")
                    scroll_amount = _SCROLL_PX.get(scroll_choice, 600)

    if intent == "scroll" and scroll_amount == 0:
        scroll_amount = -600 if "up" in transcript.lower() else 600

    return Decision(
        is_command=is_command_p >= 0.5,
        intent=intent,
        target_id=target_id,
        text=text_value,
        scroll_amount=scroll_amount,
        complete=complete_p >= 0.5,
        confidence=confidence,
        destructive=destructive,
        ambiguous=ambiguous,
        target_confidence=target_conf,
    )


def _extract_text_for_intent(intent: Intent, transcript: str) -> Optional[str]:
    if intent == "type":
        return spans.extract_type_text(transcript) or transcript
    if intent == "search":
        return spans.extract_search_query(transcript) or transcript
    if intent == "navigate":
        return spans.extract_url(transcript) or transcript
    if intent == "select":
        return spans.extract_select_value(transcript) or transcript
    return None


def _keyword_fallback(transcript: str, elements: list[Element]) -> Decision:
    # extraction (spans.*) runs on the original-case transcript so typed text keeps its
    # case ("Sarah Chen", not "sarah chen"); only verb/keyword matching is case-insensitive.
    transcript = transcript.strip()
    t = transcript.lower()
    if t in ("back", "go back"):
        return Decision(True, "back")
    if t in ("forward", "go forward"):
        return Decision(True, "forward")
    if t.startswith("scroll"):
        return Decision(True, "scroll", scroll_amount=-600 if "up" in t else 600)
    if url := spans.extract_url(transcript):
        return Decision(True, "navigate", text=url)
    if query := spans.extract_search_query(transcript):
        return Decision(True, "search", text=query)
    pinned = find_deterministic_target(elements, transcript)  # "second link" / "next field"
    if value := spans.extract_select_value(transcript):
        box = pinned or next((e for e in elements if e.role == "select"), None)
        return Decision(True, "select", target_id=box.id if box else None, text=value)
    if type_text := spans.extract_type_text(transcript):
        box = pinned or next((e for e in elements if e.role == "textbox"), None)
        return Decision(True, "type", target_id=box.id if box else None, text=type_text)
    if pinned:
        return Decision(True, "click", target_id=pinned.id, destructive=looks_destructive(pinned.name))
    # "click X": the first shortlisted element whose name matches any of the words
    words = [w for w in re.sub(r"^(click|press|open|tap)\s+", "", t).split() if len(w) > 2]
    for e in elements:
        if any(w in e.name.lower() for w in words):
            return Decision(True, "click", target_id=e.id, destructive=looks_destructive(e.name))
    return Decision(False, "none")
