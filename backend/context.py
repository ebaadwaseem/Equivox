"""Rolling conversation context for the model: the last few committed turns plus the
page before the last navigation. Only executed actions belong here -- chatter that was
ignored must never influence the next decision.
"""
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from shared.protocol import Decision

MAX_TURNS = 6


@dataclass
class Turn:
    said: str
    intent: Optional[str] = None
    target: Optional[str] = None
    text: Optional[str] = None
    outcome: str = "running"
    ts: float = field(default_factory=time.time)

    def encode(self) -> str:
        parts = [f'said="{self.said[:120]}"', f"intent={self.intent or 'none'}"]
        if self.target:
            parts.append(f'target="{self.target[:80]}"')
        if self.text:
            parts.append(f'text="{self.text[:80]}"')
        parts.append(f"outcome={self.outcome}")
        return "; ".join(parts)


class ConversationContext:
    def __init__(self, max_turns: int = MAX_TURNS):
        self.turns: deque[Turn] = deque(maxlen=max_turns)
        self.previous_page: Optional[dict] = None
        self.active_prefix: Optional[str] = None
        self.consumed_id: Optional[int] = None
        self.consumed_gen = 0

    def commit(self, said: str, decision: Decision, target_label: Optional[str] = None, outcome: str = "running") -> Turn:
        turn = Turn(
            said=said.strip(),
            intent=decision.intent,
            target=target_label or decision.target_id,
            text=decision.text,
            outcome=outcome,
        )
        self.turns.append(turn)
        return turn

    def finish(self, turn: Turn, outcome: str) -> None:
        if turn is not None:
            turn.outcome = outcome

    def remember_page(self, url: str, title: str = "") -> None:
        self.previous_page = {"url": url, "title": title}

    def consume(self, utterance_id: Optional[int], said: str) -> None:
        if utterance_id is None:
            return
        self.active_prefix = " ".join(said.split()).lower()
        self.consumed_id = utterance_id
        self.consumed_gen += 1

    def slice(self, utterance_id: Optional[int], text: str) -> tuple[Optional[str], Optional[int | str]]:
        """Return (current command, virtual id). None means the recognizer rewrote words
        already executed, or too little new text remains to be a command."""
        if utterance_id is None or self.consumed_id != utterance_id or not self.active_prefix:
            return text, utterance_id

        normalized = " ".join(text.split())
        prefix = self.active_prefix
        if not self._starts_with_prefix(normalized, prefix):
            return None, utterance_id
        remainder = normalized[len(prefix):].lstrip(" ,;.!?-")
        remainder = re.sub(r"^(?:(?:and|now|then|also|please)\b[\s,;]*)+", "", remainder, flags=re.I)
        if len(remainder.split()) < 2:
            return None, utterance_id
        return remainder, f"{utterance_id}+{self.consumed_gen}"

    def was_consumed(self, utterance_id: Optional[int], text: str) -> bool:
        if utterance_id is None or self.consumed_id != utterance_id or not self.active_prefix:
            return False
        return self._starts_with_prefix(" ".join(text.split()), self.active_prefix)

    @staticmethod
    def _starts_with_prefix(text: str, prefix: str) -> bool:
        lower = text.lower()
        if not lower.startswith(prefix):
            return False
        return len(lower) == len(prefix) or not lower[len(prefix)].isalnum()

    def encode(self) -> str:
        lines = []
        if self.previous_page:
            url = self.previous_page.get("url", "")
            title = self.previous_page.get("title", "")
            lines.append(f'previous_page: {url} ("{title[:120]}")')
        for turn in self.turns:
            lines.append(turn.encode())
        return "\n".join(lines)

    def last_turn(self) -> Optional[Turn]:
        return self.turns[-1] if self.turns else None
