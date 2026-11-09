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
from backend.commands import NUMBERS, ORDINALS

MAX_TURNS = 12


@dataclass
class Turn:
    said: str
    intent: Optional[str] = None
    target: Optional[str] = None
    text: Optional[str] = None
    outcome: str = "running"
    ts: float = field(default_factory=time.time)

    def encode(self) -> str:
        parts = [f'said="{self.said[:240]}"', f"intent={self.intent or 'none'}"]
        if self.target:
            parts.append(f'target="{self.target[:80]}"')
        if self.text:
            parts.append(f'text="{self.text[:240]}"')
        parts.append(f"outcome={self.outcome[:240]}")
        return "; ".join(parts)


class ConversationContext:
    def __init__(self, max_turns: int = MAX_TURNS):
        self.turns: deque[Turn] = deque(maxlen=max_turns)
        self.history: deque[Turn] = deque(maxlen=100)
        self._consumed: dict[int, tuple[str, int]] = {}
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
        self.history.append(turn)
        return turn

    def finish(self, turn: Turn, outcome: str) -> None:
        if turn is not None:
            turn.outcome = outcome

    def remember_page(self, url: str, title: str = "") -> None:
        self.previous_page = {"url": url, "title": title}

    def consume(self, utterance_id: Optional[int], said: str) -> None:
        if utterance_id is None:
            return
        prefix = " ".join(said.split()).lower()
        existing, _ = self._consumed.get(utterance_id, ("", 0))
        # Resolving a question may refer back to its original clause. Do not
        # un-consume a longer reply that has already arrived in the same breath.
        if existing and self._starts_with_prefix(existing, prefix):
            return
        self.active_prefix = prefix
        self.consumed_id = utterance_id
        self.consumed_gen = self.generation(utterance_id) + 1
        self._consumed[utterance_id] = (self.active_prefix, self.consumed_gen)
        if len(self._consumed) > 128:
            del self._consumed[next(iter(self._consumed))]

    def generation(self, utterance_id) -> int:
        return self._consumed.get(utterance_id, ("", 0))[1]

    def slice(self, utterance_id: Optional[int], text: str) -> tuple[Optional[str], Optional[int | str]]:
        """Return (current command, virtual id). None means the recognizer rewrote words
        already executed, or too little new text remains to be a command."""
        prefix, gen = self._consumed.get(utterance_id, ("", 0))
        if not prefix:
            return text, utterance_id

        normalized = " ".join(text.split())
        end = self._prefix_end(normalized, prefix)
        if end is None:
            return None, utterance_id
        remainder = normalized[end:].lstrip(" ,;.!?-")
        remainder = re.sub(r"^(?:(?:and|now|then|also|please|okay|ok)\b[\s,;]*)+", "", remainder, flags=re.I)
        if not remainder or (len(remainder.split()) < 2 and remainder.lower() not in {"back", "forward", "undo", "more", "again", "reload", "refresh", "backspace", "delete", "clear", "yes", "no", "confirm", "cancel", "sure"}):
            return None, utterance_id
        return remainder, f"{utterance_id}+{gen}"

    def was_consumed(self, utterance_id: Optional[int], text: str) -> bool:
        prefix, _ = self._consumed.get(utterance_id, ("", 0))
        if not prefix:
            return False
        return self._starts_with_prefix(" ".join(text.split()), prefix)

    @staticmethod
    def _starts_with_prefix(text: str, prefix: str) -> bool:
        return ConversationContext._prefix_end(text, prefix) is not None

    @staticmethod
    def _prefix_end(text: str, prefix: str) -> Optional[int]:
        # Apple revises punctuation between partial/final results. Compare words
        # while preserving the newest transcript's character offsets for slicing.
        words = list(re.finditer(r"\w+(?:['’]\w+)*", text.lower()))
        consumed = re.findall(r"\w+(?:['’]\w+)*", prefix.lower())
        def canonical(word):
            word = word.replace('’', "'")
            return str(NUMBERS.get(word, ORDINALS.get(word, word)))
        if not consumed or [canonical(w.group()) for w in words[:len(consumed)]] != [canonical(w) for w in consumed]:
            return None
        return words[len(consumed) - 1].end()

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
