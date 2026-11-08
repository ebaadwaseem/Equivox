"""End-to-end decision loop tests for main.py: chains across a page load, silence
retries, and action ordering."""
import asyncio

import main
from shared.protocol import Decision, Transcript


class FakePage:
    def __init__(self):
        self.url = "https://news.ycombinator.com"
        self.viewport_size = {"width": 800, "height": 600}

    async def title(self):
        return "Hacker News"

    async def goto(self, url):
        self.url = url

    async def wait_for_load_state(self, *args, **kwargs):
        pass


class FakeContext:
    def __init__(self):
        self.page = FakePage()

    async def expose_binding(self, *args, **kwargs):
        pass

    def on(self, *args, **kwargs):
        pass

    async def new_page(self):
        return self.page


class FakeBrowser:
    async def new_context(self, *args, **kwargs):
        return FakeContext()


class FakePlaywright:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    @property
    def chromium(self):
        return self

    async def launch(self, *args, **kwargs):
        return FakeBrowser()


class FakeState:
    url = "https://news.ycombinator.com"
    title = "Hacker News"


def _install(monkeypatch, fake_decide, fake_execute, transcripts):
    async def fake_get_page_state(target_page):
        return FakeState()

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(main, "async_playwright", lambda: FakePlaywright())
    monkeypatch.setattr(main, "get_page_state", fake_get_page_state)
    monkeypatch.setattr(main, "shortlist", lambda state, text: [])
    monkeypatch.setattr(main, "to_prompt_text", lambda cands, vp: "")
    monkeypatch.setattr(main, "decide", fake_decide)
    monkeypatch.setattr(main, "execute", fake_execute)
    monkeypatch.setattr(main, "highlight", noop)
    monkeypatch.setattr(main, "apple_speech_available", lambda: False)
    monkeypatch.setattr(main.jev, "aclose", noop)
    monkeypatch.setattr(main.undo, "push_checkpoint", noop)
    for name in ("install", "set", "set_then_idle_after", "show_on", "hide", "show", "set_level"):
        monkeypatch.setattr(main.vox, name, noop)
    monkeypatch.setattr(main.vox, "current_state", lambda: "idle")
    monkeypatch.setattr(main.vox, "is_hide_command", lambda text: False)
    monkeypatch.setattr(main.vox, "is_show_command", lambda text: False)
    monkeypatch.setattr(main.vox, "describe_action", lambda d, name: d.intent)
    monkeypatch.setattr(main.vox, "describe_failure", lambda text: text)
    monkeypatch.setattr(main.vox, "describe_not_found", lambda text: text)

    class FakeTypedTranscriber:
        async def stream(self):
            for delay, t in transcripts:
                if delay:
                    await asyncio.sleep(delay)
                yield t
            await asyncio.Event().wait()

    monkeypatch.setattr(main, "TypedTranscriber", FakeTypedTranscriber)


def _run_until(done, timeout=5.0):
    async def scenario():
        task = asyncio.create_task(main.main())
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def _act(intent, **kw):
    values = dict(
        is_command=True, intent=intent, confidence=0.99,
        is_command_confidence=0.99, intent_confidence=0.99, complete=True,
    )
    values.update(kw)
    return Decision(**values)


def test_search_then_scroll_runs_scroll_against_the_loaded_results_page(monkeypatch):
    executed: list[tuple[str, str]] = []
    decided: list[str] = []
    done = asyncio.Event()

    async def fake_decide(text, is_final, elements_text, cands, **kwargs):
        decided.append(text)
        if "search" in text:
            return _act("search", text="pizza", payload_bounded=True, act_now="clause_complete")
        return _act("scroll", scroll_amount=0.8, act_now="utterance_complete")

    async def fake_execute(target_page, d):
        executed.append((d.intent, target_page.url))
        if d.intent == "search":
            await asyncio.sleep(0.5)
            target_page.url = "https://results.example"
            return "Searched"
        done.set()
        return "Scrolled down"

    _install(monkeypatch, fake_decide, fake_execute, [
        (0, Transcript(text="search for pizza, and scroll down", is_final=False, id=1)),
        (0.35, Transcript(text="search for pizza, and scroll down", is_final=True, id=1)),
    ])

    _run_until(done)

    assert decided == ["search for pizza, and scroll down", "scroll down"]
    assert executed == [
        ("search", "https://news.ycombinator.com"),
        ("scroll", "https://results.example"),
    ]


def test_chain_confirm_waits_for_yes_and_runs_once(monkeypatch):
    executed: list[tuple[str, str]] = []
    decided: list[str] = []
    done = asyncio.Event()

    async def fake_decide(text, is_final, elements_text, cands, **kwargs):
        decided.append(text)
        if "search" in text:
            return _act("search", text="pizza", payload_bounded=True, act_now="clause_complete")
        return _act("click", target_id="e10", destructive=True, act_now="clause_complete")

    async def fake_execute(target_page, d):
        executed.append((d.intent, target_page.url))
        if d.intent == "search":
            await asyncio.sleep(0.4)
            target_page.url = "https://results.example"
            return "Searched"
        done.set()
        return "Clicked"

    _install(monkeypatch, fake_decide, fake_execute, [
        (0, Transcript(text="search for pizza, and delete account", is_final=False, id=1)),
        (0.35, Transcript(text="search for pizza, and delete account", is_final=True, id=1)),
        (1.4, Transcript(text="yes", is_final=True, id=2)),
    ])

    _run_until(done)

    assert "yes" not in decided
    assert decided[-1] == "delete account"
    assert executed == [
        ("search", "https://news.ycombinator.com"),
        ("click", "https://results.example"),
    ]


def test_partial_waits_then_silence_retry_acts(monkeypatch):
    """No final ever arrives: the 900 ms silence retry has to finish the command."""
    monkeypatch.setattr(main.policy, "SILENCE_COMPLETE_MS", 120)
    monkeypatch.setattr(main.policy, "PAYLOAD_SILENCE_MS", 120)
    decided: list[float] = []
    executed: list[str] = []
    done = asyncio.Event()

    async def fake_decide(text, is_final, elements_text, cands, **kwargs):
        silent_ms = kwargs.get("silent_ms", 0.0)
        decided.append(silent_ms)
        if silent_ms >= 120:
            return _act("search", text="pizza", payload_bounded=True, act_now="utterance_complete")
        return _act("search", text="pizza", act_now="incomplete", complete=False)

    async def fake_execute(target_page, d):
        executed.append(d.text)
        done.set()
        return "Searched"

    _install(monkeypatch, fake_decide, fake_execute, [
        (0, Transcript(text="search for pizza", is_final=False, id=7)),
    ])

    _run_until(done)

    assert executed == ["pizza"]
    assert decided[0] < 120
    assert any(ms >= 120 for ms in decided)
