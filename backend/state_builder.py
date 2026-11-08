"""Builds the "state" text sent to the model. Stable prefix first (the page/elements
block), then the transcript, then recent action history -- in that order, every time
-- so a prompt cache keyed on the prefix actually gets reused across partials.
"""
MAX_BYTES = 64 * 1024
MAX_RECENT_ACTIONS = 5


def build_state_text(elements_text: str, transcript: str, recent_actions: list[str] | None = None) -> str:
    recent_actions = recent_actions or []
    parts = ["PAGE ELEMENTS:", elements_text, "", f"TRANSCRIPT: {transcript}"]
    actions_block = "\n".join(recent_actions[-MAX_RECENT_ACTIONS:])
    if actions_block:
        parts += ["", "RECENT ACTIONS:", actions_block]
    text = "\n".join(parts)
    encoded = text.encode()
    if len(encoded) > MAX_BYTES:
        text = encoded[:MAX_BYTES].decode(errors="ignore")
    return text
