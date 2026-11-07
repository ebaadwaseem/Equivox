# Equivox — Bare-Bones Demo Plan

**Goal:** prove the one idea the product depends on — a local decision model can perform computer use — by turning a short command into a correct browser action with the smallest possible browser-only demo.
**Not the goal:** voice, safety policy, native apps, or a product-ready assistant.

## 1. Hypothesis

> Given a compact accessibility snapshot and a short command, `d1:3b` (local, via Ollama) can choose the next UI control well enough to finish short browser tasks.

If this fails on a friendly fixture page, do not build the rest yet.

## 2. Core loop

```
typed command (stand-in for voice)
  → Playwright MCP browser_snapshot (accessibility tree + refs)
  → keep 8–15 interactive candidates
  → one d1 `choice` question: which control next?
  → deterministic role → action mapping
  → Playwright MCP click / type / select
  → re-snapshot, verify, repeat (max 5 actions)
```

Everything runs locally: no extension, daemon, API server, cloud call, or OS permission.

## 3. Scope

Keep only: **snapshot → option compiler → d1 → semantic action → verify**.

Cut from `docs/architecture.md`: Chrome extension, Python daemon, WebSocket/FastAPI, ASR/TTS/VAD, system-2 planner, skill replay, macOS AX, vision path, safety tiers, audit logs, overlay UI.

## 4. Components

| Component | Implementation |
|---|---|
| Fixture | Static `spike.html` on `127.0.0.1`: tabs, switches, a select, Save buttons, distractors, visible state line |
| Browser | Playwright MCP over stdio (`npx @playwright/mcp@latest`), headed Chrome |
| Compiler | Parse snapshot lines, keep interactive roles, attach a one-line purpose, cap at 15 |
| Decider | `POST http://localhost:11434/v1/systemone`, `d1:3b`, `keep_alive: "10m"` |
| Loop | ~150-line Python loop, max 5 actions, JSONL log |

## 5. Contracts

Snapshot lines:

```
- switch "Dark mode" [checked] [ref=e4]
- button "Save changes" [ref=e7]
```

Keep `button`, `link`, `tab`, `menuitem`, `textbox`, `checkbox`, `radio`, `switch`, `combobox`. Skip disabled and text-only nodes.

Candidate:

```json
{"id": "e4", "role": "switch", "name": "Dark mode", "state": "off",
 "purpose": "switch to a dark color scheme; dims the screen", "action": "click"}
```

`purpose` is the main accuracy lever. For the spike it is a small hand-written map; generating it automatically is future work.

d1 request (one choice per step):

```json
{
  "model": "d1:3b",
  "state": {
    "command": "dim the lights",
    "page": "Equivox Fixture — Settings",
    "screen": "state: dark=off contrast=off reduce_motion=off notifications=on digest=Daily saved=no",
    "recent": [{"action": "click", "target": "Appearance"}],
    "controls": [{"id": "e4", "role": "switch", "name": "Dark mode", "state": "off"}]
  },
  "questions": {
    "target": {
      "type": "choice",
      "instructions": "Which single control should be used next to carry out the command? Choose 'unsure' if no control fits.",
      "criteria": {
        "e4": "Dark mode switch (currently off): enable dark mode to dim the lights and darken the screen",
        "e7": "Save changes button: persist the current settings; use only after the requested change is made",
        "unsure": "No listed control fits the command; ask the user."
      }
    }
  },
  "keep_alive": "10m"
}
```

Action mapping: `button|link|tab|menuitem|checkbox|radio|switch → browser_click`; `textbox → browser_type`; `combobox → browser_select_option`. Text and select values come from the scenario, never from the model.

Two deterministic guards sit outside the model: if d1 picks the already-open tab, or is unsure while that redundant option is present, the compiler removes it and asks once more; and in the interactive REPL a separate `done`/`continue` question decides when an utterance is finished. Scenario runs use the expected state line instead.

## 6. Fixture and scenarios

