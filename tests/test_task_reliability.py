import asyncio

from playwright.async_api import async_playwright

from backend.clauses import first_clause
from backend.forms import form_entry
from backend.model import decide, _keyword_fallback
from backend.choices import ranked_targets
from executor.act import execute
from perception.perceive import get_page_state, shortlist, to_prompt_text
from shared.protocol import Decision, Element


def test_grounded_punctuation_free_form_chain():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content('''<label>Name<input id="name"></label>
                <label>Email<input id="email" type="email"></label>
                <label>Phone<input id="phone" type="tel"></label>
                <label>Day<select id="day"><option>Monday</option><option>Tuesday</option></select></label>''')
            remaining = 'Fill in my name Sarah Chen email sarah@gmail.com phone 780 555 1234 and pick Tuesday'
            actions = []
            while remaining:
                state = await get_page_state(page)
                labels = [e.name for e in state.elements if e.role == 'textbox']
                clause, end, bounded = first_clause(remaining, labels)
                candidates = shortlist(state, clause)
                d = await decide(clause, True, to_prompt_text(candidates), candidates)
                actions.append(d.intent)
                result = await execute(page, d)
                assert not result.startswith(('skipped', 'no match'))
                remaining = remaining[end:].lstrip(' ,;')
            assert actions == ['type', 'type', 'type', 'select']
            assert await page.locator('#name').input_value() == 'Sarah Chen'
            assert await page.locator('#email').input_value() == 'sarah@gmail.com'
            assert await page.locator('#phone').input_value() == '780 555 1234'
            assert await page.locator('#day').input_value() == 'Tuesday'
            await browser.close()
    asyncio.run(run())


def test_native_dropdown_opens_as_voice_choices_and_selects_enabled_option():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content('''<label>Day<select><option>Monday</option>
                <optgroup label="Unavailable" disabled><option>Wednesday</option></optgroup>
                <option>Tuesday</option></select></label>''')
            element = (await get_page_state(page)).elements[0]
            assert await execute(page, Decision(True, 'click', target_id=element.id)) == 'opened native dropdown'
            focused = (await get_page_state(page)).elements[0]
            assert focused.active
            assert focused.options == ['Monday', 'Tuesday']
            assert await execute(page, Decision(True, 'select', target_id=element.id, text='Tuesday')) == 'selected Tuesday'
            await browser.close()
    asyncio.run(run())


def test_form_entries_match_arbitrary_labels_and_do_not_guess_duplicate_fields():
    elements = [Element('e01', 'textbox', 'Organization'), Element('e02', 'textbox', 'Email', input_type='email')]
    d = form_entry('fill in my Organization Acme Incorporated', elements)
    assert (d.target_id, d.text) == ('e01', 'Acme Incorporated')
    assert form_entry('email sarah at gmail dot com', elements).text == 'sarah@gmail.com'
    duplicate = elements + [Element('e03', 'textbox', 'Email')]
    assert form_entry('email sarah@gmail.com', duplicate).ambiguous
    assert _keyword_fallback('type hello', elements).target_id is None


def test_options_do_not_pad_jev_ranking_with_unrelated_controls():
    elements = [Element('e01', 'button', 'Account'), Element('e02', 'textbox', 'Email'), Element('e03', 'textbox', 'Phone')]
    d = Decision(True, 'type', target_probabilities={'e01': .8, 'e02': .19, 'e03': .001})
    assert ranked_targets(d, elements) == [elements[1]]


def test_quoted_form_payload_is_not_split_and_search_is_not_a_form_list():
    text = 'fill name "Sarah email Chen" and pick Tuesday'
    assert first_clause(text, ['name', 'email'])[0] == 'fill name "Sarah email Chen"'
    assert first_clause('search for name badges and email templates')[2] is False


def test_keyword_click_does_not_choose_arbitrarily_between_equal_matches():
    elements = [Element('e01', 'link', 'North clinic'), Element('e02', 'link', 'South clinic'), Element('e03', 'button', 'Account')]
    d = _keyword_fallback('click clinic', elements)
    assert d.ambiguous and d.target_id is None
    assert ranked_targets(d, elements) == elements[:2]
    exact = _keyword_fallback('click South clinic', elements)
    assert exact.target_id == 'e02' and not exact.ambiguous
