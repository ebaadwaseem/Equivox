import asyncio

from backend import model
from backend.context import ConversationContext
from shared.protocol import Decision, Element


def _elements():
    return [
        Element(id="e01", role="link", name="Home"),
        Element(id="e04", role="textbox", name="Search"),
    ]


def _noul(p):
    return {"type": "noul", "noul": p}


def _choice(key, probs=None):
    probs = probs or {key: 1.0}
    return {"type": "choice", "choice": key, "confidence": probs.get(key, 1.0), "probabilities": probs}


def _answers(text_span=None, intent="search", target="none", act_now="clause_complete"):
    answers = {
        "act_now": _choice(act_now, {act_now: 0.9, "not_a_command": 0.05}),
        "intent": _choice(intent),
        "target": _choice(target),
        "destructive": _noul(0.01),
        "scroll_amount": {"type": "score", "score": 1, "confidence": 0.9},
        "tab_direction": _choice("none"),
    }
    if text_span is not None:
        answers["text_span"] = _choice(text_span)
    return answers


def _run(transcript, answers, monkeypatch, context=None, page=None):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("VB_MODEL", "typesafe/jev-test")
    calls = []

    async def fake_ask(model_name, state, questions):
        calls.append({"state": state, "questions": questions})
        return answers

    monkeypatch.setattr(model.jev, "ask", fake_ask)
    d = asyncio.run(model.decide(
        transcript, False, "", _elements(), [], page=page, context=context,
    ))
    return d, calls


def test_one_request_per_partial_and_picked_span_is_copied_verbatim(monkeypatch):
    d, calls = _run("search for pizza, and scroll down", _answers("pizza"), monkeypatch)
    assert len(calls) == 1
    assert d.intent == "search"
    assert d.text == "pizza"
    assert d.act_now == "clause_complete"
    assert d.payload_bounded is True


def test_context_adds_previous_page_and_correction_question(monkeypatch):
    ctx = ConversationContext()
    ctx.commit("click the first result", Decision(True, "click", target_id="e01"))
    ctx.remember_page("https://example.com/results", "Results")
    _, calls = _run("no, the other one", _answers("pizza"), monkeypatch, context=ctx)
    state = calls[0]["state"]
    assert "previous_page" in state
    assert "click the first result" in state
    assert "is_correction" in calls[0]["questions"]


def test_correction_question_is_absent_without_history(monkeypatch):
    _, calls = _run("scroll down", _answers(None, "scroll"), monkeypatch)
    assert "is_correction" not in calls[0]["questions"]


def test_scroll_score_maps_to_a_fraction(monkeypatch):
    answers = _answers(None, "scroll")
    answers["scroll_amount"]["score"] = 2
    d, _ = _run("scroll down", answers, monkeypatch)
    assert d.intent == "scroll"
    assert d.scroll_amount == 1.0
