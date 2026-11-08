#!/usr/bin/env python3
"""Equivox spike: accessibility snapshot -> d1 choice -> Playwright MCP action.

Run:
    uv run --with mcp --with httpx spike.py
    uv run --with mcp --with httpx spike.py --all
    uv run --with mcp --with httpx spike.py --all --headless
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE_DIR = Path(__file__).resolve().parent
FIXTURE = BASE_DIR / "spike.html"
SCENARIOS = BASE_DIR / "scenarios.json"
DEFAULT_PORT = 8765
DEFAULT_OLLAMA = "http://localhost:11434"
DEFAULT_MODEL = "d1:3b"
MODEL = DEFAULT_MODEL
OLLAMA = DEFAULT_OLLAMA
LOG_PATH = BASE_DIR / "spike-log.jsonl"

INTERACTIVE_ROLES = {
    "button",
    "link",
    "tab",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "textbox",
    "searchbox",
    "checkbox",
    "radio",
    "switch",
    "combobox",
}
CLICK_ROLES = {
    "button",
    "link",
    "tab",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "checkbox",
    "radio",
    "switch",
}

# The option compiler's descriptions are the main accuracy lever. Keep these
# purpose-rich and written in the user's vocabulary, not the widget's.
PURPOSES = {
    "Profile": "open profile settings: display name, account, and sign out",
    "Appearance": (
        "dark mode and dim the lights/screen; increase contrast; reduce or stop "
        "motion and animations"
    ),
    "Notifications": (
        "open notification settings: email notifications, inbox noise, quiet mode, "
        "and digest frequency"
    ),
    "Dark mode": "enable dark mode to dim the lights and darken the screen",
    "High contrast": "increase contrast so text is easier to read",
    "Reduce motion": "stop animations and moving effects",
    "Email notifications": (
        "turn email notifications on or off; turn them off to quiet your inbox, "
        "stop email updates, and reduce inbox noise"
    ),
    "Digest frequency": "choose how often email digests are sent: Daily, Weekly, or Never",
    "Send test email": "send a test email to verify notification delivery",
    "Display name": "edit the display name shown to other people",
    "Sign out": "sign out of the account",
    "Help center": "open help documentation",
    "Save changes": (
        "persist the current settings; use only after the requested change has been "
        "made, or when the user only asks to save"
    ),
    "Discard changes": "throw away unsaved edits and restore the last saved state",
    "Preview theme": "preview the theme without saving it",
    "Reset to defaults": "restore all settings to their default values",
}

SNAPSHOT_LINE = re.compile(r'^\s*-\s+([A-Za-z][\w-]*)(?:\s+"((?:[^"\\]|\\.)*)")?(.*)$')
REF = re.compile(r"\[ref=((?:f\d+)?e\d+)\]")
FLAG = re.compile(r"\[(checked|selected|disabled|expanded|pressed)\]")
STATE_LINE = re.compile(r'state:\s*([a-z][\w]*=[^\s"]+(?:\s+[a-z][\w]*=[^\s"]+)*)')


@dataclass
class Control:
    ref: str
    role: str
    name: str
    checked: bool | None = None
    selected: bool | None = None
    disabled: bool = False
    value: str | None = None

    def state_text(self) -> str:
        bits: list[str] = []
        if self.checked is not None:
            bits.append("on" if self.checked else "off")
        if self.selected is not None:
            bits.append("selected" if self.selected else "not selected")
        if self.value:
            bits.append(f"value {self.value!r}")
        return ", ".join(bits)


def unescape(text: str) -> str:
    return text.replace('\\"', '"').replace("\\\\", "\\")


def parse_controls(snapshot: str) -> list[Control]:
    controls: list[Control] = []
    for line in snapshot.splitlines():
        match = SNAPSHOT_LINE.match(line)
        if not match:
            continue
        role, raw_name, rest = match.group(1), match.group(2), match.group(3)
        if role not in INTERACTIVE_ROLES:
            continue

        ref_match = REF.search(rest)
        if not ref_match:
            continue
        flags = set(FLAG.findall(rest))
        if "disabled" in flags:
            continue

        name = unescape(raw_name) if raw_name else role
        checked = None
        if role in {"checkbox", "radio", "switch"}:
            checked = "checked" in flags
        selected = None
        if role == "tab":
            selected = "selected" in flags

        value = None
        value_match = re.search(r":\s+(.+)$", rest)
        if value_match and role in {"textbox", "searchbox", "combobox", "button", "link"}:
            value = value_match.group(1).strip().strip('"')

        controls.append(
            Control(
                ref=ref_match.group(1),
                role=role,
                name=name,
                checked=checked,
                selected=selected,
                disabled="disabled" in flags,
                value=value,
            )
        )
    return controls


def parse_state(snapshot: str) -> dict[str, str]:
    match = STATE_LINE.search(snapshot)
    if not match:
        return {}
    state: dict[str, str] = {}
    for part in match.group(1).split():
        if "=" in part:
            key, value = part.split("=", 1)
            state[key] = value
    return state


def state_matches(state: dict[str, str], expected: dict[str, str]) -> bool:
    return all(state.get(key) == value for key, value in expected.items())


def purpose_for(control: Control) -> str:
    if control.name in PURPOSES:
        return PURPOSES[control.name]
    if control.role == "tab":
        return f"open the {control.name} section"
    if control.role in {"textbox", "searchbox"}:
        return f"enter text into {control.name}"
    if control.role == "combobox":
        return f"choose a value for {control.name}"
    return f"activate {control.name}"


def compile_candidates(controls: list[Control], limit: int = 15) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for control in controls[:limit]:
        state = control.state_text()
        description = f"{control.name} {control.role}"
        if state:
            description += f" (currently {state})"
        description += f": {purpose_for(control)}"
        candidates.append(
            {
                "id": control.ref,
                "role": control.role,
                "name": control.name,
                "state": state,
                "criteria": description,
                "control": control,
            }
        )
    return candidates


class Browser:
    def __init__(self, session: ClientSession):
        self.session = session

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> str:
        result = await self.session.call_tool(tool, arguments or {})
        is_error = bool(
            getattr(result, "isError", False) or getattr(result, "is_error", False)
        )
        text = "\n".join(
            getattr(item, "text", "") for item in getattr(result, "content", [])
        )
        if is_error:
            raise RuntimeError(f"{tool} failed: {text}")
        return text

    async def navigate(self, url: str) -> None:
        await self.call("browser_navigate", {"url": url})

    async def snapshot(self) -> str:
        return await self.call("browser_snapshot", {})


def d1_payload(
    command: str,
    state_line: str,
    candidates: list[dict[str, Any]],
    recent: list[dict[str, str]],
) -> dict[str, Any]:
    criteria = {candidate["id"]: candidate["criteria"] for candidate in candidates}
    criteria["unsure"] = "No listed control fits the command; ask the user."

    instructions = (
        "Which single control should be used next to carry out the command? "
        "Choose 'unsure' if no control fits."
    )

    return {
        "model": MODEL,
        "state": {
            "command": command,
            "page": "Equivox Fixture — Settings",
            "screen": state_line,
            "recent": recent[-3:],
            "controls": [
                {
                    "id": candidate["id"],
                    "role": candidate["role"],
                    "name": candidate["name"],
                    "state": candidate["state"],
                }
                for candidate in candidates
            ],
        },
        "questions": {
            "target": {
                "type": "choice",
                "instructions": instructions,
                "criteria": criteria,
            }
        },
        "keep_alive": "10m",
    }


async def d1_choose(
    client: httpx.AsyncClient,
    command: str,
    state_line: str,
    candidates: list[dict[str, Any]],
    recent: list[dict[str, str]],
) -> dict[str, Any]:
    payload = d1_payload(command, state_line, candidates, recent)
    started = time.perf_counter()
    response = await client.post(f"{OLLAMA}/v1/systemone", json=payload)
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    data = response.json()
    answer = data.get("answers", {}).get("target", {})
    choice = answer.get("choice")
    if not choice:
        raise RuntimeError(f"d1 returned no choice: {data}")
    return {
        "choice": choice,
        "confidence": answer.get("confidence"),
        "probabilities": answer.get("probabilities", {}),
        "latency": elapsed,
        "tokens": data.get("usage", {}).get("input_tokens"),
        "payload": payload,
    }


async def completion_check(
    client: httpx.AsyncClient, command: str, state_line: str
) -> dict[str, Any]:
    payload = {
        "model": MODEL,
        "state": {"command": command, "screen": state_line},
        "questions": {
            "status": {
                "type": "choice",
                "instructions": "Is the user's command fully satisfied by the current screen state?",
                "criteria": {
                    "done": "Yes, the current screen state already fully satisfies the command.",
                    "continue": "No, the command still needs another action.",
                },
            }
        },
        "keep_alive": "10m",
    }
    started = time.perf_counter()
    response = await client.post(f"{OLLAMA}/v1/systemone", json=payload)
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    data = response.json()
    answer = data.get("answers", {}).get("status", {})
    return {
        "choice": answer.get("choice"),
        "confidence": answer.get("confidence"),
        "probabilities": answer.get("probabilities", {}),
        "latency": elapsed,
        "tokens": data.get("usage", {}).get("input_tokens"),
    }


def requests_save(command: str) -> bool:
    return bool(re.search(r"\bsave\b", command.lower()))


async def execute(
    browser: Browser, control: Control, value: str | None
) -> str:
    if control.role in CLICK_ROLES:
        await browser.call(
            "browser_click", {"target": control.ref, "element": control.name}
        )
        return "click"
    if control.role in {"textbox", "searchbox"}:
        if value is None:
            raise RuntimeError(f"no text value supplied for {control.name!r}")
        await browser.call(
            "browser_type",
            {"target": control.ref, "element": control.name, "text": value},
        )
        return "type"
    if control.role == "combobox":
        if value is None:
            raise RuntimeError(f"no select value supplied for {control.name!r}")
        await browser.call(
            "browser_select_option",
            {"target": control.ref, "element": control.name, "values": [value]},
        )
        return "select"
    raise RuntimeError(f"unsupported role {control.role!r}")


def log_record(record: dict[str, Any]) -> None:
    record = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def top_probabilities(probabilities: dict[str, float], count: int = 2) -> str:
    ranked = sorted(probabilities.items(), key=lambda item: item[1], reverse=True)
    return ", ".join(f"{key}={value:.2f}" for key, value in ranked[:count])


async def run_noop(
    browser: Browser, client: httpx.AsyncClient, command: str, scenario: str
) -> dict[str, Any]:
    snapshot = await browser.snapshot()
    state = parse_state(snapshot)
    controls = parse_controls(snapshot)
    candidates = compile_candidates(controls)
    if not candidates:
        return {"status": "NO CONTROLS", "state": state, "steps": []}

    answer = await d1_choose(
        client, command, state_text(state), candidates, []
    )
    chosen = next(
        (candidate for candidate in candidates if candidate["id"] == answer["choice"]),
        None,
    )
    if chosen and chosen["role"] == "tab" and chosen["state"].startswith("selected"):
        candidates = [
            candidate for candidate in candidates if candidate["id"] != chosen["id"]
        ]
        answer = await d1_choose(
            client, command, state_text(state), candidates, []
        )
    status = "PASS" if answer["choice"] == "unsure" else "FAIL"
    step = {
        "kind": "decision",
        "choice": answer["choice"],
        "confidence": answer["confidence"],
        "probabilities": answer["probabilities"],
        "latency": answer["latency"],
        "tokens": answer["tokens"],
        "expected": "unsure",
    }
    log_record({"scenario": scenario, "command": command, **step})
    print(
        f"  [no-op] d1 -> {answer['choice']} ({top_probabilities(answer['probabilities'])}) "
        f"{answer['latency']:.2f}s {answer['tokens']} tok"
    )
    return {"status": status, "state": state, "steps": [step]}


async def run_command(
    browser: Browser,
    client: httpx.AsyncClient,
    command: str,
    expected: dict[str, str] | None,
    value: str | None,
    max_steps: int,
    scenario: str,
) -> dict[str, Any]:
    recent: list[dict[str, str]] = []
    steps: list[dict[str, Any]] = []

    for index in range(max_steps):
        snapshot = await browser.snapshot()
        state = parse_state(snapshot)
        if expected is not None and state_matches(state, expected):
            return {"status": "PASS", "state": state, "steps": steps}

        controls = parse_controls(snapshot)
        candidates = compile_candidates(controls)
        if not candidates:
            return {"status": "NO CONTROLS", "state": state, "steps": steps}

        answer = await d1_choose(
            client, command, state_text(state), candidates, recent
        )
        chosen = next(
            (candidate for candidate in candidates if candidate["id"] == answer["choice"]),
            None,
        )
        selected_tabs = [
            candidate
            for candidate in candidates
            if candidate["role"] == "tab" and candidate["state"].startswith("selected")
        ]
        retry = bool(selected_tabs) and (
            answer["choice"] == "unsure"
            or (chosen is not None and any(chosen is tab for tab in selected_tabs))
        )
        if retry:
            print("      (removing the already-open tab from the options and re-asking)")
            candidates = [
                candidate
                for candidate in candidates
                if not any(candidate is tab for tab in selected_tabs)
            ]
            answer = await d1_choose(
                client, command, state_text(state), candidates, recent
            )
        choice = answer["choice"]
        print(
            f"  [{index + 1}] d1 -> {choice} "
            f"({top_probabilities(answer['probabilities'])}) "
            f"{answer['latency']:.2f}s {answer['tokens']} tok"
        )

        if choice == "unsure":
            log_record(
                {
                    "scenario": scenario,
                    "command": command,
                    "step": index + 1,
                    "choice": choice,
                    "confidence": answer["confidence"],
                    "probabilities": answer["probabilities"],
                    "latency": answer["latency"],
                    "tokens": answer["tokens"],
                }
            )
            return {"status": "UNSURE", "state": state, "steps": steps}

        by_id = {candidate["id"]: candidate for candidate in candidates}
        candidate = by_id.get(choice)
        if candidate is None:
            return {"status": "BAD CHOICE", "state": state, "steps": steps}

        control: Control = candidate["control"]
        try:
            action = await execute(browser, control, value)
        except Exception as exc:  # noqa: BLE001 - surface the failure in the table
            print(f"      action failed: {exc}")
            return {"status": "ERROR", "state": state, "steps": steps, "error": str(exc)}

        step = {
            "scenario": scenario,
            "command": command,
            "step": index + 1,
            "choice": choice,
            "role": control.role,
            "name": control.name,
            "action": action,
            "confidence": answer["confidence"],
            "probabilities": answer["probabilities"],
            "latency": answer["latency"],
            "tokens": answer["tokens"],
            "screen_before": state_text(state),
        }
        steps.append(step)
        log_record(step)
        recent.append({"action": action, "name": control.name, "role": control.role})

        # In the interactive demo, a Save click ends the utterance. Otherwise,
        # ask a separate completion question instead of trusting a `done` option
        # in the main choice (which d1 handles poorly).
        if expected is None:
            snapshot = await browser.snapshot()
            after = parse_state(snapshot)
            if control.name == "Save changes":
                return {"status": "PASS", "state": after, "steps": steps}
            if requests_save(command) and after.get("saved") != "yes":
                continue
            completion = await completion_check(client, command, state_text(after))
            print(
                f"      completion -> {completion['choice']} "
                f"({top_probabilities(completion['probabilities'])}) "
                f"{completion['latency']:.2f}s"
            )
            log_record(
                {
                    "scenario": scenario,
                    "command": command,
                    "kind": "completion",
                    "choice": completion["choice"],
                    "confidence": completion["confidence"],
                    "probabilities": completion["probabilities"],
                    "latency": completion["latency"],
                    "tokens": completion["tokens"],
                }
            )
            if completion["choice"] == "done":
                return {"status": "PASS", "state": after, "steps": steps}

    snapshot = await browser.snapshot()
    return {"status": "MAX STEPS", "state": parse_state(snapshot), "steps": steps}


def state_text(state: dict[str, str]) -> str:
    if not state:
        return "state: unknown"
    return "state: " + " ".join(f"{key}={value}" for key, value in state.items())


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass


def start_server(port: int) -> ThreadingHTTPServer:
    handler = lambda *args, **kwargs: QuietHandler(  # noqa: E731
        *args, directory=str(BASE_DIR), **kwargs
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


async def check_ollama(client: httpx.AsyncClient) -> None:
    try:
        response = await client.get(f"{OLLAMA}/api/tags", timeout=10)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"Ollama is not reachable at {OLLAMA}: {exc}")
        print("Start it with `ollama serve` and pull the model with `ollama pull d1:3b`.")
        raise SystemExit(1)

    tags = response.json().get("models", [])
    names = {model.get("name") for model in tags}
    if MODEL not in names:
        print(f"warning: {MODEL!r} is not in `ollama list` (found: {sorted(names)})")


async def run_all(
    browser: Browser, client: httpx.AsyncClient, max_steps: int, url: str
) -> int:
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    results: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for scenario in scenarios:
        print(f"\n=== {scenario['name']} ===")
        print(f"command: {scenario['command']!r}")
        await browser.navigate(url)
        if scenario.get("expect_no_action"):
            result = await run_noop(
                browser, client, scenario["command"], scenario["name"]
            )
        else:
            result = await run_command(
                browser,
                client,
                scenario["command"],
                scenario.get("expect", {}),
                scenario.get("value"),
                max_steps,
                scenario=scenario["name"],
            )
        results.append((scenario, result))
        print(f"  -> {result['status']}  {state_text(result['state'])}")

    print("\n=== results ===")
    core_pass = 0
    core_total = 0
    for scenario, result in results:
        stretch = scenario.get("stretch", False)
        if not stretch:
            core_total += 1
            if result["status"] == "PASS":
                core_pass += 1
        marker = "PASS" if result["status"] == "PASS" else result["status"]
        step_count = len(result["steps"])
        suffix = " (stretch)" if stretch else ""
        print(f"{marker:10} {scenario['name']:38} {step_count} step(s){suffix}")

    print(f"\ncore: {core_pass}/{core_total}")
    if core_pass < 4:
        print("KILL CRITERION: fewer than 4/8 core scenarios passed.")
    elif core_pass >= 6:
        print("PASS BAR: at least 6/8 core scenarios passed.")
    else:
        print("Below the pass bar (6/8), above the kill criterion (4/8).")
    return 0 if core_pass >= 6 else 1


async def repl(browser: Browser, client: httpx.AsyncClient, max_steps: int, url: str) -> None:
    await browser.navigate(url)
    snapshot = await browser.snapshot()
    print(f"Fixture ready at {url}")
    print(state_text(parse_state(snapshot)))
    print("Type a command. Use `command | value` for text/select values.")
    print("`reset` reloads the fixture; `quit` exits.\n")

    while True:
        try:
            line = (await asyncio.to_thread(input, "command> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in {"quit", "exit"}:
            break
        if line.lower() == "reset":
            await browser.navigate(url)
            snapshot = await browser.snapshot()
            print("  reset:", state_text(parse_state(snapshot)))
            continue

        command, _, value = line.partition("|")
        command = command.strip()
        value = value.strip() or None
        result = await run_command(
            browser,
            client,
            command,
            expected=None,
            value=value,
            max_steps=max_steps,
            scenario="interactive",
        )
        print(f"  -> {result['status']}  {state_text(result['state'])}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="run scenarios.json")
    parser.add_argument("--headless", action="store_true", help="run Chrome headless")
    parser.add_argument("--max-steps", type=int, default=5)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--ollama", default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA)
    )
    parser.add_argument("--log", default=str(BASE_DIR / "spike-log.jsonl"))
    args = parser.parse_args()

    global MODEL, OLLAMA, LOG_PATH
    MODEL = args.model
    OLLAMA = args.ollama
    LOG_PATH = Path(args.log)
    LOG_PATH.write_text("", encoding="utf-8")

    if not FIXTURE.exists():
        print(f"missing fixture: {FIXTURE}")
        return 1

    url = f"http://127.0.0.1:{args.port}/spike.html"
    httpd = start_server(args.port)

    mcp_args = [
        "-y",
        "@playwright/mcp@latest",
        "--browser=chrome",
        "--isolated",
        "--allowed-origins",
        f"http://127.0.0.1:{args.port}",
    ]
    if args.headless:
        mcp_args.append("--headless")

    params = StdioServerParameters(command="npx", args=mcp_args)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
            await check_ollama(client)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    browser = Browser(session)
                    try:
                        if args.all:
                            return await run_all(browser, client, args.max_steps, url)
                        await repl(browser, client, args.max_steps, url)
                        return 0
                    finally:
                        try:
                            await browser.call("browser_close")
                        except Exception:  # noqa: BLE001 - best-effort cleanup
                            pass
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
