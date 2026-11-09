"""Real autocomplete rows must be observed and clicked, not neighbouring controls."""
import asyncio

import pytest
from playwright.async_api import async_playwright

from backend.choices import page_suggestions
from backend.commands import ordinal_reply
from executor.act import execute
from perception.perceive import get_page_state, find_deterministic_target
from shared.protocol import Decision, Element, PageState, Transcript


NAMES = ["University of Alberta", "University of Alberta Hospital",
         "University of Alberta Hospital Emergency Room", "University of Alberta Faculty of Education"]


def popup_html(grid):
    # Maps uses nested rows, a gridcell, and separate aria labels for name/address.
    rows = ''.join(
        f'<div role="{"row" if grid else "option"}" onclick="window.picked={i}">'
        f'<div role="{"row" if grid else "presentation"}"><div role="gridcell">'
        f'<span aria-hidden="true">&#xe0c8;</span><span aria-label="{name}">{name}</span>'
        '<span aria-label="Edmonton, AB">Edmonton, AB</span></div></div></div>'
        for i, name in enumerate(NAMES, 1))
    return ('<style>[role=row],[role=option]{padding:5px}</style>'
            '<button>Search</button><select><option>Unrelated control</option></select>'
            '<input aria-label="Starting point" aria-autocomplete="list" aria-controls="suggestions">'
            '<div role="grid"><div role="row">Unrelated table row</div></div>'
            f'<div id="suggestions" role="{"grid" if grid else "listbox"}">{rows}</div>')


@pytest.mark.parametrize("grid", [True, False])
def test_observer_exposes_one_clickable_option_per_suggestion(grid):
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(popup_html(grid))
            await page.locator('input').focus()
            state = await get_page_state(page)
            suggestions = page_suggestions(state.elements)
            assert [e.name for e in suggestions] == NAMES
            assert all(e.description == "Edmonton, AB" for e in suggestions)
            assert not any(e.name == "Unrelated table row" for e in state.elements)
            for n in range(1, 5):
                index = ordinal_reply(f"click option {n}", len(suggestions))
                await execute(page, Decision(True, "click", target_id=suggestions[index - 1].id))
                assert await page.evaluate('window.picked') == n
            # IDs survive a reorder, but numbering follows the visible list order.
            await page.evaluate("""() => {
                const list=document.querySelector('#suggestions');
                list.prepend(list.lastElementChild);
            }""")
            state = await get_page_state(page)
            assert page_suggestions(state.elements)[0].name == NAMES[-1]
            assert find_deterministic_target(state.elements, 'choose the first option').name == NAMES[-1]
            await page.locator('#suggestions').evaluate('(e)=>e.hidden=true')
            assert page_suggestions((await get_page_state(page)).elements) == []
            await page.set_content('<div role="listbox"><div role="option">Only suggestion</div></div>')
            assert [e.name for e in page_suggestions((await get_page_state(page)).elements)] == ['Only suggestion']
            await browser.close()
    asyncio.run(run())


@pytest.mark.parametrize('command', ['click option 2', 'choose the second option', 'Quick option two.'])
def test_numbered_page_suggestion_bypasses_model_without_show_options(monkeypatch, command):
    import main
    from tests.test_main_chain import _install, _run_until
    done = asyncio.Event()
    selected = []
    state = PageState('https://news.ycombinator.com', 'Autocomplete', [
        Element('e01', 'button', 'Search'),
        *[Element(f'e{n+10}', 'option', name, y=n*40, suggestion_group='popup') for n,name in enumerate(NAMES)],
    ])

    async def never_decide(*args, **kwargs):
        pytest.fail('An exact numbered suggestion should not need a model call')

    async def action(page, decision):
        selected.append(decision.target_id)
        done.set()
        return 'Selected hospital'

    _install(monkeypatch, never_decide, action, [(0, Transcript(command, True))])
    async def snapshot(*args, **kwargs):
        return state
    monkeypatch.setattr(main, 'get_page_state', snapshot)
    _run_until(done)
    assert selected == ['e11']


def test_show_options_lists_popup_names_instead_of_page_controls(monkeypatch):
    import main
    from tests.test_main_chain import _install, _run_until, FakePage
    done = asyncio.Event()
    displayed = []
    state = PageState('https://news.ycombinator.com', 'Autocomplete', [
        Element('e01', 'button', 'Search'),
        *[Element(f'e{n+10}', 'option', name, y=n*40, suggestion_group='popup',
                  description='Edmonton, AB') for n,name in enumerate(NAMES)],
    ])

    async def never_decide(*args, **kwargs):
        pytest.fail('Listing the open autocomplete should not need a model call')

    async def snapshot(*args, **kwargs):
        return state

    async def evaluate(*args, **kwargs):
        return [800, 600]

    async def show(page, rows):
        if rows:
            displayed.extend(rows)
            done.set()

    _install(monkeypatch, never_decide, never_decide, [(0, Transcript('show options', True))])
    monkeypatch.setattr(main, 'get_page_state', snapshot)
    monkeypatch.setattr(FakePage, 'evaluate', evaluate, raising=False)
    monkeypatch.setattr(main, 'shortlist', lambda state, text, **kwargs: state.elements)
    monkeypatch.setattr(main.vision, 'observe', never_decide)
    monkeypatch.setattr(main.vox, 'set_options', show)
    _run_until(done)
    assert [row['label'] for row in displayed] == NAMES
    assert all(row['detail'] == 'Edmonton, AB' for row in displayed)
