"""Keep regression tests offline even when main.py loads a developer's .env.

Individual model tests explicitly opt into a mock key/model. Real API calibration
belongs in scripts/benchmark_decisions.py --live.
"""
import pytest


@pytest.fixture(autouse=True)
def offline_defaults(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("VB_MODEL", "")
    monkeypatch.setenv("VB_TRANSCRIBER", "typed")
    monkeypatch.setenv("VB_VISION", "0")
    monkeypatch.setenv("VB_VLM_MODEL", "")
    monkeypatch.setenv("VB_NEW_TAB_URL", "https://www.google.com/")
