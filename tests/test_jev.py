"""Jev response parsing must never crash on good, bad, or missing fields."""
import asyncio

import httpx
import pytest

from backend import jev


# ---- noul / choice accessors: tolerant of missing/malformed data --------

def test_noul_good():
    assert jev.noul({"is_command": {"type": "noul", "noul": 0.96}}, "is_command") == 0.96


def test_noul_missing_key_returns_default():
    assert jev.noul({}, "is_command", default=0.5) == 0.5


def test_noul_wrong_shape_returns_default():
    assert jev.noul({"is_command": "not a dict"}, "is_command", default=0.0) == 0.0


def test_noul_non_numeric_value_returns_default():
    assert jev.noul({"is_command": {"type": "noul", "noul": "oops"}}, "is_command", default=0.0) == 0.0


def test_choice_good():
    answers = {"target": {"type": "choice", "choice": "e02", "confidence": 1, "probabilities": {"e01": 0, "e02": 1}}}
    picked, conf, probs = jev.choice(answers, "target")
    assert picked == "e02" and conf == 1.0 and probs == {"e01": 0, "e02": 1}


def test_choice_missing_key():
    picked, conf, probs = jev.choice({}, "target")
    assert picked is None and conf == 0.0 and probs == {}


def test_choice_malformed_probabilities():
    answers = {"target": {"type": "choice", "choice": "e01", "confidence": 0.8, "probabilities": "nope"}}
    picked, conf, probs = jev.choice(answers, "target")
    assert picked == "e01" and conf == 0.8 and probs == {}


# ---- ask(): never raises, always returns answers dict or None -----------

class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises

    async def post(self, *args, **kwargs):
        if self._raises:
            raise self._raises
        return self._response


def _run(coro):
    return asyncio.run(coro)


def test_ask_no_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = _run(jev.ask("some-model", "state", {}))
    assert result is None


def test_ask_success(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake = _FakeClient(response=_FakeResponse({"answers": {"is_command": {"type": "noul", "noul": 1.0}}}))
    monkeypatch.setattr(jev, "_get_client", lambda: fake)
    result = _run(jev.ask("some-model", "state", {"is_command": {}}))
    assert result == {"is_command": {"type": "noul", "noul": 1.0}}


def test_ask_malformed_response_no_answers_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake = _FakeClient(response=_FakeResponse({"usage": {}}))
    monkeypatch.setattr(jev, "_get_client", lambda: fake)
    result = _run(jev.ask("some-model", "state", {}))
    assert result is None


def test_ask_http_error_returns_none(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake = _FakeClient(response=_FakeResponse({"error": "bad"}, status=500))
    monkeypatch.setattr(jev, "_get_client", lambda: fake)
    result = _run(jev.ask("some-model", "state", {}))
    assert result is None


def test_ask_timeout_returns_none(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake = _FakeClient(raises=httpx.TimeoutException("too slow"))
    monkeypatch.setattr(jev, "_get_client", lambda: fake)
    result = _run(jev.ask("some-model", "state", {}))
    assert result is None


def test_ask_unexpected_exception_returns_none(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    fake = _FakeClient(raises=RuntimeError("network gremlins"))
    monkeypatch.setattr(jev, "_get_client", lambda: fake)
    result = _run(jev.ask("some-model", "state", {}))
    assert result is None
