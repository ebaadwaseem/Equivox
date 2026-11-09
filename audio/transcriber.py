"""PERSON 1: audio input.

Goal: replace TypedTranscriber with a real streaming one (Web Speech or MLX Whisper)
that yields partial Transcripts while the user talks and a final one at the end.
Anything that implements `async def stream()` yielding Transcript works with main.py.
"""
import asyncio
import re
import sys
import os
from typing import AsyncIterator

from shared.protocol import Transcript


class TypedTranscriber:
    """Fallback: type commands in the terminal. Keep this around for testing + demo backup."""

    async def stream(self) -> AsyncIterator[Transcript]:
        loop = asyncio.get_running_loop()
        lines = asyncio.Queue()
        descriptor = sys.stdin.fileno()
        pending = b""
        def ready():
            nonlocal pending
            chunk = os.read(descriptor, 4096)
            if not chunk:
                loop.remove_reader(descriptor)
                if pending:
                    lines.put_nowait(pending.decode(errors="replace"))
                    pending = b""
                lines.put_nowait("")
                return
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                lines.put_nowait(line.decode(errors="replace") + "\n")
        loop.add_reader(descriptor, ready)
        try:
            while True:
                print("\n🎤 say> ", end="", flush=True)
                line = await lines.get()
                if not line:
                    return
                line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip()
                if line:
                    yield Transcript(text=line, is_final=True)
        finally:
            loop.remove_reader(descriptor)



class WhisperMLXTranscriber:
    """TODO(person 1): mic -> mlx-whisper streaming. Yield is_final=False for partials."""

    async def stream(self) -> AsyncIterator[Transcript]:
        raise NotImplementedError
        yield  # makes this an async generator
