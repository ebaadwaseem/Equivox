"""Browser regressions for real controls, open shadow roots, frames, and history."""
import asyncio

from playwright.async_api import async_playwright

from backend.context import ConversationContext
from executor.act import execute, new_tab
from perception.perceive import get_page_state, shortlist
from shared.protocol import Decision
from ui import vox


def test_native_select_shadow_editor_and_frame_input():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content('''
              <h1>Booking</h1>
              <label for="province">Province</label><select id="province">
                <option value="AB">Alberta</option><option value="BC">British Columbia</option>
                <option>New Brunswick</option><option>Newfoundland</option>
              </select>
              <input type="password" aria-label="Password" value="secret">
              <div id="editor-host"></div>
              <iframe srcdoc='<label>Name<input id="name"></label>'></iframe>
            ''')
            await page.locator('#editor-host').evaluate('''el => {
              el.attachShadow({mode:'open'}).innerHTML = '<div role="textbox" aria-label="Message" contenteditable="true">old text</div>';
            }''')
            await page.frames[1].locator('input').wait_for()
            state = await get_page_state(page, with_screenshot=True)
            import base64
            from io import BytesIO
            from PIL import Image
            image = Image.open(BytesIO(base64.b64decode(state.screenshot_b64)))
            assert image.width <= 768 and image.height <= 512
            province = next(e for e in state.elements if e.name == "Province")
            assert province.options == ["Alberta", "British Columbia", "New Brunswick", "Newfoundland"]
            assert "selected Alberta" in await execute(page, Decision(True, "select", province.id, "alberta"))
            assert await page.locator('select').input_value() == "AB"
            assert "ambiguous" in await execute(page, Decision(True, "select", province.id, "New"))
            assert await page.locator('select').input_value() == "AB"
            password = next(e for e in state.elements if e.name == "Password")
            assert password.value is None
            editor = next(e for e in state.elements if e.name == "Message")
            assert editor.role == "textbox"
            await execute(page, Decision(True, "type", editor.id, "Hello from speech"))
            assert await page.get_by_role('textbox', name='Message').inner_text() == "Hello from speech"
            frame_input = next(e for e in state.elements if e.name == "Name")
            assert frame_input.id.startswith("f1:")
            await execute(page, Decision(True, "type", frame_input.id, "Sarah"))
            assert await page.frames[1].locator('input').input_value() == "Sarah"
            assert "Booking" in state.summary
            await browser.close()
    asyncio.run(run())


def test_new_home_tab_search_uses_the_new_page_and_encodes_the_query():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()
            original = await context.new_page()
            await context.route('https://www.google.com/**', lambda route: route.fulfill(body='<h1>Google</h1>', content_type='text/html'))
            page = await new_tab(context)
            assert page is not original
            assert page.url == "https://www.google.com/"
            urls = []
            async def search_route(route):
                urls.append(route.request.url)
                await route.fulfill(body="<h1>Search results</h1>", content_type="text/html")
            await page.route('https://duckduckgo.com/**', search_route)
            await execute(page, Decision(True, "search", text="physiotherapy clinics in edmonton"))
            assert "q=physiotherapy+clinics+in+edmonton" in page.url
            assert original.url == "about:blank"
            assert len(urls) == 1
            await browser.close()
    asyncio.run(run())


def test_history_survives_navigation_and_marks_only_the_model_window():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context()
            await vox.install(context)
            page = await context.new_page()
            history = ConversationContext(max_turns=2)
            for n in range(3):
                history.commit(f"search for clinic {n}", Decision(True, "search", text=f"clinic {n}"), outcome="done")
            await vox.set_history(page, history.history, history.turns)
            await vox.show_on(page)
            await page.goto('data:text/html,<h1>Results</h1>')
            await vox.show_on(page)
            rows = page.locator('#vox-host').locator('li')
            assert await rows.count() == 3
            assert await rows.first.get_attribute('data-context') == "false"
            assert await rows.last.get_attribute('data-context') == "true"
            await page.locator("#vox-host").locator("summary").click()
            assert await rows.first.inner_text() == "search for clinic 0\nsearch · clinic 0 · done"
            await browser.close()
    asyncio.run(run())


def test_overlay_keeps_long_speech_and_history_inside_viewport():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(viewport={"width": 1000, "height": 720})
            await vox.install(context)
            page = await context.new_page()
            await page.set_content('<body style="background:#e5edf2;font:24px system-ui;padding:40px"><h1>Clinic search</h1><p>Physiotherapy clinics in Edmonton</p></body>')
            context_log = ConversationContext(max_turns=3)
            for said, intent, payload in [
                ("open wikipedia", "navigate", "wikipedia.org"),
                ("open a new tab", "new_tab", None),
                ("search for physiotherapy clinics in edmonton", "search", "physiotherapy clinics in edmonton"),
                ("okay now scroll down", "scroll", None),
            ]:
                context_log.commit(said, Decision(True, intent, text=payload), outcome="done")
            await vox.set_history(page, context_log.history, context_log.turns)
            speech = 'open a new tab, then search for physiotherapy clinics in edmonton, okay now scroll down'
            await vox.set(page, 'listening', speech)
            await page.locator('#vox-host').locator('summary').click()
            await page.wait_for_timeout(200)
            assert (await page.locator('#vox-host').locator('#text').text_content()).strip() == speech
            bounds = await page.locator('#vox-host').locator('.panel').bounding_box()
            assert bounds['y'] >= 0
            assert bounds['x'] >= 0
            await page.screenshot(path='/tmp/equivox-overlay.png')
            await browser.close()
    asyncio.run(run())
