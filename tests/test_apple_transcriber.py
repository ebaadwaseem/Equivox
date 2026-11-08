"""Exercises audio/apple_transcriber.py against tests/fakes/fake_listen.py (a scripted
fake of the Swift listener) -- no microphone, no Speech framework, no real subprocess
binary required. Covers the subprocess-management half of the voice input feature; the
early-commit decision logic itself is unit-tested in tests/test_early_commit.py.
"""
import asyncio
import sys
from pathlib import Path

import pytest

from audio.apple_transcriber import AppleSpeechTranscriber
from shared.protocol import Transcript

FAKE_LISTEN = [sys.executable, str(Path(__file__).parent / "fakes" / "fake_listen.py")]


def _script(tmp_path: Path, lines: list[str]) -> list[str]:
    path = tmp_path / "script.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return [*FAKE_LISTEN, str(path)]


async def _collect(transcriber: AppleSpeechTranscriber, count: int, timeout: float = 5.0) -> list[Transcript]:
    out: list[Transcript] = []
    agen = transcriber.stream()
    try:
        async with asyncio.timeout(timeout):
            async for t in agen:
                out.append(t)
                if len(out) >= count:
                    break
    finally:
        await agen.aclose()
    return out


def test_partial_then_final_flow(tmp_path):
    command = _script(tmp_path, [
        '{"type":"partial","id":1,"text":"click"}',
        '{"type":"partial","id":1,"text":"click sub"}',
        '{"type":"final","id":1,"text":"click submit","confidence":0.92}',
    ])
    transcriber = AppleSpeechTranscriber(command=command)

    async def run():
        return await _collect(transcriber, 3)

    results = asyncio.run(run())
    assert [t.text for t in results] == ["click", "click sub", "click submit"]
    assert [t.is_final for t in results] == [False, False, True]
    assert all(t.id == 1 for t in results)
    assert results[-1].confidence == pytest.approx(0.92)


def test_level_and_status_callbacks(tmp_path):
    command = _script(tmp_path, [
        '{"type":"level","value":0.42}',
        '{"type":"status","value":"listening","detail":""}',
        '{"type":"final","id":1,"text":"scroll down"}',
    ])
    levels: list[float] = []
    statuses: list[tuple[str, str]] = []

    async def on_level(v):
        levels.append(v)

    async def on_status(kind, detail):
        statuses.append((kind, detail))

    transcriber = AppleSpeechTranscriber(on_level=on_level, on_status=on_status, command=command)

    async def run():
        return await _collect(transcriber, 1)

    results = asyncio.run(run())
    assert [t.text for t in results] == ["scroll down"]
    assert levels == [0.42]
    assert statuses == [("listening", "")]


def test_malformed_lines_are_ignored(tmp_path):
    command = _script(tmp_path, [
        "not even json {{{",
        '{"type":"partial"',  # truncated JSON
        '{"type":"final","id":1,"text":"go back","confidence":0.8}',
    ])
    transcriber = AppleSpeechTranscriber(command=command)

    async def run():
        return await _collect(transcriber, 1)

    results = asyncio.run(run())
    assert [t.text for t in results] == ["go back"]
    assert results[0].is_final is True


def test_restarts_with_backoff_after_process_crash(tmp_path):
    command = _script(tmp_path, [
        '{"type":"partial","id":1,"text":"click"}',
        "__CRASH__",
    ])
    transcriber = AppleSpeechTranscriber(command=command)
    transcriber._restart_attempt = 0

    async def run():
        # bypass the real backoff sleep so the test doesn't take 0.5s+ -- we only care
        # that it DOES restart and keeps yielding, not the exact delay.
        orig_sleep = asyncio.sleep

        async def fast_sleep(_delay):
            await orig_sleep(0)

        asyncio.sleep = fast_sleep
        try:
            return await _collect(transcriber, 2, timeout=5.0)
        finally:
            asyncio.sleep = orig_sleep

    results = asyncio.run(run())
    # the fake script restarts from the top each time it's relaunched, so a crash
    # followed by a clean restart yields the same first partial twice in a row.
    assert [t.text for t in results] == ["click", "click"]


def test_pause_and_resume_write_to_stdin(tmp_path):
    command = _script(tmp_path, ['{"type":"partial","id":1,"text":"hi"}'])
    transcriber = AppleSpeechTranscriber(command=command)

    async def run():
        agen = transcriber.stream()
        try:
            async with asyncio.timeout(5.0):
                async for _ in agen:
                    break
            # process is alive and past its scripted lines; pause/resume must not raise
            await transcriber.pause()
            await transcriber.resume()
        finally:
            await agen.aclose()

    asyncio.run(run())
