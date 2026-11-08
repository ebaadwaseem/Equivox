# Equivox

A local-first, voice-driven computer-use co-pilot for accessibility, built around Liquid AI's [`d1`](https://www.liquid.ai/blog/d1-open) decision model running on [Ollama](https://ollama.com/library/d1).

The core idea: **d1 is a system-1 policy, not an agent.** It does not write text, plans, or coordinates. Equivox compiles the user's speech and a compact accessibility snapshot into a closed set of options, lets d1 pick in one forward pass, then applies a deterministic safety gate and executes a semantic UI action.

## MVP direction

- **First surface:** Chrome extension + local Python daemon (DOM/accessibility tree, no OS permissions).
- **Second surface:** macOS Accessibility (AX) adapter for native apps.
- **Vision:** fallback only, downscaled and set-of-marks; measured ~10 s for a 1100×700 screenshot on an M2.
- **Voice:** ASR text primary; `d1-omni` raw audio as an optional stop/intent gate.
- **System-2:** optional and off the hot path; deterministic skills + d1 first.
- **Safety:** deterministic red-tier blocks and confirmation around d1, never model judgment alone.

## Bare-bones spike

A working browser-only spike lives in [`spike.py`](spike.py), with [`spike.html`](spike.html) and [`scenarios.json`](scenarios.json). It uses Playwright MCP for the browser and local `d1:3b` via Ollama for decisions. See [`docs/demo-plan.md`](docs/demo-plan.md) for the plan and measured results.

```bash
ollama pull d1:3b
uv run --with mcp --with httpx spike.py          # interactive REPL
uv run --with mcp --with httpx spike.py --all    # 10 scenarios
```

## Read the architecture

See [`docs/architecture.md`](docs/architecture.md) for:

- what d1 can and cannot do;
- browser-only vs macOS AX vs full-OS vs vision-only scope evaluation;
- component diagram, turn lifecycle, state schema, and decision compiler;
- ASR, system-2, Reader, safety, privacy, and accessibility design;
- prior art (OpenAdapt, UFO², Agent S, UI-TARS, browser-use, Stagehand, Pipecat, OS voice control);
- latency measurements, delivery plan, evaluation metrics, and risks.

## Status

Architecture draft; a bare-bones browser spike is implemented (8/8 core scenarios, 10/10 including stretch).
