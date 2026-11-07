# Equivox — Expansion Plan: from typed spike to voiced demo

**Status:** Proposal for review
**Date:** 2026-11-07
**Builds on:** `spike.py` (validated: 10/10 scenarios), `docs/demo-plan.md` §9, `docs/architecture.md`, `docs/research-decision-model-landscape.md`
**Goal:** turn the typed REPL spike into a hands-free, voice-driven demo with a real UI — keeping everything local except, optionally, the microphone's speech API.

---

## 0. Where we are

The spike proved the one hypothesis that matters (demo-plan §1): a local `d1:3b` call over a compact control list picks the right UI control for short commands, including paraphrases. Measured on an M2: warm decisions 1.2–1.8 s, completion check 0.21 s, 10/10 scenarios.

What the spike deliberately is not (demo-plan §3):

- **No voice.** Typed REPL stands in for speech (`spike.py:654`).
- **No product UI.** Terminal only; no transcript panel, no feedback on the page.
- **Fixture-only perception.** The screen parser is regex over Playwright MCP's text snapshot (`spike.py:129–170`), and the accuracy lever is a 13-entry hand-written `PURPOSES` map (`spike.py:67–96`) — works only because we wrote the fixture ourselves.
- **Two round-trips per step** (target choice + separate completion check, `spike.py:314–344`), and a `Save changes` special-case hack (`spike.py:538`).

This plan closes those four gaps in the order that de-risks them. It follows the strategy the ecosystem already validated (research doc §4): fan-out typed questions in one request, code owns control flow, act on partial transcripts, never generate text.

## 1. The expansion in one line

> Speak at the browser; partial transcripts stream into the same decide loop the spike proved; d1 answers intent/target/safety/completion in one call; code gates and executes; the page highlights what is about to happen; a control panel shows everything the system sees and decided.

Still **not** the goal (unchanged from architecture.md §3.1): autonomous multi-app agents, credentials/payments, cross-platform parity, vision-first control.

## 2. Target architecture (delta from the spike)

```
 control panel (any Chrome tab)          daemon (Python, asyncio)                controlled window
 ┌───────────────────────────┐   ws     ┌──────────────────────────────┐    Playwright (in-process)
 │ mic: Web Speech API  ─────┼─────────▶│ orchestrator (state machine) │──▶ [collector script in page]
 │  OR getUserMedia+16k PCM  │          │  ├─ ASR worker (local opt)   │◀──   roles/names/states/bboxes
 │ partials + finals ────────┼─────────▶│  ├─ purpose compiler         │◀── mutation events (dirty flag)
 │ typed command box         │          │  ├─ shortlist scorer         │
 │ timeline, decisions,      │◀─────────┤  ├─ d1 client (fan-out heads)│    overlay.js (injected)
 │ candidates, confirmations │          │  └─ policy + verifier        │──▶ highlight / numbered badges
 └───────────────────────────┘          │ Ollama d1:3b /v1/systemone   │    / toast (window.__eq)
                                        └──────────────────────────────┘
```

Concrete changes:

