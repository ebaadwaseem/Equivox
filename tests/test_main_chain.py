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

    async def route(self, *args, **kwargs):
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
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("VB_MODEL", "typesafe/jev-test")
    monkeypatch.setenv("VB_TRANSCRIBER", "typed")
    async def fake_get_page_state(target_page, **kwargs):
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
    for name in ("install", "set", "set_then_idle_after", "show_on", "hide", "show", "set_level", "set_history", "set_options"):
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

    assert decided == ["search for pizza", "scroll down"]
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
        return _act("search", text="pizza", act_now="clause_complete")

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
    # Unchanged state can reuse the first Jev decision at the silence deadline.
    assert len(decided) == 1


def test_single_final_drains_all_clauses_without_more_input(monkeypatch):
    executed = []
    decided = []
    done = asyncio.Event()

    async def fake_decide(text, *args, **kwargs):
        decided.append(text)
        if text.startswith("search"):
            return _act("search", text="clinics in edmonton", act_now="utterance_complete")
        return _act("scroll", scroll_amount=.8, act_now="utterance_complete")

    async def fake_execute(page, d):
        executed.append((d.intent, page.url))
        if d.intent == "search":
            await asyncio.sleep(.1)
            page.url = "https://results.example"
        else:
            done.set()
        return "done"

    _install(monkeypatch, fake_decide, fake_execute, [
        (0, Transcript("search for clinics in edmonton then scroll down", True)),
    ])
    _run_until(done)
    assert decided == ["search for clinics in edmonton", "scroll down"]
    assert executed[-1] == ("scroll", "https://results.example")


def test_three_part_prompt_opens_tab_while_search_is_still_being_spoken(monkeypatch):
    executed = []
    done = asyncio.Event()

    async def fake_decide(text, *args, **kwargs):
        if text == "open a new tab":
            return _act("new_tab", act_now="clause_complete")
        if text.startswith("search"):
            return _act("search", text="physiotherapy clinics in edmonton", act_now="clause_complete")
        return _act("scroll", scroll_amount=.8, act_now="clause_complete")

    async def new_tab(ctx, url=None):
        page = FakePage()
        page.url = "https://www.google.com/"
        executed.append(("new_tab", page.url))
        return page

    async def fake_execute(page, d):
        executed.append((d.intent, page.url))
        if d.intent == "search":
            await asyncio.sleep(.15)
            page.url = "https://results.example"
        else:
            done.set()
        return "done"

    _install(monkeypatch, fake_decide, fake_execute, [
        (0, Transcript("open a new tab", False, id=1)),
        (.2, Transcript("open a new tab then search for physiotherapy clinics", False, id=1)),
        (.15, Transcript("open a new tab then search for physiotherapy clinics in edmonton okay now scroll down", True, id=1)),
    ])
    monkeypatch.setattr(main.act, "new_tab", new_tab)
    _run_until(done)
    assert executed == [("new_tab", "https://www.google.com/"), ("search", "https://www.google.com/"), ("scroll", "https://results.example")]


def test_separate_commands_during_navigation_are_not_overwritten(monkeypatch):
    executed = []
    done = asyncio.Event()

    async def fake_decide(text, *args, **kwargs):
        if text.startswith("search"):
            return _act("search", text="pizza", act_now="utterance_complete")
        return _act("scroll", scroll_amount=-.8 if "up" in text else .8, act_now="utterance_complete")

    async def fake_execute(page, d):
        executed.append(d.intent if d.intent == "search" else d.scroll_amount)
        if d.intent == "search":
            await asyncio.sleep(.3)
        if len(executed) == 3:
            done.set()
        return "done"

    _install(monkeypatch, fake_decide, fake_execute, [
        (0, Transcript("search for pizza", True)),
        (.05, Transcript("scroll down", True)),
        (.05, Transcript("scroll up", True)),
    ])
    _run_until(done)
    assert executed == ["search", .8, -.8]


def test_audio_activity_prevents_payload_commit_when_transcript_stalls(monkeypatch):
    monkeypatch.setattr(main.policy, "PAYLOAD_SILENCE_MS", 120)
    done = asyncio.Event()
    speech_ended = []
    executed_at = []

    async def fake_decide(text, *args, **kwargs):
        return _act("search", text="clinics", act_now="clause_complete")

    async def fake_execute(page, d):
        executed_at.append(asyncio.get_running_loop().time())
        done.set()
        return "done"

    _install(monkeypatch, fake_decide, fake_execute, [])
    monkeypatch.setenv("VB_TRANSCRIBER", "apple")
    monkeypatch.setattr(main, "apple_speech_available", lambda: True)

    class FakeSpeech:
        def __init__(self, on_level, **kwargs):
            self.on_level = on_level

        async def stream(self):
            await self.on_level(.2)
            yield Transcript("search for clinics", False, id=70)
            for _ in range(12):
                await asyncio.sleep(.04)
                await self.on_level(.2)
            speech_ended.append(asyncio.get_running_loop().time())
            await self.on_level(0)
            await asyncio.Event().wait()

    monkeypatch.setattr(main, "AppleSpeechTranscriber", FakeSpeech)
    _run_until(done)
    assert executed_at[0] >= speech_ended[0] + .1


