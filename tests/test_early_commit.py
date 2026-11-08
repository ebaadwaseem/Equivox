from backend.early_commit import EarlyCommitTracker
from shared.protocol import Decision

CONFIDENT = dict(is_command_confidence=0.95, complete_confidence=0.9)


def test_no_commit_on_first_partial_even_if_confident():
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="scroll", target_id=None, **CONFIDENT)
    assert tracker.should_commit(1, d, is_final=False) is False
    assert tracker.already_committed(1) is False


def test_early_commit_for_scroll_down_on_second_matching_partial():
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="scroll", target_id=None, scroll_amount=0.9, **CONFIDENT)
    assert tracker.should_commit(1, d, is_final=False) is False  # 1st partial: just starts the streak
    assert tracker.should_commit(1, d, is_final=False) is True  # 2nd matching partial: commit
    assert tracker.already_committed(1) is True


def test_early_commit_for_click_same_target_twice():
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="click", target_id="e42", **CONFIDENT)
    assert tracker.should_commit(7, d, is_final=False) is False
    assert tracker.should_commit(7, d, is_final=False) is True


def test_no_early_commit_for_type_free_text_even_if_confident_and_repeated():
    """type/search/navigate/select carry free text that could still change -- must
    always wait for the final, no matter how confident or how many matching partials."""
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="type", target_id="name-field", text="Sarah Chen", **CONFIDENT)
    assert tracker.should_commit(2, d, is_final=False) is False
    assert tracker.should_commit(2, d, is_final=False) is False
    assert tracker.should_commit(2, d, is_final=False) is False
    assert tracker.already_committed(2) is False


def test_no_commit_when_target_changes_between_partials():
    tracker = EarlyCommitTracker()
    d1 = Decision(is_command=True, intent="click", target_id="e1", **CONFIDENT)
    d2 = Decision(is_command=True, intent="click", target_id="e2", **CONFIDENT)
    assert tracker.should_commit(3, d1, is_final=False) is False
    assert tracker.should_commit(3, d2, is_final=False) is False  # streak resets, not a match
    assert tracker.should_commit(3, d2, is_final=False) is True  # now e2 repeats


def test_no_commit_below_is_command_confidence_threshold():
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="click", target_id="e1", is_command_confidence=0.5, complete_confidence=0.9)
    assert tracker.should_commit(4, d, is_final=False) is False
    assert tracker.should_commit(4, d, is_final=False) is False


def test_no_commit_below_complete_confidence_threshold():
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="click", target_id="e1", is_command_confidence=0.95, complete_confidence=0.3)
    assert tracker.should_commit(5, d, is_final=False) is False
    assert tracker.should_commit(5, d, is_final=False) is False


def test_no_commit_when_destructive_even_if_confident():
    """'click delete account' must still go through the CONFIRM gate -- never
    auto-execute just because it arrived on a confident, steady partial."""
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="click", target_id="delete-btn", destructive=True, **CONFIDENT)
    assert tracker.should_commit(6, d, is_final=False) is False
    assert tracker.should_commit(6, d, is_final=False) is False


def test_no_commit_when_ambiguous_even_if_confident():
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="click", target_id="e1", ambiguous=True, **CONFIDENT)
    assert tracker.should_commit(8, d, is_final=False) is False
    assert tracker.should_commit(8, d, is_final=False) is False


def test_no_commit_on_a_final_transcript():
    """early commit is only ever for partials -- finals go through the normal
    policy.decide_outcome() path in main.py, never this one."""
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="click", target_id="e1", **CONFIDENT)
    assert tracker.should_commit(9, d, is_final=False) is False
    assert tracker.should_commit(9, d, is_final=True) is False


def test_no_commit_when_utterance_id_is_none():
    """typed/panel input always has id=None and must never be early-committed --
    it already arrives as a single final Transcript with no partials to race."""
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="click", target_id="e1", **CONFIDENT)
    assert tracker.should_commit(None, d, is_final=False) is False


def test_already_committed_prevents_double_action():
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="scroll", target_id=None, **CONFIDENT)
    tracker.should_commit(10, d, is_final=False)
    assert tracker.should_commit(10, d, is_final=False) is True
    assert tracker.already_committed(10) is True
    # a third partial (or the eventual final) for the same id must never re-fire
    assert tracker.should_commit(10, d, is_final=False) is False
    assert tracker.should_commit(10, d, is_final=True) is False


def test_forget_clears_streak_but_not_committed_flag():
    tracker = EarlyCommitTracker()
    d = Decision(is_command=True, intent="click", target_id="e1", **CONFIDENT)
    tracker.should_commit(11, d, is_final=False)
    tracker.should_commit(11, d, is_final=False)
    assert tracker.already_committed(11) is True
    tracker.forget(11)  # main.py calls this once the final for id=11 arrives
    assert tracker.already_committed(11) is True  # still ignored -- never acts twice


def test_independent_utterance_ids_tracked_separately():
    tracker = EarlyCommitTracker()
    d1 = Decision(is_command=True, intent="click", target_id="e1", **CONFIDENT)
    d2 = Decision(is_command=True, intent="click", target_id="e2", **CONFIDENT)
    assert tracker.should_commit(1, d1, is_final=False) is False
    assert tracker.should_commit(2, d2, is_final=False) is False
    assert tracker.should_commit(1, d1, is_final=False) is True
    assert tracker.should_commit(2, d2, is_final=False) is True
