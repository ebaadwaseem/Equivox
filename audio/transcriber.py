"""PERSON 1: audio input.

Goal: replace TypedTranscriber with a real streaming one (Web Speech or MLX Whisper)
that yields partial Transcripts while the user talks and a final one at the end.
Anything that implements `async def stream()` yielding Transcript works with main.py.
"""
import asyncio
from typing import AsyncIterator

from shared.protocol import Transcript


class TypedTranscriber:
    """Fallback: type commands in the terminal. Keep this around for testing + demo backup."""

    async def stream(self) -> AsyncIterator[Transcript]:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, input, "\n🎤 say> ")
            line = line.strip()
            if line:
                yield Transcript(text=line, is_final=True)


class WhisperMLXTranscriber:
    """TODO(person 1): mic -> mlx-whisper streaming. Yield is_final=False for partials."""

    async def stream(self) -> AsyncIterator[Transcript]:
        raise NotImplementedError
        yield  # makes this an async generator
