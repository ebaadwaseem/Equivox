"""PERSON 3: model client.

decide() gets the transcript + candidate elements and returns a Decision.
- If OPENROUTER_API_KEY is set: calls OpenRouter (OpenAI-compatible chat API).
  TODO: swap the prompt for Jev's typed-question format (is_command, intent, target, complete).
- Otherwise: a dumb keyword matcher, so the pipeline runs with no key.
"""
import json
import os
import re

import httpx

from shared.protocol import Decision, Element

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.getenv("VB_MODEL", "")   # TODO(person 3): set to the Jev model id on OpenRouter

SYSTEM = """You control a web browser for a user speaking commands.
Given the transcript and a list of page elements, reply with ONLY JSON:
{"is_command": bool, "intent": "click|type|scroll|navigate|back|forward|search|none",
 "target_id": "eNN or null", "text": "string or null", "scroll_amount": int, "complete": bool,
 "confidence": 0-1}"""


async def decide(transcript: str, elements_text: str, elements: list[Element]) -> Decision:
    key = os.getenv("OPENROUTER_API_KEY")
    if key and MODEL:
        return await _llm(transcript, elements_text, key)
    return _keyword_fallback(transcript, elements)


async def _llm(transcript: str, elements_text: str, key: str) -> Decision:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"ELEMENTS:\n{elements_text}\n\nTRANSCRIPT: {transcript}"},
                ],
            },
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    data = json.loads(re.search(r"\{.*\}", content, re.S).group(0))
    return Decision(**{k: v for k, v in data.items() if k in Decision.__dataclass_fields__})


def _keyword_fallback(transcript: str, elements: list[Element]) -> Decision:
    t = transcript.lower().strip()
    if t in ("back", "go back"):
        return Decision(True, "back")
    if t in ("forward", "go forward"):
        return Decision(True, "forward")
    if t.startswith("scroll"):
        return Decision(True, "scroll", scroll_amount=-600 if "up" in t else 600)
    if m := re.match(r"(?:go to|open) (\S+\.\S+)", t):
        return Decision(True, "navigate", text=m.group(1))
    if m := re.match(r"search(?: for)? (.+)", t):
        return Decision(True, "search", text=m.group(1))
    if m := re.match(r"(?:type|write) (.+)", t):
        box = next((e for e in elements if e.role == "textbox"), None)
        return Decision(True, "type", target_id=box.id if box else None, text=m.group(1))
    # "click X": the first shortlisted element whose name matches any of the words
    words = [w for w in re.sub(r"^(click|press|open|tap)\s+", "", t).split() if len(w) > 2]
    for e in elements:
        if any(w in e.name.lower() for w in words):
            return Decision(True, "click", target_id=e.id)
    return Decision(False, "none")
