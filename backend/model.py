"""PERSON 3: model client.

One Jev request per transcript update. Jev only answers typed questions (is this
executable yet, what intent, which element, is it destructive, which code-generated
span is the payload); code owns control flow and every string that reaches the browser.
Without a key, or on any Jev failure, a keyword matcher keeps the pipeline running.
"""
import logging
import os
import re
import time
from typing import Optional, get_args

from backend import jev, policy, spans
from backend.state_builder import build_state_text
from perception.perceive import build_choice_criteria, find_deterministic_target
from shared.protocol import Decision, Element, Intent

logger = logging.getLogger("backend.model")

VALID_INTENTS = set(get_args(Intent))
TARGET_INTENTS = {"click", "type", "select"}
ACT_NOW_VALUES = {"not_a_command", "incomplete", "clause_complete", "utterance_complete"}

INTENT_CRITERIA = {
    "click": "click a button or link",
    "type": "type text into a text field",
    "select": "choose an option from a dropdown menu",
    "scroll": "scroll the page up or down",
    "navigate": "go to a specific website/URL by name",
    "back": "go back to the previous page",
    "forward": "go forward to the next page",
    "search": "run a web search for something",
    "find": "find/locate specific text or words that are somewhere on the CURRENT page (not a web search)",
    "new_tab": "open a new browser tab",
    "close_tab": "close the current browser tab",
    "switch_tab": "switch to a different already-open browser tab",
    "accessibility": "adjust page accessibility: make text bigger/smaller, zoom in/out, toggle high-contrast mode",
    "none": "not a browser command at all",
}

ACT_NOW_CRITERIA = {
    "not_a_command": "chit-chat, narration, or a fragment not addressed to the browser",
    "incomplete": "a command has started but its required object is still missing (go to, search for, click the, type)",
    "clause_complete": "a self-contained clause is finished; the user may still add another command after a comma",
    "utterance_complete": "the whole utterance is finished and nothing more is coming",
}

TAB_DIRECTION_CRITERIA = {
    "next": "the next tab, the other tab, or no direction given",
    "previous": "the previous tab, the tab before, or last tab",
    "first": "the first tab",
    "none": "not about switching tabs",
}

_SCROLL_LEVELS = {0: 0.3, 1: 0.8, 2: 1.0}
_MISSING = object()


