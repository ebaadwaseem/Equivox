"""Async client for Jev's typed-question API.

Jev is NOT a chat model -- no /chat/completions. It takes a block of "state" text
plus a dict of typed questions and returns typed answers:
  - "noul"   -> yes/no, answers[q]["noul"] is a 0..1 probability
  - "choice" -> answers[q]["choice"] is the picked key, ["confidence"] and
                ["probabilities"] give the full distribution
  - "score"  -> ordered-level question, not used here yet

POST https://openrouter.ai/api/v1/systemone
{"model": "...", "state": "<text>", "questions": {...}}
-> {"answers": {...}, "usage": {...}}

This module never raises on a bad/slow/missing response -- callers always get
`None` (or a default) back and fall through to the keyword fallback in model.py.
Never logs the API key.
"""
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger("jev")

JEV_URL = "https://openrouter.ai/api/v1/systemone"
TIMEOUT_S = 2.0

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT_S)
    return _client


async def aclose() -> None:
    """Call once on shutdown to release the pooled connection."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ask(model: str, state: Any, questions: dict[str, Any]) -> Optional[dict[str, Any]]:
    """POST state+questions to Jev. Returns the `answers` dict, or None on any failure."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    url = os.getenv("VB_JEV_URL", JEV_URL)
    try:
        client = _get_client()
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "state": state, "questions": questions},
        )
        r.raise_for_status()
        data = r.json()
    except httpx.TimeoutException:
        logger.warning("jev: request timed out after %.1fs", TIMEOUT_S)
        return None
    except httpx.HTTPStatusError as e:
        logger.warning("jev: HTTP %s from %s", e.response.status_code, url)
        return None
    except Exception as e:
        logger.warning("jev: request failed: %s: %s", type(e).__name__, e)
        return None

    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        logger.warning("jev: malformed response (no 'answers' dict)")
        return None
    return answers


# ---- typed-answer accessors: tolerant of missing/malformed fields -------

def noul(answers: dict[str, Any], key: str, default: float = 0.0) -> float:
    """Yes/no question -> probability in [0, 1]."""
    a = answers.get(key)
    if isinstance(a, dict):
        try:
            return float(a.get("noul", default))
        except (TypeError, ValueError):
            return default
    return default


def score(answers: dict[str, Any], key: str) -> Optional[float]:
    """Ordered-level question -> score, or None when missing/malformed."""
    a = answers.get(key)
    if not isinstance(a, dict):
        return None
    try:
        return float(a.get("score"))
    except (TypeError, ValueError):
        return None


def choice(answers: dict[str, Any], key: str) -> tuple[Optional[str], float, dict[str, float]]:
    """Multiple-choice question -> (picked_key, confidence, probabilities)."""
    a = answers.get(key)
    if not isinstance(a, dict):
        return None, 0.0, {}
    picked = a.get("choice")
    try:
        confidence = float(a.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    probs = a.get("probabilities")
    probs = probs if isinstance(probs, dict) else {}
    return (picked if isinstance(picked, str) else None), confidence, probs
