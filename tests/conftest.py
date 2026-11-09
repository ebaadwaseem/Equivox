"""Keep regression tests offline and deterministic even when main.py loads a
developer's .env -- a local VB_START_URL override (common for manual testing) would
otherwise silently break every test_main_chain.py-based scenario, which assumes the
fake page starts at the same URL FakePage/PageState fixtures hardcode
(https://news.ycombinator.com): main.py navigates there on startup, and a mismatch
makes inspect_page()/handle() treat the page as "changed under us" and bail out
silently, with no exception -- the kind of failure that looks like a hang, not a bug.

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
    monkeypatch.setenv("VB_START_URL", "https://news.ycombinator.com")
