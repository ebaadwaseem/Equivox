"""Builds the "state" text sent to the model. Stable prefix first (page + elements),
then context, then the transcript -- in that order every time -- so a prompt cache
keyed on the prefix is reused across partials.
"""
MAX_BYTES = 64 * 1024
MAX_TRANSCRIPT_CHARS = 400
MAX_RECENT_ACTIONS = 5


def build_state_text(
    elements_text: str,
    transcript: str,
    recent_actions: list[str] | None = None,
    page: dict | None = None,
    context=None,
    pending_confirmation: str | None = None,
) -> str:
    recent_actions = recent_actions or []
    parts = []
    if page:
        parts.append(f'PAGE: {page.get("url", "")} / {page.get("title", "")} / {page.get("site", "")}')
    parts += ["ELEMENTS:", elements_text]

    context_text = context.encode() if context is not None else ""
    if context_text:
        parts += ["", "CONTEXT:", context_text]
    if pending_confirmation:
        parts += ["", f"PENDING_CONFIRMATION: {pending_confirmation}"]

    if not context_text and recent_actions:
        actions_block = "\n".join(recent_actions[-MAX_RECENT_ACTIONS:])
        parts += ["", "RECENT ACTIONS:", actions_block]

    parts += ["", f"TRANSCRIPT: {transcript[-MAX_TRANSCRIPT_CHARS:]}"]
    text = "\n".join(parts)
    encoded = text.encode()
    if len(encoded) > MAX_BYTES:
        text = encoded[:MAX_BYTES].decode(errors="ignore")
    return text
