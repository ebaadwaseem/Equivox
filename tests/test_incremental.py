import asyncio

import pytest

from backend.clauses import first_clause
from backend.context import ConversationContext, MAX_TURNS
from backend.model import _extract_text_for_intent
from backend.scheduler import Scheduler
from backend.state_builder import build_state_text, MAX_BYTES
from executor.act import navigation_url
from shared.protocol import Decision, Transcript


@pytest.mark.parametrize("text,clause", [
    ("open a new tab then search for clinics", "open a new tab"),
    ("search for clinics in edmonton, okay now scroll down", "search for clinics in edmonton"),
    ('type "hello, then click submit" into the message field then scroll down',
     'type "hello, then click submit" into the message field'),
    ("search for red and blue clinics", "search for red and blue clinics"),
    ("search for edmonton, alberta clinics", "search for edmonton, alberta clinics"),
    ("type don't stop into the box and scroll down", "type don't stop into the box"),
])
def test_command_boundaries(text, clause):
    assert first_clause(text)[0] == clause


def test_independent_text_answer_cannot_become_new_tab_url():
    assert _extract_text_for_intent("new_tab", "open a new tab", text_pick="clinics", url_pick="none") is None
    assert _extract_text_for_intent("navigate", "go to github.com", text_pick="none", url_pick="github.com") == "github.com"
    assert _extract_text_for_intent("scroll", "scroll down", text_pick="bogus") is None
    assert _extract_text_for_intent("search", "search for clinics", text_pick="invented") is None


@pytest.mark.parametrize("value", ["physiotherapy clinics in edmonton", "javascript:alert(1)", "about:blank", "https://", "https://user:pass@example.com"])
def test_invalid_navigation_is_rejected_before_browser_mutation(value):
    with pytest.raises(ValueError):
        navigation_url(value)


def test_consumption_survives_another_utterance_and_allows_single_word_commands():
    context = ConversationContext()
    context.consume(1, "open a new tab")
    context.consume(2, "scroll down")
    assert context.slice(1, "open a new tab then back")[0] == "back"
    assert context.slice(2, "scroll down")[0] is None


def test_history_retains_actions_outside_the_model_window():
    context = ConversationContext()
    for n in range(20):
        context.commit(f"command {n}", Decision(True, "scroll"))
    assert len(context.turns) == MAX_TURNS
    assert len(context.history) == 20
    assert context.history[0] not in context.turns
    assert context.history[-1] is context.turns[-1]


def test_large_observations_cannot_truncate_current_command():
    text = build_state_text("very large page " * 10000, "search for clinics")
    assert len(text.encode()) <= MAX_BYTES
    assert text.endswith("TRANSCRIPT: search for clinics")


def test_continuous_speech_does_not_starve_decisions_or_cancel_running_calls():
    async def run():
        received = []
        cancelled = []

        async def handle(t):
            received.append(t.text)
            try:
                await asyncio.sleep(.07)
            except asyncio.CancelledError:
                cancelled.append(t.text)
                raise

        sched = Scheduler(handle, debounce_ms=20)
        for n in range(20):
            sched.submit(Transcript(str(n), False, id=1))
            await asyncio.sleep(.01)
        assert received  # acts during the stream, not just after silence
        sched.submit(Transcript("final", True, id=1))
        sched.submit(Transcript("separate command", True, id=2))
        await asyncio.sleep(.3)
        assert received[-2:] == ["final", "separate command"]
        assert not cancelled
        await sched.aclose()

    asyncio.run(run())


def test_recognizer_punctuation_revisions_preserve_consumed_words():
    context = ConversationContext()
    context.consume(1, "open a new tab,")
    assert context.slice(1, "Open a new tab then search for clinics")[0] == "search for clinics"
    assert context.slice(1, "open a new window then search for clinics")[0] is None


def test_uncertain_readiness_does_not_commit_from_just_the_top_label():
    from backend.policy import evaluate
    d = Decision(True, "new_tab", act_now="clause_complete", complete_confidence=.4)
    assert evaluate(d, is_final=False).outcome == "WAIT"