| Spike | Expansion | Why |
|---|---|---|
| REPL input | Daemon (FastAPI + WebSocket) + control panel page | Voice and UI need a process that owns mic, browser, and model; panel is also the demo surface |
| Playwright MCP over stdio, regex on its text snapshot | Playwright **in-process** + collector script evaluated in the page | Structured data (roles/states/**bboxes**), event subscription, no text-format drift; MCP stays as an eval fallback |
| 2 model round-trips/step, separate completion check | **One** `/v1/systemone` call with fan-out heads (`intent`, `target`, `is_command`, `complete`, `destructive`, `is_correction`, `asr_fix`) | jev-voice-browser answers 9–11 questions per request at 250–350 ms hosted; our measured cost of extra heads is ~zero (architecture.md §3.11: 4 questions @1.2k tok ≈ 0.87 s) |
| Hand-written `PURPOSES` | Purpose compiler (deterministic) + user alias layer | Removes the fixture-only lever; paraphrase ability moves to product config, not page knowledge |
| No values from the model | Span extraction: regex candidates from transcript, d1 picks one | Select-never-generate, needed for voice dictation |
| completion round-trip + `Save changes` special case | Deterministic verifier (re-read target element) + `complete` head + stall detection | Cheaper, kills the hack, catches silent failures |

## 3. Streaming speech-to-text

### 3.1 The trigger model (engine-independent)

The product trick from jev-voice-browser (research doc §2–4, confirmed in its source): **"real time" is not streaming audio into the model — it is cheap-enough re-deciding on every partial transcript.**

Two lanes:

- **Fast lane — partials.** Every partial transcript (debounced ~200 ms, max 2 d1 requests in flight, older ones aborted via `AbortController`) triggers the full decide call. The fan-out heads let d1 act on a sentence that has already committed: `"turn on dark mode and…"`. Terminating words don't wait: partial `"stop"`, `"cancel"`, `"never mind"`, `"confirm"` are matched by a deterministic keyword spotter in the daemon — zero model cost, always available, even mid-action.
- **Slow lane — endpoint.** On final transcript (recognizer endpoint, or VAD silence ≥700 ms, or 15 s max utterance), decide with `complete` forced/bypassed. The silence retry (jev-voice-browser's `_scheduleSilenceRetry`) re-decides once the user goes quiet even if d1 said `complete=0.3` at the last partial.

Gates (adapted from jev-voice-browser's `constants.js` thresholds; recalibrate on our eval set):

| Condition | Action |
|---|---|
| `is_command < 0.5` | Ignore (user talking to someone else) |
| `intent` confidence < 0.55 | Wait for more words |
| `complete < 0.6` **and** not final and not silent ≥700 ms | Wait |
| `target` confidence < 0.45 or top prob < 0.35 | **Numbered overlay** on top-3 candidates; spoken number picks (deterministic) |
| `destructive ≥ 0.5` (unless intent ≥ 0.9 and "confirm" already said) | Speak/toast exact action; require spoken `confirm` |
| `is_correction ≥ 0.6` **and** phrase finished | Undo/redirect last action (uses recent-actions-with-outcomes context) |
| everything green | Execute; highlight target ~300 ms before acting |

Corrections only count once the phrase is finished — a one-word partial `"go"` must not undo the previous action (jev-voice-browser rule).

### 3.2 Engines

| Engine | Partials | Local? | Notes |
|---|---|---|---|
| **Web Speech API** (Chrome `SpeechRecognition`, `continuous=true, interimResults=true`) | Word-by-word, free | **No** — Chrome sends audio to Google | Exactly the jev-voice-browser mechanism; zero code, zero footprint; no hotword biasing; Chrome-only; breaks the "everything local" claim |
| **faster-whisper** `small.en` int8 + Silero VAD (ONNX) + **LocalAgreement-2** (ufal/whisper_streaming policy) | Stable every ~0.5–1 s | Yes | The local-first default. Hotword biasing via `initial_prompt` seeded with visible control names. ~500 MB RAM; measure RTF on the M2 |
| **Moonshine v2** (Tiny/Small, MIT, ONNX, streaming encoder) | Native streaming | Yes | Feb 2026 arXiv: 50 ms (Tiny) / 148 ms (Small) response latency, 5.8×/13× faster than Whisper equivalents. Strong candidate for the fast lane |
| **Parakeet TDT 0.6B v3** via `parakeet-mlx` (streaming, local attention) | Streaming | Yes | Best accuracy-per-size local STT in 2026 comparisons; Apple-Silicon-native |
| d1-omni-600M audio gate | — | Yes | Optional later: 30 s clip for stop/intent barge-in; cannot transcribe (architecture.md §3.8) |

**Recommendation — two stages, one interface:**

1. **`WebSpeechASR` first (day 1).** It is the exact proven pattern, and it makes the *decision* loop (the risky part) testable with voice in hours. Clearly labeled as the non-local stopgap.
2. **`WhisperStreamASR` as the default (the differentiator).** faster-whisper + VAD + LocalAgreement-2 behind an `ASRWorker` interface (`start/stop, on_partial, on_final`). Benchmarked against Web Speech on the fixture vocabulary (WER + partial cadence + perceived latency). **Then** try Moonshine v2 in the same interface — if it holds quality on UI vocabulary, it becomes the fast lane or the whole engine.

Mic capture for the local path: control panel page does `getUserMedia` → AudioWorklet downsample → 16 kHz mono Int16 → WebSocket binary → daemon. This avoids `sounddevice`/native deps entirely and matches where the demo user already is. (Correction to architecture.md §4.2, which assumed Python-side capture for the MVP.)

Memory: `d1:3b` Q8 ≈ 3.7 GB + whisper small int8 ≈ 0.5 GB is fine on 16 GB; on 8 GB use `base.en` and never load a VLM concurrently (architecture.md §3.8).

### 3.3 What the ASR layer adds beyond transcription

- **Contextual biasing** (whisper path): seed `initial_prompt` with the current page's control names, so "Digest frequency" and "Reduce motion" transcribe well.
- **`asr_fix` head**: with n-best enabled, add a choice over 3–5 alternative transcripts in the *same* d1 call, conditioned on visible labels — pick the reading that matches the screen.
- **TTS later** (phase 4): macOS `say` for zero-dep demo, Piper/Kokoro behind an interface; always barge-in-able (any speech above VAD threshold stops playback and cancels reversible in-flight actions).

## 4. Real-time screen parsing → available actions

### 4.1 Why the current parser has to go

`parse_controls()` (`spike.py:129–170`) scrapes the MCP snapshot's *rendered text* with regex. That is lossy and fragile by construction: no geometry (no highlight, no numbered badges, no viewport awareness), no change events (full re-fetch each step), values/labels truncated by MCP's renderer, and any upstream format change silently breaks the pipeline (we already hit one MCP arg drift — demo-plan §11). The 15-candidate cap is positional, not scored.

### 4.2 The new pipeline: collect in the page, decide in the daemon

The proven pattern (jev-voice-browser `snapshot.js`, jev-ultrafast) is an **in-page collector script** + daemon-side compaction. Playwright in-process gives us `page.evaluate`, injected init scripts, and CDP sessions without any subprocess protocol.

**Collector** (self-contained function, `page.evaluate`, ~5–15 ms on normal pages):

- `querySelectorAll` over ~20 interactive selectors (`a[href]`, `button`, `input:not([type=hidden])`, `textarea`, `select`, `summary`, `[role=button|link|tab|switch|checkbox|radio|menuitem|option|combobox|searchbox|textbox]`, `[contenteditable=true]`, `[onclick]`).
- Per element: role (explicit `role` or inferred from tag/type), **accessible name** via `aria-label → innerText → value → placeholder → title → img[alt]`, state (`checked/selected/expanded/disabled/value`), `getBoundingClientRect` + visibility filter (`checkVisibility`, `aria-hidden`, ≥2 px), `inViewport` flag, nearest labelled section (`closest('[role=group],[role=region],fieldset,section,nav,dialog')` → its label/heading), `aria-describedby`/placeholder text (≤80 chars).
- **Stable IDs**: persistent `data-eq-id` attribute (`e01…`), reused across snapshots per element — this is what makes verification and correction cheap.
- **bboxes** kept daemon-side (never sent to d1) for highlight/overlay/scroll-into-view.
- Injection: `context.add_init_script(...)` for launched contexts; CDP `Page.addScriptToEvaluateOnNewDocument` when attaching to a user Chrome over `--remote-debugging-port` (CSP cannot block it).

**AX enrichment fallback**: if the DOM pass yields few *named* candidates (custom widgets, canvas-adjacent UI), pull `Accessibility.getFullAXTree` over a CDP session and merge by `backendDOMNodeId`. This covers the gap that would otherwise force a vision path, and it stays text.

**Compaction / state budget** (deterministic): viewport-first, top-to-bottom; dedupe on `(role, text, href)`; drop nameless non-inputs; caps — start at **60 elements / 60 chars per label / ~4.5k chars (~1.1k tokens)** of element state. Every extra 1k tokens ≈ +0.5–0.7 s on the M2 (architecture.md §3.11), and our own 20-option run measured 1.6–1.8 s. jev-voice-browser caps at 100 elements/24k chars because hosted Jev is 300 ms; *we must be stricter because d1 is 10× slower locally*. Mark below-fold elements `below_fold` instead of dropping them.

**Event model — changes, not frames** (architecture.md §1.1, now actually implemented): a `MutationObserver` + `focus`/`scroll` listeners post a coalesced `dirty` message (`page.expose_binding` → daemon). The daemon does **not** decide on screen changes; it marks the snapshot stale and re-collects lazily before the next decision (also re-collect when stale >1.5 s, as jev-voice-browser does).

### 4.3 Replacing `PURPOSES`: the purpose compiler + alias layer

The spike's accuracy lever was 13 hand-written purposes. Replace with two layers, neither of which is per-page hand-writing:

1. **Deterministic purpose compiler** (pure Python, cached per element key):
   `purpose = f"{verb_for_role} {name.lower()}"` + state (", currently off") + section (", in Appearance") + describedby/placeholder (": display name") — rendered into the contrastive shape the ecosystem converged on:
   `{"what": "toggle Dark mode, currently off, in Appearance", "not_for": "saving or discarding changes", "examples": ["dark mode", "dim the lights"]}` (examples come from layer 2, not from the page).
2. **Product alias layer** (`aliases.json`, user-editable, hot-reloaded — this is *vocabulary of the user*, not knowledge of the page):
   `dim the lights → Dark mode`, `stop the animations → Reduce motion`, `quiet my inbox → Email notifications off`, `easier to read → High contrast`…

   This is the honest fix for paraphrase failures: the spike proved paraphrases work when purposes are rich, but auto-generated page purposes can never invent synonyms. Aliases are product configuration (and map exactly onto the accessibility story: users define their own words).

**Shortlist scorer** for dense pages (>60 candidates): `score = 0.45·lexsim(transcript, name+purpose) + 0.25·role_prior + 0.15·in_viewport + 0.10·focus/recency + 0.05·section_match` (character n-grams, stdlib only, <10 ms). Top 20; always append `none` and `scroll`. Viewport-first when the transcript is empty of matches.

**Values (span extraction)**: for `type`/`select` intents, regex-extract candidate spans from the final transcript (quoted strings, capitalized phrases, select-option names matched fuzzily) and add a `text_span` choice head; d1 picks, code copies verbatim. The model never generates text.

### 4.4 Verification (replaces the completion round-trip and the `Save changes` hack)

1. **Deterministic**: after the action, re-read *only the target element* (`evaluate` on `[data-eq-id=…]`): toggle flipped, value set, tab selected, URL changed, `aria-live` text changed. ~5 ms, no model call.
2. **d1 `complete` head** (already in the same request) as the second opinion.
3. **Stall detection** (jev-ultrafast rule): page-signature hash (url + title + top element texts); 3 consecutive actions with no signature change → stop and ask the user. Never loop blindly.
4. Idempotency: never repeat an identical action on the same target within one utterance.

## 5. UI

### 5.1 Control panel (served by the daemon at `http://127.0.0.1:8766/ui`)

One page, WebSocket-connected, also the **mic source**. Keyboard: hold `Space` = push-to-talk, `Esc` = cancel, click = same actions.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ● Listening    d1:3b warm · last decision 1.31s · [Hold Space to talk]  ⚙│
├───────────────────────────────────┬──────────────────────────────────────┤
│ TRANSCRIPT                        │ ACTION TIMELINE                      │
│ "turn on dark mode and…" ▸partial │ 1 ✓ click "Appearance"      1.30s    │
│ "turn on dark mode and save" ●final│    e12 · p=0.96 · unsure=0.01      │
│                                   │ 2 ✓ click "Dark mode"       1.42s    │
│ GOAL: turn on dark mode and save  │    e04 · p=0.91 · verified (on)      │
│                                   │ 3 … deciding (1.0s)                  │
├───────────────────────────────────┤    [expand → full probability bars]  │
│ SCREEN — what Equivox can see (18)│──────────────────────────────────────│
│ e04 switch "Dark mode" off  [Appearance]                                 │
│ e07 button "Save changes"  unsaved                                       │
│ e09 tab "Notifications"                                                  │
├──────────────────────────────────────────────────────────────────────────┤
│ ⚠ About to click "Discard changes" — say "confirm" or "cancel"  [Confirm]│
├──────────────────────────────────────────────────────────────────────────┤
│ or type: turn on dark mode and save            ASR: webspeech|local ⚙    │
└──────────────────────────────────────────────────────────────────────────┘
```

Panels and behavior:

- **Status rail**: state machine pill (`Idle → Listening → Deciding → Confirming → Executing → Verifying → Speaking`), model warm/cold chip, last-decision latency, PTT button.
- **Transcript**: partials in muted style with `▸`, finals bold with ASR confidence; the utterance that acted gets marked (like jev-voice-browser's `renderTranscript`).
- **Action timeline**: each step = `said → action → target → outcome` (research doc's recommended context shape — this is also exactly what gets fed back to d1 as `recent`, which is what makes "no, the other one" resolvable). Expandable decision record: full probability bars per head, thresholds evaluated, tokens, latency.
- **Screen list**: the compacted candidates, with the chosen target highlighted and overlay candidates badge-numbered — the "trust panel": it shows *why* the system can see what it sees.
- **Confirmation drawer** for yellow/red tier: exact action in words + Confirm/Cancel buttons (not voice-only; accessibility).
- **Settings**: ASR engine (webspeech / whisper-stream), thresholds, aliases editor (the paraphrase vocabulary), scenario runner (`--all` equivalent for regression), fixture reset.
- Typed command box stays (fallback when mic is unavailable — and the spike's REPL behavior preserved).

WebSocket protocol (mirror jev-voice-browser's, proven):

```
client → server: {type: "transcript", text, final, utteranceId}
                 {type: "command", text}          # typed fallback
                 {type: "audio", bytes}           # binary PCM (local ASR path)
                 {type: "undo"} {type: "snapshot"}
server → client: {type: "hello", uiState} {type: "transcript"} {type: "decision"}
                 {type: "action", ui} {type: "snapshot"} {type: "candidates"}
                 {type: "pending", summary} {type: "log"}
```

### 5.2 Page overlay (`overlay.js`, injected via init script, exposes `window.__eq`)

- **Highlight ring** (amber, 3 px) on the chosen target ~300 ms *before* the action fires — the trust moment architecture.md asks for.
- **Numbered badges** (blue circles) on the top-3 candidates when the policy decides to disambiguate; the spoken or typed number selects deterministically. Double duty as a genuine accessibility affordance.
- **Toast** at bottom-center for state ("Dark mode is on", `Say "confirm" to …`, "cancelled").
- Self-contained CSS with max z-index, `pointer-events: none` (it never steals clicks), auto-cleanup timers.

## 6. Repo layout

```
Equivox/
  spike.py  spike.html  scenarios.json     # untouched baseline + regression harness
  docs/
  pyproject.toml                           # uv: fastapi, uvicorn, websockets, playwright,
  aliases.json                             # user vocabulary (dim the lights → Dark mode …)
  equivox/
    __main__.py        # serve | eval
    daemon.py          # FastAPI + WS, static /ui
    orchestrator.py    # state machine, lanes, in-flight mgmt (evolves spike.py run_command)
    decision.py        # d1 client, fan-out question compiler, thresholds (evolves spike.py d1_*)
    surfaces/
      base.py          # SurfaceAdapter protocol: collect() / execute() / subscribe()
      browser.py       # Playwright in-process + collector script + CDP AX enrichment
      mcp.py           # legacy spike adapter, kept for eval A/B
    perception.py      # compaction, dedupe, caps, dirty-flag handling
    purposes.py        # purpose compiler + alias layer
    shortlist.py       # scoring, caps, scroll/none
    asr/
      base.py          # ASRWorker protocol
      webspeech.py     # (no server code needed — transcript arrives over WS)
      whisper_stream.py# faster-whisper + Silero VAD + LocalAgreement-2
    policy.py          # gates, risk tiers, keyword spotter (stop/cancel/confirm)
    verify.py          # deterministic re-read + stall detection
    overlay_src.js     # compiled/served to the page
  ui/index.html        # control panel
```

`spike.py` stays runnable forever as the validated baseline; the new eval mode reuses `scenarios.json` plus new voice-focused scenarios (multi-step, corrections, out-of-scope, ambiguity → numbered pick).

## 7. Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **1. Voice on the fixture** (~2–3 days) | Daemon + WS + control panel; `WebSpeechASR` partials → decide loop; fan-out heads + thresholds; overlay highlight + toast; keyword barge-in; typed fallback; timeline UI | Hands-free demo of demo-plan §8 script; 10/10 fixture scenarios via voice; barge-in "stop" works mid-action; median spoken-command→action ≤2.5 s (cloud STT) |
| **2. Local ears** (~2–3 days) | `WhisperStreamASR` (faster-whisper small.en int8 + VAD + LocalAgreement-2), hotword biasing from snapshot labels, `asr_fix` head; bench vs Web Speech (WER, partial cadence, latency) | Fully offline demo; perceived p50 ≤3 s; partial cadence ≤1 s; WER on fixture vocabulary <10% |
| **3. Structured perception + one real page** (~3–4 days) | In-page collector replaces MCP-regex (MCP kept for eval); purpose compiler + aliases; shortlist scorer; span extraction; deterministic verifier + stall detection; numbered disambiguation | Fixture parity 10/10 on the new surface; **one** allowlisted real-page task end-to-end with confirmation; zero regex parsing of snapshot text in the decide path |
| **4. Speak back + safety** (~2 days) | TTS (macOS `say` → Piper behind interface) with barge-in; risk tiers (green/yellow/red), red-tier spoken-confirm flow; redaction of sensitive values; audit log fields (thresholds, outcomes) | Red-tier actions 100% blocked without explicit "confirm"; TTS barge-in works; log sufficient to replay a session |
| **Later** (tracked in architecture.md §5.2) | Chrome extension attach, macOS AX adapter, skill replay, Jev head-to-head, MLX d1 measurement | — |

Sequencing rationale: voice (1) and perception (3) are independent — if the collector hits trouble, voice still ships on the MCP surface; if voice stalls, the perception work still improves the typed demo. Phase 2 is a swap behind an interface, not a rewrite.

## 8. Corrections to `docs/architecture.md`

Written one day before this plan; mostly still accurate (d1 facts verified against current Ollama/Liquid docs: 3B on LFM2.5-VL-3B, single forward pass, images-as-tokens on the hosted API — our local M2 numbers stand). Four updates:

1. **Mic capture**: browser-side capture (Web Speech API or `getUserMedia`→WS), not `sounddevice` in the daemon, for the MVP. Matches the proven ecosystem pattern and removes native audio deps.
2. **ASR engine table**: add **Moonshine v2** (measured 50–148 ms streaming encoder latency, MIT, ONNX) and **Parakeet TDT 0.6B v3** (best accuracy/size on Apple Silicon, MLX streaming) as 2026 current options; faster-whisper stays the conservative default, d1-omni stays optional.
3. **First surface**: "Chrome MV3 extension + daemon" should read "Playwright-in-process daemon" for phases 1–3; the extension only earns its complexity when attaching to the user's real session (phase: later). Both the spike and jev-voice-browser validate the Playwright path.
4. **Vision**: confirmed demoted — jev-ultrafast ships "no screenshots in the default agent loop"; our AX-enrichment fallback (§4.2) further postpones it.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Web Speech API is cloud-dependent (breaks local-first claim; Chrome-only) | Explicitly a phase-1 stopgap; phase 2 makes local ASR the default; typed fallback always present |
| whisper partials slower/less stable than Web Speech word-streams | LocalAgreement-2 + fast lane only needs *committed* words; measure before switching thresholds |
| Memory contention d1 + whisper on 8 GB | base.en, sequential (ASR idles during d1), never concurrent VLM |
| d1 latency grows with fan-out heads | Heads don't multiply latency (measured); keep element state ≤1.1k tokens; cap 20 |
| Collector misses custom widgets / shadow DOM | CDP AX-tree enrichment; if still thin → MCP adapter or a `scroll`/`none` ask; vision stays out |
| Thresholds tuned on hosted Jev don't transfer to d1 | Recalibrate on our eval set; log every gate evaluation; thresholds are config, not code |
| Prompt injection via real pages | Screen text is data (never adds options); destructive gate + spoken confirm; injection tests in eval (architecture.md §6.3) |
| Numbered overlays fight page CSS/CSP | High z-index + `pointer-events:none`; CDP `addScriptToEvaluateOnNewDocument` bypasses page CSP |

Housekeeping noticed during exploration: `.env` holds a real-looking `OPENCODE_API_KEY` (rotate and move to a secrets manager); `LICENSE` is a 0-byte file; `spike-log.jsonl` is truncated on every run (`spike.py:710`) — keep history when the log becomes the audit trail.