def test_recognizer_revision_during_inference_cannot_execute_old_payload(monkeypatch):
    executed = []
    done = asyncio.Event()

    async def fake_decide(text, *args, **kwargs):
        await asyncio.sleep(.15)
        return _act("search", text="pizza" if "pizza" in text else "pasta", act_now="clause_complete")

    async def fake_execute(page, d):
        executed.append(d.text)
        done.set()
        return "done"

    _install(monkeypatch, fake_decide, fake_execute, [
        (0, Transcript("search for pizza", False, id=10)),
        (.12, Transcript("search for pasta", True, id=10)),
    ])
    _run_until(done)
    assert executed == ["pasta"]


def test_click_option_two_uses_displayed_target_and_survives_original_final(monkeypatch):
    from shared.protocol import Element
    executed=[]
    shown=[]
    done=asyncio.Event()
    async def decide(text,*args,**kwargs):
        return _act('click',target_id='e1',ambiguous=True,act_now='clause_complete')
    async def execute(page,d):
        executed.append(d.target_id)
        done.set()
        return 'Clicked'
    async def options(page,rows):
        if rows:
            shown.append(rows)
    _install(monkeypatch,decide,execute,[
        (0,Transcript('click the clinic',False,id=1)),
        (.2,Transcript('click the clinic',True,id=1)),
        (.15,Transcript('click option two',True,id=2)),
    ])
    monkeypatch.setattr(main,'shortlist',lambda state,text:[Element('e1','button','First clinic'),Element('e2','button','Second clinic')])
    monkeypatch.setattr(main.vox,'set_options',options)
    _run_until(done)
    assert executed==['e2']
    assert [r['label'] for r in shown[0]]==['First clinic','Second clinic']


def test_simple_edit_and_reload_commands_do_not_call_jev(monkeypatch):
    executed=[]
    done=asyncio.Event()
    async def decide(*args,**kwargs):
        raise AssertionError('Exact commands should not call Jev')
    async def execute(page,d):
        executed.append((d.intent,d.text))
        if len(executed)==3:
            done.set()
        return 'done'
    _install(monkeypatch,decide,execute,[
        (0,Transcript('clear the text then press backspace then reload tab',True))
    ])
    _run_until(done)
    assert executed==[('clear',None),('key','Backspace'),('reload',None)]


def test_option_reply_can_continue_the_same_physical_utterance(monkeypatch):
    from shared.protocol import Element
    done=asyncio.Event()
    executed=[]
    async def decide(*args,**kwargs):
        return _act('click',target_id='e1',ambiguous=True,act_now='clause_complete')
    async def execute(page,d):
        executed.append(d.target_id)
        done.set()
        return 'Clicked'
    _install(monkeypatch,decide,execute,[
        (0,Transcript('click the clinic',False,id=1)),
        (.25,Transcript('click the clinic click option two',True,id=1)),
    ])
    monkeypatch.setattr(main,'shortlist',lambda state,text:[Element('e1','button','First clinic'),Element('e2','button','Second clinic')])
    _run_until(done)
    assert executed==['e2']


def test_show_options_then_spoken_choice_uses_generic_page_controls(monkeypatch):
    from shared.protocol import PageState, Element
    done=asyncio.Event()
    executed=[]
    async def decide(*args,**kwargs):
        raise AssertionError('Explicit choices should bypass Jev')
    async def execute(page,d):
        executed.append(d.target_id)
        done.set()
        return 'Clicked'
    async def state(page,**kwargs):
        return PageState(page.url,'A regular page',[Element('e1','button','First choice'),Element('e2','button','Second choice')])
    async def evaluate(page,script,*args):
        return [800,600]
    async def revision(page):
        return ['stable']
    _install(monkeypatch,decide,execute,[
        (0,Transcript('show options',True,id=1)),
        (.25,Transcript('click option two',True,id=2)),
    ])
    monkeypatch.setattr(main,'get_page_state',state)
    monkeypatch.setattr(main,'shortlist',lambda state,text,**kwargs:state.elements)
    monkeypatch.setattr(FakePage,'evaluate',evaluate,raising=False)
    monkeypatch.setattr(main.vision,'scene_revision',revision)
    _run_until(done)
    assert executed==['e2']


