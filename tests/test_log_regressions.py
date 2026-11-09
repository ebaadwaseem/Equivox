import asyncio
import os
import sys

from audio.transcriber import TypedTranscriber
from backend.commands import ordinal_reply,inspection_request,shortcut
from perception.perceive import find_deterministic_target
from shared.protocol import Element


def test_command_forms_from_real_log():
    assert ordinal_reply('Quick option one.',3)==1
    assert ordinal_reply('Choose the third option.',3)==3
    assert inspection_request('Show me my options.')=='options'
    assert shortcut('Remove text.').intent=='clear'
    assert shortcut('Control V.').text=='ControlOrMeta+V'
    assert shortcut('Paste link').text=='ControlOrMeta+V'


def test_ordinal_autocomplete_and_video_do_not_pick_toolbar_controls():
    elements=[Element('e1','button','Guide'),Element('e2','link','Home'),
              Element('e3','option','First suggestion'),Element('e4','option','Second suggestion'),
              Element('e5','option','Third suggestion'),Element('e6','link','First video',is_video_link=True)]
    assert find_deterministic_target(elements,'choose the third option').id=='e5'
    assert find_deterministic_target(elements,'click the first video').id=='e6'


def test_terminal_cancellation_does_not_wait_for_input_and_ignores_arrow_escapes(monkeypatch):
    async def run():
        read,write=os.pipe()
        stream=os.fdopen(read)
        monkeypatch.setattr(sys,'stdin',stream)
        generator=TypedTranscriber().stream()
        task=asyncio.create_task(anext(generator))
        try:
            os.write(write,b'\x1b[A\x1b[B\nreload tab\n')
            assert (await asyncio.wait_for(task,1)).text=='reload tab'
            waiting=asyncio.create_task(anext(generator))
            await asyncio.sleep(.01)
            waiting.cancel()
            await asyncio.gather(waiting,return_exceptions=True)
            await generator.aclose()
        finally:
            os.close(write)
            stream.close()
    asyncio.run(run())
