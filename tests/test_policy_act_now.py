from backend import policy
from shared.protocol import Decision


def _d(intent="click", **kw):
    values = dict(
        is_command=True,
        intent=intent,
        target_id="e1",
        confidence=0.9,
        is_command_confidence=0.95,
        target_confidence=0.9,
        target_top_prob=0.9,
        act_now="clause_complete",
        complete=True,
    )
    values.update(kw)
    return Decision(**values)


def test_clause_complete_acts_without_final():
    assert policy.decide_outcome(_d(), is_final=False) == "COMMIT"


def test_semantically_incomplete_command_still_waits_after_silence_or_final():
    d = _d(act_now="incomplete", complete=False)
    result = policy.evaluate(d, is_final=False)
    assert result.outcome == "WAIT"
    assert policy.decide_outcome(d, is_final=False, silent_ms=900) == "WAIT"
    assert policy.decide_outcome(d, is_final=True) == "WAIT"


def test_not_a_command_is_ignored():
    assert policy.decide_outcome(_d(act_now="not_a_command"), is_final=True) == "IGNORE"


def test_destructive_still_requires_confirmation_on_a_partial():
    assert policy.decide_outcome(_d(destructive=True), is_final=False) == "CONFIRM"


def test_low_target_probability_asks_for_disambiguation():
    d = _d(target_top_prob=0.2, target_confidence=0.3)
    assert policy.decide_outcome(d, is_final=False) == "DISAMBIGUATE"


def test_commit_confidence_threshold_gates_click_type_select():
    # below COMMIT_CONFIDENCE_THRESHOLD (0.60) but above the looser ambiguous-gap
    # thresholds (0.45/0.35) -- would have wrongly COMMIT-ed before this was wired in
    for intent in ("click", "type", "select"):
        d = _d(intent, target_id="e1", text="x", target_top_prob=0.5, target_confidence=0.5)
        assert policy.decide_outcome(d, is_final=True) == "DISAMBIGUATE"


def test_commit_confidence_threshold_on_low_intent_confidence():
    # 0.58 is between INTENT_CONFIDENCE_THRESHOLD (0.55, an earlier/looser WAIT gate)
    # and COMMIT_CONFIDENCE_THRESHOLD (0.60) -- isolates this specific new check
    d = _d("click", intent_confidence=0.58)
    assert policy.decide_outcome(d, is_final=True) == "DISAMBIGUATE"


def test_commit_confidence_threshold_does_not_affect_other_intents():
    # scroll/search/etc. never had this gate and still don't -- only click/type/select
    d = _d("scroll", target_id=None, target_top_prob=0.5, intent_confidence=0.58)
    assert policy.decide_outcome(d, is_final=True) == "COMMIT"


def test_commit_confidence_threshold_exempt_for_deterministic_target():
    # ordinals/form_entry/shortcuts set target_top_prob/intent_confidence to 1.0 --
    # this is really just confirming the default-exemption, not a special-case flag
    d = _d("click", target_top_prob=1.0, target_confidence=1.0, intent_confidence=1.0)
    assert policy.decide_outcome(d, is_final=True) == "COMMIT"


def test_free_text_waits_for_final_or_silence():
    d = _d("search", target_id=None, text="pizza", payload_bounded=False)
    result = policy.evaluate(d, is_final=False)
    assert result.outcome == "WAIT"
    assert result.retry_in_ms == 600


def test_comma_bounded_free_text_can_act_early():
    d = _d("search", target_id=None, text="pizza", payload_bounded=True)
    assert policy.decide_outcome(d, is_final=False) == "COMMIT"


def test_stale_response_never_commits_free_text():
    d = _d("search", target_id=None, text="pizza", payload_bounded=True)
    assert policy.decide_outcome(d, is_final=False, stale=True) == "WAIT"


def test_stale_response_never_acts_before_the_text_settles():
    d = _d("scroll", target_id=None)
    assert policy.decide_outcome(d, is_final=False, stale=True) == "WAIT"


def test_stale_response_cannot_act_even_when_final():
    d = _d("scroll", target_id=None)
    assert policy.decide_outcome(d, is_final=True, stale=True) == "WAIT"


def test_stale_incomplete_response_never_acts():
    d = _d(act_now="incomplete", complete=False)
    assert policy.decide_outcome(d, is_final=False, stale=True) == "WAIT"


def test_free_text_without_a_span_waits_instead_of_fabricating():
    d = _d("type", target_id="e1", text=None)
    assert policy.decide_outcome(d, is_final=True) == "WAIT"


def test_click_without_a_target_asks_instead_of_acting_blind():
    d = _d("click", target_id=None, target_top_prob=1.0, target_confidence=1.0)
    assert policy.decide_outcome(d, is_final=True) == "DISAMBIGUATE"


def test_click_without_a_target_still_waits_while_the_utterance_is_open():
    d = _d("click", target_id=None, act_now="incomplete", complete=False)
    assert policy.decide_outcome(d, is_final=False) == "WAIT"
