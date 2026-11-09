import pytest

from backend.commands import ordinal_reply, shortcut, tab_query, inspection_request


@pytest.mark.parametrize('text,n', [
    ('click option 1', 1), ('click option one', 1), ('click option two', 2),
    ('choose the second one', 2), ('option 2', 2), ('2', 2), ('last', 3),
])
def test_spoken_options(text,n):
    assert ordinal_reply(text,3)==n


@pytest.mark.parametrize('text', ['click option four','click the first link','type one in the box','option 0'])
def test_reply_does_not_swallow_other_commands(text):
    assert ordinal_reply(text,3) is None


@pytest.mark.parametrize('text,intent,payload', [
    ('reload tab','reload',None), ('refresh the page','reload',None),
    ('clear the text','clear',None), ('backspace','key','Backspace'),
    ('click x key','key','x'), ('press the enter key','key','Enter'),
    ('select all text','key','ControlOrMeta+A'),
    ('switch to tab two','switch_tab','2'), ('switch to the first tab','switch_tab','1'),
    ('switch to the maps tab','switch_tab','maps'),
    ('pan map left','map_move','left'), ('plan a route to Edmonton city hall','route','edmonton city hall'),
])
def test_exact_commands_skip_model(text,intent,payload):
    d=shortcut(text)
    assert d.intent==intent
    assert d.text==payload


def test_key_repeat_is_bounded():
    assert shortcut('press backspace three times').repeat_count==3
    assert shortcut('press backspace 99 times') is None
    assert shortcut('click delete account') is None
    assert shortcut('press control w') is None
    assert tab_query('open one') is None


def test_inspection_is_explicit():
    assert inspection_request('tell me what you can see')=='describe'
    assert inspection_request('show map options')=='options'
    assert inspection_request('help me find an accessible route')=='plan'
    assert inspection_request('search for clinics') is None


def test_click_x_without_key_stays_a_page_click():
    assert shortcut('click x') is None
    assert shortcut('click x key').text=='x'