The fixture is a mock settings app: Profile / Appearance / Notifications tabs; Dark mode, High contrast, Reduce motion switches; Email notifications checkbox; Digest frequency select; Save, Discard, Reset, Preview, Sign out as distractors. A visible state line (`dark=off contrast=off reduce_motion=off notifications=on digest=Daily saved=no`) makes pass/fail deterministic.

| Command | Expected | Tests |
|---|---|---|
| turn on dark mode | dark=on | exact |
| dim the lights | dark=on | paraphrase |
| stop the animations | reduce_motion=on | paraphrase |
| increase the contrast | contrast=on | direct |
| turn on dark mode and save | dark=on saved=yes | multi-step |
| turn off email notifications and save | notifications=off saved=yes | tab + multi-step |
| set digest to weekly | digest=Weekly | select |
| save my changes | saved=yes | short |
| quiet my inbox* | notifications=off | hard paraphrase |
| what is the current state?* | no change | out-of-scope → unsure |

\* Stretch. **Pass:** 6/8 core reach the exact state within 5 actions. **Kill:** fewer than 4/8 — the premise is not validated on text snapshots.

Log per step: choice, probabilities, confidence, latency, tokens, action count.

## 7. Files and run

Flat on purpose; no package layout.

```
docs/demo-plan.md
docs/research-decision-model-landscape.md
spike.html
spike.py
scenarios.json
```

```bash
ollama pull d1:3b
uv run --with mcp --with httpx spike.py          # interactive REPL
uv run --with mcp --with httpx spike.py --all    # scenario run
```

Prerequisites: Ollama 0.40.15+ with `d1:3b`, `uv`, Node/npx, and Chrome. Add `--headless` for a non-visible run.

`spike.py` serves the fixture, launches Playwright MCP with `--browser=chrome --allowed-origins http://127.0.0.1:8765`, and tears both down on exit. Build is roughly one day: smoke test, fixture, harness, compiler/decider/loop, scenario tuning, demo recording.

Spike-only safety: fixture origin, 5-action limit, no credential or payment fields, `unsure` stops the loop.

## 8. Demo (3 minutes)

1. Start; Chrome opens on the Profile tab.
2. "dim the lights" → Appearance tab → Dark mode on.
3. "now save it" → Save; status flips to `saved=yes`.
4. "stop the animations" → Reduce motion on.
5. "turn off email notifications and save" → Notifications → toggle → Save.
6. Show the log (ref, probability, latency, tokens), then the `--all` table.

The trust moment is a paraphrase making the browser do the right thing — not a scripted selector.

## 9. Next steps

Ordered by value. Prior art and measurements for each: `docs/research-decision-model-landscape.md`.

**Decision quality (small changes, do first)**

