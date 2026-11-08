"""decide()'s Jev-path overrides -- these only run when a model answer is actually
present, so they need jev.ask mocked rather than exercised through the keyword fallback.
"""
import asyncio

from backend import model
from shared.protocol import Element


def _elements():
    return [Element(id="e01", role="link", name="Home")]


def _run(transcript, answers, monkeypatch, is_final=True):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("VB_MODEL", "typesafe/jev-test")

    async def fake_ask(model_name, state, questions):
        return answers

    monkeypatch.setattr(model.jev, "ask", fake_ask)
    return asyncio.run(model.decide(transcript, is_final, "", _elements(), []))


def _noul(p):
    return {"type": "noul", "noul": p}


def _choice(key, probs=None):
    probs = probs or {key: 1.0}
    return {"type": "choice", "choice": key, "confidence": probs.get(key, 1.0), "probabilities": probs}


def test_jev_misclassifying_next_tab_as_new_tab_is_corrected(monkeypatch):
    # regression: "next tab" and "new tab" are a near-minimal pair; simulate Jev picking
    # the wrong one and confirm the deterministic override in decide() fixes it.
    answers = {
        "is_command": _noul(0.9),
        "complete": _noul(1.0),
        "intent": _choice("new_tab"),
        "target": _choice("none"),
    }
    d = _run("next tab", answers, monkeypatch)
    assert d.intent == "switch_tab"
    assert d.text == "next"
    assert d.target_id is None


def test_jev_correct_new_tab_classification_passes_through(monkeypatch):
    answers = {
        "is_command": _noul(0.9),
        "complete": _noul(1.0),
        "intent": _choice("new_tab"),
        "target": _choice("none"),
    }
    d = _run("open a new tab", answers, monkeypatch)
    assert d.intent == "new_tab"


def test_jev_ordinal_pin_overrides_wrong_target_choice(monkeypatch):
    elements_probs = {"e01": 0.5, "none": 0.5}
    answers = {
        "is_command": _noul(0.9),
        "complete": _noul(1.0),
        "intent": _choice("click"),
        "target": _choice("none", elements_probs),
    }
    d = _run("click the first link", answers, monkeypatch)
    assert d.target_id == "e01"
    assert d.ambiguous is False
