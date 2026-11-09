from backend.policy import decide_outcome, is_ambiguous, looks_destructive
from shared.protocol import Decision


def test_ignore_when_not_a_command():
    d = Decision(is_command=False, intent="none", confidence=0.1)
    assert decide_outcome(d, is_final=True) == "IGNORE"


def test_ignore_when_confidence_below_threshold():
    d = Decision(is_command=True, intent="click", target_id="e1", confidence=0.3)
    assert decide_outcome(d, is_final=True) == "IGNORE"


def test_preview_on_partial_with_target():
    d = Decision(is_command=True, intent="click", target_id="e1", confidence=0.9, complete=False)
    assert decide_outcome(d, is_final=False) == "PREVIEW"


def test_wait_on_partial_without_target():
    d = Decision(is_command=True, intent="click", target_id=None, confidence=0.9, complete=False)
    assert decide_outcome(d, is_final=False) == "WAIT"


def test_wait_when_final_but_not_complete():
    d = Decision(is_command=True, intent="click", target_id="e1", confidence=0.9, complete=False)
    assert decide_outcome(d, is_final=True) == "WAIT"


def test_confirm_when_destructive():
    d = Decision(is_command=True, intent="click", target_id="e1", confidence=0.9, complete=True, destructive=True)
    assert decide_outcome(d, is_final=True) == "CONFIRM"


def test_disambiguate_when_ambiguous():
    d = Decision(is_command=True, intent="click", target_id="e1", confidence=0.9, complete=True, ambiguous=True)
    assert decide_outcome(d, is_final=True) == "DISAMBIGUATE"


def test_destructive_takes_priority_over_ambiguous():
    d = Decision(is_command=True, intent="click", target_id="e1", confidence=0.9, complete=True,
                 destructive=True, ambiguous=True)
    assert decide_outcome(d, is_final=True) == "CONFIRM"


def test_commit_when_final_complete_confident():
    d = Decision(is_command=True, intent="click", target_id="e1", confidence=0.9, complete=True)
    assert decide_outcome(d, is_final=True) == "COMMIT"


def test_wait_when_final_complete_but_not_confident_enough():
    d = Decision(is_command=True, intent="click", target_id="e1", confidence=0.55, complete=True)
    assert decide_outcome(d, is_final=True) == "WAIT"


def test_is_ambiguous_close_probabilities():
    assert is_ambiguous({"e1": 0.55, "e2": 0.45}) is True


def test_is_ambiguous_clear_winner():
    assert is_ambiguous({"e1": 0.9, "e2": 0.1}) is False


def test_is_ambiguous_single_candidate():
    assert is_ambiguous({"e1": 1.0}) is False


def test_looks_destructive_delete():
    assert looks_destructive("Delete account") is True


def test_looks_destructive_unrelated():
    assert looks_destructive("Sign up") is False
