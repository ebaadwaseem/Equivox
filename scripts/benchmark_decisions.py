"""Small synthetic decision calibration; no browser mutations.

Run: python -m scripts.benchmark_decisions --live
Reports observed latency and predicted commits. Uses the configured Jev route/key;
results are a small diagnostic sample, not a general accuracy benchmark.
"""
import argparse
import asyncio
import json
import os
import time

from dotenv import load_dotenv

from backend import jev, policy
from backend.context import ConversationContext
from backend.model import decide
from perception.perceive import to_prompt_text
from shared.protocol import Decision, Element

CASES = [
    ("open a new", "none", False),
    ("open a new tab", "new_tab", False),
    ("search for", "none", False),
    ("search for physiotherapy clinics in edmonton", "search", True),
    ("scroll down", "scroll", False),
    ("type Sarah Chen into the name field", "type", True),
    ("select Alberta from the province dropdown", "select", True),
]


async def run():
    elements = [Element("e01", "textbox", "Name"),
                Element("e02", "select", "Province", options=["Alberta", "British Columbia"])]
    context = ConversationContext()
    context.commit("open a new tab", Decision(True, "new_tab"), outcome="done")
    latencies = []
    try:
        for text, expected, final in CASES:
            started = time.perf_counter()
            decision = await decide(text, final, to_prompt_text(elements), elements,
                                    page={"url": "about:blank", "title": ""}, context=context)
            ms = (time.perf_counter() - started) * 1000
            latencies.append(ms)
            result = policy.evaluate(decision, final)
            print(json.dumps(dict(transcript=text, expected=expected, intent=decision.intent,
                                  text=decision.text, act_now=decision.act_now, policy=result.outcome,
                                  ms=round(ms), target=decision.target_id)))
        ordered = sorted(latencies)
        print(json.dumps(dict(samples=len(ordered), p50_ms=round(ordered[len(ordered)//2]),
                              max_ms=round(ordered[-1]))))
    finally:
        await jev.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Call the configured model on synthetic commands")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to run API calls")
    load_dotenv()
    if not os.getenv("OPENROUTER_API_KEY") or not os.getenv("VB_MODEL"):
        parser.error("Configure OPENROUTER_API_KEY and VB_MODEL before running the live probe")
    asyncio.run(run())