async def decide(
    transcript: str,
    is_final: bool,
    elements_text: str,
    elements: list[Element],
    recent_actions: Optional[list[str]] = None,
    page: Optional[dict] = None,
    context=None,
    pending_confirmation: Optional[str] = None,
    silent_ms: float = 0.0,
) -> Decision:
    key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("VB_MODEL", "")
    if not key or not model:
        d = _keyword_fallback(transcript, elements)
        d.act_now = "utterance_complete" if is_final or silent_ms >= policy.SILENCE_COMPLETE_MS else "incomplete"
        d.complete = d.act_now == "utterance_complete"
        return d

    t0 = time.perf_counter()
    text_candidates = spans.extract_text_candidates(transcript)
    url_candidates = spans.extract_url_candidates(transcript)
    state = build_state_text(
        elements_text, transcript, recent_actions, page=page,
        context=context, pending_confirmation=pending_confirmation,
    )
    criteria = build_choice_criteria(elements)
    questions: dict = {
        "act_now": {
            "type": "choice",
            "instructions": (
                "Can the words in the transcript be executed now, or should the browser wait? "
                "Judge the command said so far; use page and context to resolve back/other references."
            ),
            "criteria": ACT_NOW_CRITERIA,
        },
        "intent": {
            "type": "choice",
            "instructions": "What kind of browser action is the speaker asking for?",
            "criteria": INTENT_CRITERIA,
        },
        "target": {
            "type": "choice",
            "instructions": (
                "Which page element is the speaker referring to? Match visible text, role, and position. "
                "Use context for 'the other one' or 'not that one'."
            ),
            "criteria": criteria,
        },
        "destructive": {
            "type": "noul",
            "instructions": (
                "Would carrying out this command submit, pay, delete, send, post, log out, or otherwise "
                "do something hard to undo? Routine search, navigation, scrolling and typing are not destructive."
            ),
        },
        "scroll_amount": {
            "type": "score",
            "instructions": "How far does the user want to scroll?",
            "criteria": [
                {"what": "a little / a few lines"},
                {"what": "one screen / normal amount, or no amount specified"},
                {"what": "all the way to the top or bottom"},
            ],
        },
        "tab_direction": {
            "type": "choice",
            "instructions": "When switching tabs, which tab does the speaker refer to?",
            "criteria": TAB_DIRECTION_CRITERIA,
        },
    }
    if text_candidates:
        questions["text_span"] = {
            "type": "choice",
            "instructions": "Which option is exactly the text the user wants typed or searched? Options are verbatim.",
            "criteria": {c: f'"{c}"' for c in text_candidates} | {"none": "nothing should be typed or searched"},
        }
    if url_candidates:
        questions["url_span"] = {
            "type": "choice",
            "instructions": "Which option is the web address the user wants to open?",
            "criteria": {c: c for c in url_candidates} | {"none": "no web address is mentioned"},
        }
    if context is not None and context.turns:
        questions["is_correction"] = {
            "type": "noul",
            "instructions": (
                "Is the speaker rejecting or redirecting the most recent action in context "
                "('no, not that one', 'undo that', 'the other one') rather than issuing a fresh command?"
            ),
        }

    logger.debug(
        "jev request: model=%s state_bytes=%d questions=%s",
        model, len(state.encode()), list(questions.keys()),
    )
    answers = await jev.ask(model, state, questions)
    logger.info("jev call: %.0f ms, answers=%s", (time.perf_counter() - t0) * 1000, "ok" if answers else "FAILED")
    if answers is None:
        d = _keyword_fallback(transcript, elements)
        d.act_now = "utterance_complete" if is_final or silent_ms >= policy.SILENCE_COMPLETE_MS else "incomplete"
        d.complete = d.act_now == "utterance_complete"
        return d

    act_key, act_conf, act_probs = jev.choice(answers, "act_now")
    if act_key in ACT_NOW_VALUES:
        if act_probs:
            is_command_p = 1.0 - float(act_probs.get("not_a_command", 0.0))
        else:
            is_command_p = 0.0 if act_key == "not_a_command" else max(0.5, act_conf)
        complete_p = act_conf if act_key in ("clause_complete", "utterance_complete") else 0.0
    else:
        is_command_p = jev.noul(answers, "is_command")
        complete_p = jev.noul(answers, "complete", default=1.0)
        act_key = "not_a_command" if is_command_p < 0.5 else (
            "utterance_complete" if complete_p >= 0.5 else "incomplete"
        )

    intent_key, intent_conf, _ = jev.choice(answers, "intent")
    target_key, target_conf, target_probs = jev.choice(answers, "target")
    intent: Intent = intent_key if intent_key in VALID_INTENTS else "none"
    if act_key == "not_a_command":
        intent = "none"

    target_id = target_key if (target_key and target_key != "none") else None
    target_id = target_id if intent in TARGET_INTENTS else None
    target_top_prob = float(target_probs.get(target_id, target_conf)) if target_id else 1.0
    ambiguous = bool(target_id) and (
        target_conf < policy.TARGET_CONFIDENCE_THRESHOLD or target_top_prob < policy.TARGET_TOP_PROB_THRESHOLD
    )

    if pinned := find_deterministic_target(elements, transcript):
        target_id = pinned.id
        ambiguous = False
        target_conf = 1.0
        target_top_prob = 1.0
        if intent not in TARGET_INTENTS:
            intent = "click"
        # perception already being sure about the target is itself strong evidence, so
        # floor both confidences rather than let a hesitant model answer stall the action.
        is_command_p = max(is_command_p, 0.85)
        intent_conf = max(intent_conf, 0.85)

    if spans.extract_tab_switch_query(transcript):
        intent = "switch_tab"
        target_id = None
        ambiguous = False
        is_command_p = max(is_command_p, 0.85)
        intent_conf = max(intent_conf, 0.85)

    if intent == "switch_tab":
        tab_choice, _, _ = jev.choice(answers, "tab_direction")
        target_id = None
        ambiguous = False
        tab_text = spans.extract_tab_switch_query(transcript)
        if tab_text is None:
            tab_text = tab_choice if tab_choice not in (None, "none") else "next"

    target_name = next((e.name for e in elements if e.id == target_id), "")
    if intent == "type" and not target_id:
        target_id = next((e.id for e in elements if e.role == "textbox"), None)
    if intent == "select" and not target_id:
        target_id = next((e.id for e in elements if e.role == "select"), None)

    text_pick, _, _ = jev.choice(answers, "text_span")
    url_pick, _, _ = jev.choice(answers, "url_span")
    text_value = _extract_text_for_intent(
        intent, transcript,
        text_pick=text_pick if "text_span" in answers else _MISSING,
        url_pick=url_pick if "url_span" in answers else _MISSING,
    )
    if intent == "switch_tab":
        text_value = tab_text

    destructive = policy.looks_destructive(target_name) or jev.noul(answers, "destructive") >= policy.DESTRUCTIVE_THRESHOLD

    scroll_to: Optional[str] = None
    scroll_amount = 0.0
    if intent == "scroll":
        scroll_to = spans.extract_scroll_to(transcript)
        if scroll_to is None:
            sign = -1 if spans.extract_scroll_direction(transcript) == "up" else 1
            explicit_fraction = spans.extract_scroll_fraction(transcript)
            if explicit_fraction is not None:
                scroll_amount = sign * explicit_fraction
            else:
                level = jev.score(answers, "scroll_amount")
                scroll_amount = sign * _SCROLL_LEVELS.get(int(round(level)) if level is not None else 1, 0.8)

    confidence = is_command_p
    if intent_conf > 0:
        confidence = min(confidence, intent_conf)
    if target_id and target_conf > 0:
        confidence = min(confidence, target_conf)

    return Decision(
        is_command=act_key != "not_a_command" and is_command_p >= policy.IS_COMMAND_THRESHOLD,
        intent=intent,
        target_id=target_id,
        text=text_value,
        scroll_amount=scroll_amount,
        scroll_to=scroll_to,
        complete=act_key in ("clause_complete", "utterance_complete"),
        confidence=confidence,
        destructive=destructive,
        ambiguous=ambiguous,
        target_confidence=target_conf,
        is_command_confidence=is_command_p,
        complete_confidence=complete_p,
        intent_confidence=intent_conf,
        act_now=act_key,
        is_correction=jev.noul(answers, "is_correction") >= 0.5,
        payload_bounded=spans.is_comma_bounded(transcript, text_value or ""),
        target_top_prob=target_top_prob,
    )


