"""Headless run-through of the demo tasks against tests/fixtures/pages/form.html.

Usage:
    python tests/run_demo_checks.py          # keyword fallback (no API key)
    python tests/run_demo_checks.py --live   # real Jev (needs OPENROUTER_API_KEY + VB_MODEL in .env)
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=os.getenv("VB_LOG_LEVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from backend import jev, policy
from backend.model import decide
from executor.act import execute
from perception.perceive import get_page_state, shortlist, to_prompt_text

FIXTURE = (Path(__file__).parent / "fixtures" / "pages" / "form.html").resolve().as_uri()


async def run_command(page, text: str, recent_actions: list[str]):
    t0 = time.perf_counter()
    state = await get_page_state(page)
    cands = shortlist(state, text)
    vp = page.viewport_size
    vp_tuple = (vp["width"], vp["height"]) if vp else None
    prompt_text = to_prompt_text(cands, vp_tuple)
    d = await decide(text, True, prompt_text, cands, recent_actions)
    outcome = policy.decide_outcome(d, is_final=True)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return d, outcome, cands, elapsed_ms


def _report(ok: bool, text: str, detail: str, ms: float, live: bool, failures: list[str]) -> None:
    status = "PASS" if ok else "FAIL"
    if not ok:
        failures.append(text)
    latency = f"  ({ms:.0f} ms)" if live else ""
    print(f"[{status}] {text!r} -> {detail}{latency}")


async def main(live: bool) -> int:
    load_dotenv()
    if not live:
        os.environ.pop("OPENROUTER_API_KEY", None)  # force the keyword fallback
    print(f"=== demo checks: {'LIVE (Jev)' if live else 'keyword fallback (no key)'} ===\n")

    failures: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        await page.goto(FIXTURE)
        recent_actions: list[str] = []

        # --- demo task 2: fill the appointment form by voice ---
        for text, expect_intent, expect_name in [
            ("type Sarah Chen in the name field", "type", "Full name"),
            ("next field", "click", "Email"),
            ("select Tuesday", "select", "Preferred date"),
            ("check the i agree box", "click", "I agree to the terms"),
        ]:
            d, outcome, cands, ms = await run_command(page, text, recent_actions)
            target_name = next((e.name for e in cands if e.id == d.target_id), None)
            ok = d.intent == expect_intent and target_name == expect_name and outcome == "COMMIT"
            _report(ok, text, f"intent={d.intent} target={target_name!r} outcome={outcome}", ms, live, failures)
            if ok:
                recent_actions.append(await execute(page, d))

        # --- demo task 3: destructive action must NOT auto-execute ---
        d, outcome, cands, ms = await run_command(page, "click delete account", recent_actions)
        target_name = next((e.name for e in cands if e.id == d.target_id), None)
        ok = target_name == "Delete account" and outcome == "CONFIRM"
        _report(ok, "click delete account", f"outcome={outcome} (must be CONFIRM)", ms, live, failures)

        # --- demo task 4: basics ---
        for text, expect_intent in [("scroll down", "scroll"), ("go back", "back")]:
            d, outcome, cands, ms = await run_command(page, text, recent_actions)
            ok = d.intent == expect_intent
            _report(ok, text, f"intent={d.intent}", ms, live, failures)

        await browser.close()
    await jev.aclose()

    total = 7
    print(f"\n{total - len(failures)}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use the real Jev model instead of the keyword fallback")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.live)))
