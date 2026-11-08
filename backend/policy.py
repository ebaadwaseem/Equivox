"""Pure policy: Decision -> what main.py should actually do. No I/O, no model calls --
just the rules, with every threshold in one place so they're easy to tune during the demo.
"""
from typing import Literal

from shared.protocol import Decision

Outcome = Literal["PREVIEW", "COMMIT", "CONFIRM", "DISAMBIGUATE", "WAIT", "IGNORE"]

IS_COMMAND_THRESHOLD = 0.5      # below this, it's just not a command -> ignore
COMMIT_CONFIDENCE_THRESHOLD = 0.6  # below this, we're not sure enough to act
AMBIGUOUS_GAP = 0.2              # top-2 target probabilities this close -> ask which one

# belt-and-suspenders: these words force CONFIRM even if the model's "destructive" call
# fails/times out or says no. Demo task: "click delete account" must never auto-execute.
DESTRUCTIVE_KEYWORDS = ("delete", "remove", "cancel", "deactivate", "unsubscribe", "permanently")


def looks_destructive(element_name: str) -> bool:
    t = element_name.lower()
    return any(k in t for k in DESTRUCTIVE_KEYWORDS)


def is_ambiguous(probabilities: dict[str, float]) -> bool:
    """Top-2 candidate probabilities within AMBIGUOUS_GAP -> can't tell which one was meant."""
    if len(probabilities) < 2:
        return False
    top_two = sorted(probabilities.values(), reverse=True)[:2]
    return (top_two[0] - top_two[1]) < AMBIGUOUS_GAP


def decide_outcome(d: Decision, is_final: bool) -> Outcome:
    if not d.is_command or d.confidence < IS_COMMAND_THRESHOLD:
        return "IGNORE"
    if not is_final:
        # still talking: never commit, at most show the current best guess
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