def _extract_text_for_intent(
    intent: Intent,
    transcript: str,
    text_pick=_MISSING,
    url_pick=_MISSING,
) -> Optional[str]:
    if text_pick is not _MISSING:
        return None if text_pick in (None, "none") else text_pick
    if url_pick is not _MISSING:
        return None if url_pick in (None, "none") else url_pick

    if intent == "type":
        return spans.extract_type_text(transcript) or _first(spans.extract_text_candidates(transcript))
    if intent == "search":
        return spans.extract_search_query(transcript) or _first(spans.extract_text_candidates(transcript))
    if intent == "navigate":
        return spans.extract_url(transcript) or _first(spans.extract_url_candidates(transcript))
    if intent == "select":
        return spans.extract_select_value(transcript)
    if intent == "find":
        return spans.extract_find_text(transcript) or _first(spans.extract_text_candidates(transcript))
    if intent == "switch_tab":
        return spans.extract_tab_switch_query(transcript) or "next"
    if intent == "new_tab":
        return spans.extract_new_tab_url(transcript) or _first(spans.extract_url_candidates(transcript))
    if intent == "accessibility":
        return spans.extract_accessibility_action(transcript)
    return None


def _first(values: list[str]) -> Optional[str]:
    return values[0] if values else None


def _keyword_fallback(transcript: str, elements: list[Element]) -> Decision:
    transcript = transcript.strip()
    t = transcript.lower()
    if t in ("back", "go back"):
        return Decision(True, "back")
    if t in ("forward", "go forward"):
        return Decision(True, "forward")
    if t.startswith("scroll"):
        scroll_to = spans.extract_scroll_to(transcript)
        if scroll_to:
            return Decision(True, "scroll", scroll_to=scroll_to)
        sign = -1 if spans.extract_scroll_direction(transcript) == "up" else 1
        fraction = spans.extract_scroll_fraction(transcript) or spans.DEFAULT_SCROLL_FRACTION
        return Decision(True, "scroll", scroll_amount=sign * fraction)
    if "tab" in t:
        if t in ("close tab", "close this tab", "close the tab"):
            return Decision(True, "close_tab")
        if t in ("new tab", "open a new tab", "open new tab") or t.startswith("open a new tab"):
            return Decision(True, "new_tab", text=spans.extract_new_tab_url(transcript))
        if switch := spans.extract_tab_switch_query(transcript):
            return Decision(True, "switch_tab", text=switch)
    if url := spans.extract_url(transcript):
        return Decision(True, "navigate", text=url)
    if query := spans.extract_search_query(transcript):
        return Decision(True, "search", text=query)
    pinned = find_deterministic_target(elements, transcript)
    if value := spans.extract_select_value(transcript):
        box = pinned or next((e for e in elements if e.role == "select"), None)
        return Decision(True, "select", target_id=box.id if box else None, text=value)
    if type_text := spans.extract_type_text(transcript):
        box = pinned or next((e for e in elements if e.role == "textbox"), None)
        return Decision(True, "type", target_id=box.id if box else None, text=type_text)
    if find_text := spans.extract_find_text(transcript):
        return Decision(True, "find", text=find_text)
    if action := spans.extract_accessibility_action(transcript):
        return Decision(True, "accessibility", text=action)
    if pinned:
        return Decision(True, "click", target_id=pinned.id, destructive=policy.looks_destructive(pinned.name))
    words = [w for w in re.sub(r"^(click|press|open|tap)\s+", "", t).split() if len(w) > 2]
    for e in elements:
        if any(w in e.name.lower() for w in words):
            return Decision(True, "click", target_id=e.id, destructive=policy.looks_destructive(e.name))
    return Decision(False, "none")
