"""End-to-end tests for the Vox panel's typed-command box: context.expose_binding()
bridges it to Python (ONE queue, like the terminal), and the resulting Transcript runs
through the real decide()/execute() pipeline exactly like any other input.
"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

from backend.model import decide
from executor.act import execute, highlight
from perception.perceive import get_page_state, shortlist, to_prompt_text
from ui import vox

FORM_FIXTURE = (Path(__file__).parent / "fixtures" / "pages" / "form.html").resolve().as_uri()
SECOND_FIXTURE = (Path(__file__).parent / "fixtures" / "pages" / "second.html").resolve().as_uri()


async def _submit_via_overlay(page, text: str) -> None:
    await page.evaluate(
        """(text) => {
            const input = document.getElementById('vox-host').shadowRoot.getElementById('cmd-input');
            input.focus();
            input.value = text;
            input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
        }""",
        text,
    )


def _run(coro):
    return asyncio.run(coro)


def test_typing_in_overlay_input_runs_the_command_on_form_html():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context()
            queue: asyncio.Queue = asyncio.Queue()

            def on_command(source, text):
                text = (text or "").strip()
                if text:
                    queue.put_nowait(text)

            await ctx.expose_binding("voxCommand", on_command)
            await vox.install(ctx)
            page = await ctx.new_page()
            await page.goto(FORM_FIXTURE)
            await page.wait_for_selector("#vox-host", timeout=3000)

            await _submit_via_overlay(page, "click book appointment")
            text = await asyncio.wait_for(queue.get(), timeout=3)

            state = await get_page_state(page)
            cands = shortlist(state, text)
            d = await decide(text, True, to_prompt_text(cands), cands, [])
            if d.target_id:
                await highlight(page, d.target_id)
            result = await execute(page, d)

            await browser.close()
            return text, d.intent, d.target_id, result

    text, intent, target_id, result = _run(scenario())
    assert text == "click book appointment"
    assert intent == "click"
    assert target_id == "e12"  # "Book Appointment" button in form.html
    assert "did click" in result


def test_overlay_input_clears_and_shows_optimistic_listening_state():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context()
            queue: asyncio.Queue = asyncio.Queue()
            await ctx.expose_binding(
                "voxCommand",
                lambda source, text: queue.put_nowait(text) if (text or "").strip() else None,
            )
            await vox.install(ctx)
            page = await ctx.new_page()
            await page.goto(FORM_FIXTURE)
            await page.wait_for_selector("#vox-host", timeout=3000)

            await _submit_via_overlay(page, "scroll down")
            await asyncio.wait_for(queue.get(), timeout=3)

            cleared = await page.evaluate(
                "() => document.getElementById('vox-host').shadowRoot.getElementById('cmd-input').value"
            )
            state = await page.evaluate(
                """() => {
                    const sr = document.getElementById('vox-host').shadowRoot;
                    return sr.querySelector('.stage').dataset.state;
                }"""
            )
            await browser.close()
            return cleared, state

    cleared, state = _run(scenario())
    assert cleared == ""
    assert state == "listening"


def test_binding_survives_navigation_and_new_tab():
    """The binding is registered on the CONTEXT, not a page -- it must keep working
    after navigating to a different page, and on a brand-new tab, with zero extra
    per-page wiring."""
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context()
            queue: asyncio.Queue = asyncio.Queue()

            def on_command(source, text):
                text = (text or "").strip()
                if text:
                    queue.put_nowait(text)

            await ctx.expose_binding("voxCommand", on_command)
            await vox.install(ctx)

            page = await ctx.new_page()
            await page.goto(FORM_FIXTURE)
            await page.wait_for_selector("#vox-host", timeout=3000)

            # navigate to a second page -- the binding must still work there
            await page.goto(SECOND_FIXTURE)
            await page.wait_for_selector("#vox-host", timeout=3000)
            await _submit_via_overlay(page, "scroll down")
            from_second_page = await asyncio.wait_for(queue.get(), timeout=3)

            # open a brand new tab -- the binding must work there too, with no
            # extra per-page setup (that's the whole point of expose_binding on
            # the context vs. the old per-page expose_function approach)
            new_page = await ctx.new_page()
            await new_page.goto(FORM_FIXTURE)
            await new_page.wait_for_selector("#vox-host", timeout=3000)
            await _submit_via_overlay(new_page, "go back")
            from_new_tab = await asyncio.wait_for(queue.get(), timeout=3)

            await browser.close()
            return from_second_page, from_new_tab

    from_second_page, from_new_tab = _run(scenario())
    assert from_second_page == "scroll down"
    assert from_new_tab == "go back"


def test_empty_and_whitespace_input_is_ignored():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context()
            queue: asyncio.Queue = asyncio.Queue()

            def on_command(source, text):
                text = (text or "").strip()
                if text:
                    queue.put_nowait(text)

            await ctx.expose_binding("voxCommand", on_command)
            await vox.install(ctx)
            page = await ctx.new_page()
            await page.goto(FORM_FIXTURE)
            await page.wait_for_selector("#vox-host", timeout=3000)

            await _submit_via_overlay(page, "   ")  # whitespace only -- submitCommand() no-ops
            await _submit_via_overlay(page, "scroll down")  # real command, should still go through
            text = await asyncio.wait_for(queue.get(), timeout=3)
            empty_afterward = queue.empty()  # only one item was ever enqueued
            await browser.close()
            return text, empty_afterward

    text, empty_afterward = _run(scenario())
    assert text == "scroll down"
    assert empty_afterward
