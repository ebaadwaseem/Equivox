"""Split explicit command connectors, respecting quoted payloads.

This is segmentation, not planning: each clause needs a fresh page decision.
"""
import re

_COMMAND = r"(?:open|search|scroll|click|tap|type|enter|write|fill|set|pick|select|choose|go|navigate|switch|close|find|look|zoom|back|forward|undo|delete|press|hit|clear|reload|refresh|pan|move|plan|tell|describe|show)\b"
_BOUNDARY = re.compile(
    rf"(?:[,;.!?]\s*(?:(?:and|then|now|okay|ok|also|please)\b[\s,]*)*|"
    rf"\s+(?:and\s+then|and\s+now|okay\s+now|then|and|now)\s+)(?={_COMMAND})", re.I)
_FILLER = re.compile(r"^(?:(?:and|then|now|okay|ok|also|please)\b[\s,;]*)+", re.I)


# Elliptical form lists share their leading verb: "fill name X, email Y".
_FORM_FIELD = r"(?:(?:my|the)\s+)?(?:name|first name|last name|email(?: address)?|phone(?: number)?|address|city|postal code|zip code)\b"
_FORM_BOUNDARY = re.compile(rf"(?:[,;]\s*(?:and\s+)?|\s+and\s+)(?={_FORM_FIELD}\s+\S)", re.I)


def first_clause(text: str, field_labels=()) -> tuple[str, int, bool]:
    """Return command, consumed character end, and whether another command bounds it."""
    quoted = set()
    quote = None
    for i, char in enumerate(text):
        if char in '\"“”' or (char == "'" and (i == 0 or not text[i - 1].isalnum())):
            quote = None if quote else char
        if quote:
            quoted.add(i)
    form_start = re.match(rf"^(?:(?:fill(?:\s+in)?|set|enter|type|write)\s+)?{_FORM_FIELD}\s+", text, re.I)
    form_matches = list(_FORM_BOUNDARY.finditer(text)) if form_start else []
    # On forms, actual observed labels let punctuation-free ASR separate fields.
    labels = [re.escape(label.strip().rstrip(' :*')) for label in field_labels if label.strip()]
    if labels:
        label = '(?:' + '|'.join(sorted(labels, key=len, reverse=True)) + ')'
        start = re.match(rf'^(?:(?:fill(?:\s+in)?|set|enter|type|write)\s+)?(?:(?:my|the)\s+)?{label}\s+', text, re.I)
        if start:
            boundary = re.compile(rf'(?:[,;]\s*(?:and\s+)?|\s+(?:and\s+)?)(?=(?:(?:my|the)\s+)?{label}\s+\S)', re.I)
            form_matches.extend(m for m in boundary.finditer(text) if m.start() >= start.end())
    for match in sorted(list(_BOUNDARY.finditer(text)) + form_matches, key=lambda m: m.start()):
        if match.start() not in quoted and text[:match.start()].strip():
            return _FILLER.sub("", text[:match.start()].strip()), match.start(), True
    return _FILLER.sub("", text.strip()), len(text), False
