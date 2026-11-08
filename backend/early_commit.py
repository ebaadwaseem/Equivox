"""Early-commit-on-partials: act on a PARTIAL transcript, before the ~700ms
endpoint-triggered final arrives, for intents with no free text to get wrong (click,
scroll, back, forward, tab switching) -- but only once the model is confident AND the
same target has held steady across two consecutive partials in a row. Pure policy, no
I/O -- same spirit as backend/policy.py, split out so main.py's handle() stays thin and
this is unit-testable without a browser.
"""
from shared.protocol import Decision

# intents with no free text (unlike type/search/navigate/select, which must wait for
# the final since the words themselves could still change)
EARLY_COMMIT_INTENTS = {"click", "scroll", "back", "forward", "new_tab", "close_tab", "switch_tab"}
IS_COMMAND_THRESHOLD = 0.85
COMPLETE_THRESHOLD = 0.8
STREAK_REQUIRED = 2


class EarlyCommitTracker:
    """One instance per running session (keyed internally by Transcript.id)."""

    def __init__(
        self,
        intents: frozenset[str] = frozenset(EARLY_COMMIT_INTENTS),
        streak_required: int = STREAK_REQUIRED,
        is_command_threshold: float = IS_COMMAND_THRESHOLD,
        complete_threshold: float = COMPLETE_THRESHOLD,
    ):
        self._intents = intents
        self._streak_required = streak_required
        self._is_command_threshold = is_command_threshold
        self._complete_threshold = complete_threshold
        self._streaks: dict[int, dict] = {}  # utterance id -> {"key": (intent, target_id), "count": int}
        self._committed: set[int] = set()  # utterance ids already acted on early

    def already_committed(self, utterance_id: int) -> bool:
        """True once should_commit() has returned True for this id -- the caller must
        ignore everything else for this id, including its eventual final, so an action
        never fires twice for the same utterance."""
        return utterance_id in self._committed

    def forget(self, utterance_id: int) -> None:
        """Call when an utterance's final arrives (whether or not it was early-committed)
        -- its partial-streak bookkeeping is no longer needed."""
        self._streaks.pop(utterance_id, None)

    def should_commit(self, utterance_id: int | None, d: Decision, is_final: bool) -> bool:
        """Call once per partial. Returns True the moment this utterance should act
        early -- and from then on, already_committed(utterance_id) is True."""
        if is_final or utterance_id is None or utterance_id in self._committed:
            return False
        if (
            d.intent not in self._intents
            or d.destructive  # must still go through the CONFIRM gate
            or d.ambiguous  # must still go through the DISAMBIGUATE gate
            or d.is_command_confidence < self._is_command_threshold
            or d.complete_confidence < self._complete_threshold
        ):
            return False

        key = (d.intent, d.target_id)
        streak = self._streaks.get(utterance_id)
        streak = {"key": key, "count": streak["count"] + 1} if streak and streak["key"] == key \
            else {"key": key, "count": 1}
        self._streaks[utterance_id] = streak

        if streak["count"] >= self._streak_required:
            self._committed.add(utterance_id)
            self._streaks.pop(utterance_id, None)
            return True
        return False