def test_noisy_partial_reply_does_not_discard_the_options(monkeypatch):
    from shared.protocol import Element
    done=asyncio.Event()
    executed=[]
    calls=[]
    async def decide(text,*args,**kwargs):
        calls.append(text)
        return _act('click',target_id='e1',ambiguous=True,act_now='clause_complete')
    async def execute(page,d):
        executed.append(d.target_id)
        done.set()
        return 'Clicked'
    _install(monkeypatch,decide,execute,[
        (0,Transcript('click the clinic',True,id=1)),
        (.2,Transcript('click on the link in the description',False,id=2)),
        (.12,Transcript('click option one',False,id=2)),
        (.12,Transcript('click option two',True,id=2)),
    ])
    monkeypatch.setattr(main,'shortlist',lambda state,text:[Element('e1','button','First clinic'),Element('e2','button','Second clinic')])
    _run_until(done)
    assert calls==['click the clinic']
    assert executed==['e2']


def test_new_tab_commits_before_search_payload_finishes(monkeypatch):
    done = asyncio.Event()
    opened = asyncio.Event()
    executions = []
    original_page = None

    async def open_tab(ctx, url=None):
        nonlocal original_page
        original_page = ctx.page
        page = FakePage()
        page.url = 'https://new-tab.example'
        opened.set()
        return page

    async def fake_decide(text, is_final, *args, **kwargs):
        assert opened.is_set(), 'The fixed tab command should bypass model inference'
        assert text.startswith('search for')
        if not is_final:
            return _act('search', text=text.removeprefix('search for '), act_now='incomplete')
        return _act('search', text='edmonton oilers', act_now='utterance_complete')

    async def fake_execute(page, d):
        assert page is not original_page
        executions.append((d.intent, d.text))
        done.set()
        return 'Searched'

    _install(monkeypatch, fake_decide, fake_execute, [
        (0, Transcript('open a new tab and search', False, id=51)),
        (.15, Transcript('open a new tab and search for edmonton', False, id=51)),
        (.15, Transcript('open a new tab and search for edmonton oilers', True, id=51)),
    ])
    monkeypatch.setattr(main.act, 'new_tab', open_tab)
    _run_until(done)
    assert executions == [('search', 'edmonton oilers')]


def test_options_survive_incomplete_ordinal_reply(monkeypatch):
    from shared.protocol import Element
    done = asyncio.Event()
    choices = [Element('e01', 'button', 'First clinic'), Element('e02', 'button', 'Second clinic')]
    rows = []
    calls = []

    async def fake_decide(text, *args, **kwargs):
        calls.append(text)
        return _act('click', ambiguous=True, act_now='utterance_complete')

    async def fake_execute(page, d):
        assert d.target_id == 'e02'
        done.set()
        return 'Clicked'

    _install(monkeypatch, fake_decide, fake_execute, [
        (0, Transcript('click clinic', True, id=61)),
        (.1, Transcript('click', False, id=62)),
        (.1, Transcript('click option two', True, id=62)),
    ])
    async def state(page, **kwargs):
        result = FakeState()
        result.elements = choices
        return result
    async def set_options(page, values):
        rows.append(values)
    monkeypatch.setattr(main, 'get_page_state', state)
    monkeypatch.setattr(main, 'shortlist', lambda *args: choices)
    monkeypatch.setattr(main.vox, 'set_options', set_options)
    _run_until(done)
    assert calls == ['click clinic']
    assert any(len(row) == 2 for row in rows)
    assert rows[-1] == []


def test_clicking_native_dropdown_publishes_selectable_voice_options(monkeypatch):
    from shared.protocol import Element
    done = asyncio.Event()
    dropdown = Element('e01', 'select', 'Day', active=True, options=['Monday', 'Tuesday'])
    shown = []
    async def fake_decide(*args, **kwargs):
        return _act('click', target_id='e01', act_now='utterance_complete')
    async def fake_execute(page, d):
        if d.intent == 'click':
            return 'opened native dropdown'
        assert (d.intent, d.target_id, d.text) == ('select', 'e01', 'Tuesday')
        done.set()
        return 'selected Tuesday'
    _install(monkeypatch, fake_decide, fake_execute, [
        (0, Transcript('click day dropdown', True, id=71)),
        (.2, Transcript('click option two', True, id=72)),
    ])
    async def state(page, **kwargs):
        result = FakeState()
        result.elements = [dropdown]
        result.summary = ''
        result.screenshot_b64 = None
        return result
    async def evaluate(self, *args):
        return [800, 600]
    async def revision(page):
        return 'unchanged'
    async def set_options(page, rows):
        shown.append(rows)
    monkeypatch.setattr(FakePage, 'evaluate', evaluate, raising=False)
    monkeypatch.setattr(main, 'get_page_state', state)
    monkeypatch.setattr(main, 'shortlist', lambda *args, **kwargs: [dropdown])
    monkeypatch.setattr(main.vision, 'scene_revision', revision)
    monkeypatch.setattr(main.vox, 'set_options', set_options)
    _run_until(done)
    assert any([row['label'] for row in rows] == ['Monday', 'Tuesday'] for rows in shown)
    assert shown[-1] == []
