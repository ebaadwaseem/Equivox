"""Pure policy: Decision -> what main.py should actually do. No I/O, no model calls --
just the rules, with every threshold in one place so they're easy to tune.
"""
from dataclasses import dataclass
from typing import Literal

from shared.protocol import Decision

Outcome = Literal["PREVIEW", "COMMIT", "CONFIRM", "DISAMBIGUATE", "WAIT", "IGNORE"]

IS_COMMAND_THRESHOLD = 0.50
INTENT_CONFIDENCE_THRESHOLD = 0.55
COMMIT_CONFIDENCE_THRESHOLD = 0.60
TARGET_CONFIDENCE_THRESHOLD = 0.45
TARGET_TOP_PROB_THRESHOLD = 0.35
DESTRUCTIVE_THRESHOLD = 0.50
SILENCE_COMPLETE_MS = 900
PAYLOAD_SILENCE_MS = 600
AMBIGUOUS_GAP = 0.2

PAYLOAD_INTENTS = {"type", "search", "navigate", "select", "find"}

# belt-and-suspenders: these words force CONFIRM even if the model's "destructive" call
# fails/times out or says no. Demo task: "click delete account" must never auto-execute.
DESTRUCTIVE_KEYWORDS = ("delete", "remove", "cancel", "deactivate", "unsubscribe", "permanently")


@dataclass
class PolicyResult:
    outcome: Outcome
    retry_in_ms: int = 0


def looks_destructive(element_name: str) -> bool:
    t = element_name.lower()
    return any(k in t for k in DESTRUCTIVE_KEYWORDS)


def is_ambiguous(probabilities: dict[str, float]) -> bool:
    """Legacy top-2-gap check; kept for callers/tests that already use it."""
    if len(probabilities) < 2:
        return False
    top_two = sorted(probabilities.values(), reverse=True)[:2]
    return (top_two[0] - top_two[1]) < AMBIGUOUS_GAP


def _retry(silent_ms: float, window_ms: int) -> int:
    return max(50, int(window_ms - silent_ms))


def evaluate(d: Decision, is_final: bool, silent_ms: float = 0.0, stale: bool = False) -> PolicyResult:
    if not d.is_command or d.is_command_confidence < IS_COMMAND_THRESHOLD or d.act_now == "not_a_command":
        return PolicyResult("IGNORE")

    if d.act_now is None:
        if d.confidence < IS_COMMAND_THRESHOLD:
            return PolicyResult("IGNORE")
        return PolicyResult(_legacy_outcome(d, is_final))

    # A decision computed from text the recognizer has already superseded must not act:
    # the newest transcript has its own request in flight, and committing both would run
    # the same command twice (e.g. "scroll" then "scroll down").
    if stale and not (is_final or silent_ms >= SILENCE_COMPLETE_MS):
        return PolicyResult("WAIT", _retry(silent_ms, SILENCE_COMPLETE_MS))

    if d.intent == "none" or d.intent_confidence < INTENT_CONFIDENCE_THRESHOLD:
        return PolicyResult("WAIT", _retry(silent_ms, SILENCE_COMPLETE_MS))

    if d.destructive:
        return PolicyResult("CONFIRM")

    if d.ambiguous or (
        d.target_id
        and (d.target_confidence < TARGET_CONFIDENCE_THRESHOLD or d.target_top_prob < TARGET_TOP_PROB_THRESHOLD)
    ):
        return PolicyResult("DISAMBIGUATE")

    silent = silent_ms >= SILENCE_COMPLETE_MS or is_final
    if d.act_now == "incomplete":
        if not silent:
            return PolicyResult("WAIT", _retry(silent_ms, SILENCE_COMPLETE_MS))
    elif d.act_now not in ("clause_complete", "utterance_complete"):
        if not silent:
            return PolicyResult("WAIT", _retry(silent_ms, SILENCE_COMPLETE_MS))

    if d.intent == "click" and not d.target_id:
        return PolicyResult("DISAMBIGUATE")

    if d.intent in PAYLOAD_INTENTS:
        if not d.text:
            return PolicyResult("WAIT")
        bounded_early = d.act_now == "clause_complete" and d.payload_bounded
        if not (is_final or silent_ms >= PAYLOAD_SILENCE_MS or bounded_early):
            return PolicyResult("WAIT", _retry(silent_ms, PAYLOAD_SILENCE_MS))

    return PolicyResult("COMMIT")


def _legacy_outcome(d: Decision, is_final: bool) -> Outcome:
    if not is_final:
        return "PREVIEW" if d.target_id else "WAIT"
    if not d.complete:
        return "WAIT"
    if d.destructive:
        return "CONFIRM"
    if d.ambiguous:
        return "DISAMBIGUATE"
    if d.confidence < COMMIT_CONFIDENCE_THRESHOLD:
        return "WAIT"
    return "COMMIT"


def decide_outcome(d: Decision, is_final: bool, silent_ms: float = 0.0, stale: bool = False) -> Outcome:
    return evaluate(d, is_final, silent_ms, stale).outcome