- **Fan-out side-questions.** Add `is_command` ("is this addressed to the browser at all?"), `complete`, `destructive`, and `is_correction` as extra question heads in the *same* d1 request as the target choice. Replaces the separate `completion_check` round-trip and adds the safety gates the whole ecosystem uses.
- **Contrastive criteria.** Write each option as `{what, not_for, examples}` (TypeSafe's shape; plain text works for d1) instead of a single purpose line.
- **Action outcomes in state.** Keep `said / action / target / outcome` ("navigated to …") for the last 3 actions, so "no, the other one" and "back to the results" become resolvable.
- **Span extraction.** For any typed value, regex-extract candidate spans from the command and let d1 pick one. The model never produces text — already true in scenario mode; needed for the REPL and voice.

**Voice**

- **Streaming partials.** `faster-whisper` (or whisper.cpp) partial hypotheses → debounce ~200 ms → the same decide loop. `complete`/silence gates let closed-set actions fire mid-sentence ("go back") while payload intents wait for the final transcript. This is exactly how `jev-voice-browser` acts before you finish the sentence (~300 ms decisions, 2 in-flight, older cancelled).

**Beyond the fixture**

- **Real page.** One stable public page. Expect the real limits: no purpose map, messy accessible names, 100+ candidates (cap harder, viewport-first), iframes invisible, below-fold needs scrolling. This is where an auto-generated purpose compiler earns its keep.
- **Limited real demo.** One real task end-to-end with a human confirming each action. Same code, real target, no general-web claims.
- **Numbered overlays.** On low-confidence targets, badge the top 2–3 candidates on the page and accept a spoken number. Deterministic, no model call, and a genuine accessibility affordance.

**Provider / input options**

- **Jev comparison.** `jev-1.13-free` via OpenCode answers the same request shape (~0.25 s text, ~0.42 s image with `TypeSafe-Danger-Zone-Images: true`). A head-to-head on the same snapshot is worth it: Jev's calibrated probabilities vs d1's concentration, hosted ~0.3 s vs local ~1.3 s. jev-ultrafast's dynamic per-operation target heads are the reference if target+action ever need to be joint.
- **Screenshot path.** `browser_snapshot {boxes: true}` adds viewport coordinates per element; Jev 1.14 Box emits boxes natively; OmniParser is the fallback where no DOM/AX exists. Only after the tree path is exhausted.

## 10. Input and runtime options to evaluate

- **Accessibility tree vs screenshots.** The tree gives refs and roles for free and stays the default. Screenshots need set-of-marks or coordinate grounding (`boxes: true`, Jev 1.14 Box, OmniParser) and add prefill latency. Try only after the tree path is exhausted.
- **Ollama vs MLX.** d1 MLX exports exist (`d1-3B-MLX-fp16/8bit/4bit`, `mlx-vlm>=0.7.8`, `/v1/decisions`). MLX may cut latency on Apple Silicon, but we have not measured it; vendor numbers are not comparable to our Ollama runs. Measure before switching.

## 11. Corrections and measurements

- A Chrome extension + daemon isn't needed; Playwright MCP already provides snapshots and semantic actions over stdio.
- Playwright MCP uses a single `target` argument (`e12` or a selector), not older `ref`/`element`.
- Separate target/action questions are independent in d1; use one target choice + deterministic mapping.
- 20 options is a safe cap, not an API limit (local API allows 255; practical range ~2–26).
- `confidence` is distribution concentration, not calibrated correctness.
- d1 cannot write text or call tools; the harness does the translation.
- Measured d1 latency is 0.7–1.8 s for 370–890 tokens, not the vendor's 30–60 ms.

Already measured on an M2 (Ollama 0.40.15, `d1:3b`, warm):

- 8 terse controls: paraphrases failed — "dim the lights" → unsure (0.38), "make the text easier to read" → unsure (0.44).
- Same 8, purpose-rich: Dark mode 0.62 / 0.91 s, High contrast 0.83 / 0.82 s, Reduce motion 0.95 / 0.82 s.
- 20 purpose-rich: correct at 1.6–1.8 s / ~890 tokens; "make the text easier to read" split 0.42/0.37 between High contrast and Large text.

Takeaway: option wording is the dominant variable. The hand-written purpose map is doing real work.

**Implementation result (2026-11-07):** the spike passes 8/8 core scenarios and 10/10 including stretch on an M2. Warm d1 calls are ~1.2–1.8 s; the full `--all` run takes about a minute.

**Related work:** the same architecture (closed choice, select-don't-generate, code-owned control flow) is what the Jev ecosystem ships — `jev-voice-browser`, `typesafe-computer-use`, `browser-use/jev-ultrafast`. See `docs/research-decision-model-landscape.md` for the survey, their measured numbers, and their stated limits.

## 12. Risks

| Risk | Mitigation |
|---|---|
| d1 can't ground paraphrases | rich purposes, ≤15 options, `unsure` escape, kill criterion |
| hand-written purposes overstate generality | state it; auto-generate later |
| fixture is too friendly | distractors + paraphrases; no real-web claims |
| raw-control action space caps generality (WebMCP: 25/49 raw vs 49/49 with site tools) | keep commands short; task-level actions or exposed site tools later |
| page text is untrusted (indirect prompt injection) | fixture-only for now; treat snapshot text as data; destructive gate + confirm before real pages |
| MCP browser isn't the user's session | accept; attach via extension/CDP later |
| d1 has no memory | resend compact state + last 1–3 actions with outcomes |
| model/API drift | pin Ollama version and model digest in the log |
