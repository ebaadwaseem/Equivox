"""PERSON 1: real on-device voice input, via the Swift listener in audio/apple_speech.

Same shape as TypedTranscriber (`async def stream()` yielding Transcript), plus
side-channel level/status callbacks main.py uses to drive Vox's mic-level ring and
"Paused"/error display, and pause()/resume() to mute the mic without killing the
subprocess. If the listener process dies, it's restarted with backoff; it never takes
typed input (terminal/overlay box) down with it.
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional

from shared.protocol import Transcript

logger = logging.getLogger("apple_transcriber")

BIN_PATH = Path(__file__).parent.parent / "bin" / "vox-listen"
RESTART_BACKOFF_S = [0.5, 1, 2, 4, 5]  # caps at 5s and repeats from there


def is_available() -> bool:
    return BIN_PATH.exists()


class AppleSpeechTranscriber:
    def __init__(
        self,
        on_level: Optional[Callable[[float], Awaitable[None]]] = None,
        on_status: Optional[Callable[[str, str], Awaitable[None]]] = None,
        endpoint_ms: int = 700,
        allow_server: bool = False,
        command: Optional[list[str]] = None,
    ):
        self._on_level = on_level
        self._on_status = on_status
        self._endpoint_ms = endpoint_ms
        self._allow_server = allow_server
        # lets tests point this at tests/fakes/fake_listen.py instead of the real
        # Swift binary; defaults to [str(BIN_PATH)].
        self._command = command or [str(BIN_PATH)]
        self._process: Optional[asyncio.subprocess.Process] = None
        self._restart_attempt = 0

    async def stream(self) -> AsyncIterator[Transcript]:
        while True:
            try:
                async for t in self._run_once():
                    yield t
                    self._restart_attempt = 0  # a clean message means the process is healthy
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("voice listener process error: %s", e)
            delay = RESTART_BACKOFF_S[min(self._restart_attempt, len(RESTART_BACKOFF_S) - 1)]
            self._restart_attempt += 1
            logger.info("restarting voice listener in %.1fs", delay)
            if self._on_status:
                await self._on_status("error", f"voice listener restarting in {delay:.0f}s")
            await asyncio.sleep(delay)

    async def _run_once(self) -> AsyncIterator[Transcript]:
        args = [*self._command, "--endpoint-ms", str(self._endpoint_ms)]
        if self._allow_server:
            args.append("--allow-server")
        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
        )
        stderr_task = asyncio.create_task(self._drain_stderr(self._process))
        try:
            assert self._process.stdout is not None
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break  # process exited
                async for t in self._handle_line(line):
                    yield t
        finally:
            stderr_task.cancel()
            if self._process and self._process.returncode is None:
                self._process.kill()
            if self._process:
                await self._process.wait()
            self._process = None
        raise RuntimeError("voice listener process exited")

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            logger.warning("vox-listen stderr: %s", line.decode(errors="replace").rstrip())

    async def _handle_line(self, raw: bytes) -> AsyncIterator[Transcript]:
        try:
            msg = json.loads(raw.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.debug("ignoring malformed listener line: %r", raw[:200])
            return
        if not isinstance(msg, dict):
            return
        msg_type = msg.get("type")
        if msg_type == "partial":
            text = str(msg.get("text") or "").strip()
            if text:
                yield Transcript(text=text, is_final=False, id=msg.get("id"))
        elif msg_type == "final":
            text = str(msg.get("text") or "").strip()
            if text:
                try:
                    confidence = float(msg.get("confidence", 1.0))
                except (TypeError, ValueError):
                    confidence = 1.0
                yield Transcript(text=text, is_final=True, confidence=confidence, id=msg.get("id"))
        elif msg_type == "level":
            if self._on_level:
                try:
                    await self._on_level(float(msg.get("value", 0.0)))
                except (TypeError, ValueError):
                    pass
        elif msg_type == "status":
            if self._on_status:
                await self._on_status(str(msg.get("value", "")), str(msg.get("detail", "")))
        # unknown message types are ignored, not an error

    async def pause(self) -> None:
        await self._send_control("pause")

    async def resume(self) -> None:
        await self._send_control("resume")

    async def send_vocab(self, words: list[str]) -> None:
        """Up to 100 distinctive words from the CURRENT page, merged into the Swift
        listener's contextualStrings for its next new request -- a misheard word is
        much less likely when it's already visible on screen."""
        await self._send_control("vocab " + json.dumps({"words": words[:100]}))

    async def _send_control(self, cmd: str) -> None:
        if self._process is None or self._process.stdin is None:
            return
        try:
            self._process.stdin.write((cmd + "\n").encode())
            await self._process.stdin.drain()
        except Exception as e:
            logger.debug("could not send %r to listener: %s", cmd, e)
