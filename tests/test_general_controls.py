import asyncio
import time

from playwright.async_api import async_playwright

from backend.vision import scene_revision
from executor.act import execute, switch_tab, new_tab, close_tab
from shared.protocol import Decision
from ui import vox


def test_editing_keys_clear_and_reload_act_on_real_controls():
    async def run():
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            page=await browser.new_page()
            await page.set_content('<input aria-label="Message" value="hello"><div contenteditable="true" aria-label="Notes">notes</div>')
            field=page.get_by_role('textbox',name='Message')
            await field.focus()
            await field.press('End')
            await execute(page,Decision(True,'key',text='Backspace',repeat_count=2))
            assert await field.input_value()=='hel'
            await execute(page,Decision(True,'key',text='x'))
            assert await field.input_value()=='helx'
            await execute(page,Decision(True,'clear'))
            assert await field.input_value()==''
            editor=page.locator('[contenteditable]')
            await editor.focus()
            await execute(page,Decision(True,'clear'))
            assert not (await editor.inner_text()).strip()
            loads=[]
            async def response(route):
                loads.append(1)
                await route.fulfill(body='<h1>Reloadable page</h1>',content_type='text/html')
            await page.route('https://example.test/**',response)
            await page.goto('https://example.test/')
            await execute(page,Decision(True,'reload'))
            assert len(loads)==2
            await browser.close()
    asyncio.run(run())


def test_tab_switches_by_number_first_next_and_title_and_closes_last_to_home():
    async def run():
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            context=await browser.new_context()
            await context.route('https://www.google.com/**',lambda r:r.fulfill(body='<title>Google</title>',content_type='text/html'))
            first=await context.new_page()
            await first.set_content('<title>Clinic results</title>')
            second=await new_tab(context)
            third=await context.new_page()
            await third.set_content('<title>Appointment form</title>')
            assert second.url=='https://www.google.com/'
            assert await switch_tab(context,third,'first') is first
            assert await switch_tab(context,first,'2') is second
            assert await switch_tab(context,second,'next') is third
            assert await switch_tab(context,third,'clinic') is first
            try:
                await switch_tab(context,first,'missing')
                assert False,'A missing tab must not report a successful switch'
            except ValueError:
                pass
            await second.close()
            await third.close()
            replacement=await close_tab(context,first)
            assert replacement.url=='https://www.google.com/'
            assert len(context.pages)==1
            await browser.close()
    asyncio.run(run())


def test_option_rows_are_readable_and_emit_the_same_spoken_command():
    async def run():
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            context=await browser.new_context(viewport={'width':1000,'height':720})
            commands=[]
            await context.expose_binding('voxCommand',lambda source,text:commands.append(text))
            await vox.install(context)
            page=await context.new_page()
            await page.goto('data:text/html,<h1>Choose a clinic</h1>')
            await vox.set(page,'unsure','Which clinic?')
            await vox.set_options(page,[{'label':'Clinic one with a longer name','detail':'First visible result'},
                                        {'label':'Clinic two','detail':'Second visible result'}])
            buttons=page.locator('#vox-host').locator('#choices-list button')
            assert await buttons.count()==2
            bounds1=await buttons.nth(0).bounding_box()
            bounds2=await buttons.nth(1).bounding_box()
            assert bounds2['y']>=bounds1['y']+bounds1['height']
            await buttons.nth(1).click()
            assert commands==['click option 2']
            await page.screenshot(path='/tmp/equivox-options.png')
            await browser.close()
    asyncio.run(run())


def test_last_transcript_survives_idle_and_navigation_until_cooldown(monkeypatch):
    async def run():
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            context=await browser.new_context()
            await vox.install(context)
            page=await context.new_page()
            await page.goto('data:text/html,<h1>Page</h1>')
            await vox.set(page,'listening','click option two')
            await vox.set(page,'idle')
            assert vox._current==('idle','click option two')
            await page.goto('data:text/html,<h1>Other page</h1>')
            await vox.show_on(page)
            await page.wait_for_function("document.getElementById('vox-host').shadowRoot.getElementById('text').textContent.includes('click option two')")
            assert 'click option two' in await page.locator('#vox-host').locator('#text').text_content()
            monkeypatch.setattr(vox,'_last_message_at',time.monotonic()-31)
            await vox.set(page,'idle')
            await page.wait_for_timeout(180)
            assert 'Say a browser command' in await page.locator('#vox-host').locator('#text').text_content()
            await browser.close()
    asyncio.run(run())


def test_visual_scene_changes_invalidate_choices_but_overlay_changes_do_not():
    async def run():
        async with async_playwright() as p:
            browser=await p.chromium.launch()
            page=await browser.new_page()
            await page.set_content('<h1>Choices</h1><button>Clinic one</button>')
            await vox.set(page,'idle')
            before=await scene_revision(page)
            await vox.set(page,'listening','show options')
            assert await scene_revision(page)==before
            await page.locator('button',has_text='Clinic one').evaluate("el=>el.textContent='Different clinic'")
            assert await scene_revision(page)!=before
            await browser.close()
    asyncio.run(run())
