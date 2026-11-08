"""Split explicit command connectors, respecting quoted payloads.

This is segmentation, not planning: each clause needs a fresh page decision.
"""
import re

_COMMAND = r"(?:open|search|scroll|click|tap|type|enter|write|select|choose|go|navigate|switch|close|find|look|zoom|back|forward|undo|delete)\b"
_BOUNDARY = re.compile(
    rf"(?:[,;.!?]\s*(?:(?:and|then|now|okay|ok|also|please)\b[\s,]*)*|"
    rf"\s+(?:and\s+then|and\s+now|okay\s+now|then|and|now)\s+)(?={_COMMAND})", re.I)
_FILLER = re.compile(r"^(?:(?:and|then|now|okay|ok|also|please)\b[\s,;]*)+", re.I)


def first_clause(text: str) -> tuple[str, int, bool]:
    """Return command, consumed character end, and whether another command bounds it."""
    quoted = set()
    quote = None
    for i, char in enumerate(text):
        if char in '\"“”' or (char == "'" and (i == 0 or not text[i - 1].isalnum())):
            quote = None if quote else char
        if quote:
            quoted.add(i)
    for match in _BOUNDARY.finditer(text):
        if match.start() not in quoted and text[:match.start()].strip():
            return _FILLER.sub("", text[:match.start()].strip()), match.start(), True
    return _FILLER.sub("", text.strip()), len(text), False
