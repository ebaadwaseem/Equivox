# PLAN-v2.md: Hands-free web control, real-time natural language

*Working title. Preliminary, 2026-11-07. Hackathon: 64 h, theme "Health and accessibility".*

*This revises the earlier `PLAN.md` draft in this folder. Main changes: inference goes through Ollama's `/v1/systemone` (not the transformers route); a 26-option cap on `choice` questions means a target shortlist; STT options now include WhisperKit and on-device contextual biasing; GPU/thermal contention on the M2 Air is treated as a design constraint.*

*Revision (2026-11-07): the **default decision model is hosted Jev (`jev-1.13.0`) reached through OpenRouter**, authenticated with `OPENROUTER_API_KEY` in `.env`; the local d1/Ollama endpoint becomes the **backup** (hosted outage, dead Wi-Fi, offline/private mode). The goal is one convincing real-time natural-language demo: search, scroll, going to key webpages, forward/back to the start.*

## 1. Pitch and success criteria

Speak naturally ("open the second result", "scroll down, a bit more") and the page responds **mid-sentence**. Audio stays on the laptop (on-device STT); the decision model runs hosted by default (Jev via OpenRouter) with a local d1 fallback when the network is gone. For people with motor impairments, RSI, tremor or limited dexterity, this is hands-free browsing.

**Demo scope — one continuous hands-free run:** search for a topic, open a result, scroll ("a bit more"), visit 2–3 key webpages (e.g. Wikipedia, one docs page), then "go back", "back to the start", "forward" — with previews firing before sentences end throughout.

Demo succeeds if:

1. Mid-sentence behavior is visible: a predicted target is highlighted while speaking, and the action commits before the sentence ends.
2. A fixture eval (section 9) gives us numbers to show.
3. Nothing irreversible fires by mistake. Destructive actions need a spoken confirm.
4. A typed-command fallback keeps the demo alive if the mic or STT misbehaves.
5. The full demo scope above works in one take without touching keyboard or mouse, including if OpenRouter drops mid-demo (seamless local failover).

## 2. Facts the design rests on (re-verify in spikes)

- **d1-3B is a decision model, not a generator.** Typed questions (`choice`, `score`, `noul` = yes/no) answered in one forward pass with probabilities. Liquid reports ~1.3x the time for 3 questions vs. 1, so many small questions per call are cheap.
- **Local serving:** Ollama `POST /v1/systemone` (≥ 0.35, newer recommended; `d1:3b` is Q8_0, ~3.7 GB, 32K context, text + image). The llama.cpp server exposes the same endpoint. Code against the protocol so the backend is swappable. MLX: no d1-specific path found; the documented fallback is transformers `model.system_one()` on MPS.
- **Limits to check for d1:** 64 KiB per request; `choice` questions capped at about 26 options in Ollama's docs. Plan for a shortlist of ≤ 24 target candidates.
- **Reference:** `moritzkremb/jev-voice-browser` (MIT): one request per partial transcript, ~11 typed questions, code-owned policy, code extracts text and URLs, fixture-based tests. We borrow the pattern and credit it. We now share its hosted-Jev decision backend by default; we differ with local STT, accessibility features, speculative preview, vision fallback, and a local d1 fallback path.
- **Uncertainty is a feature.** Low `target` confidence triggers numbered-overlay disambiguation, the same pattern as Apple Voice Control and Windows Voice Access.

## 3. Decisions

| Decision | Choice | Revisit if |
|---|---|---|
| Scope | Web only | Never, within 64 h |
| Browser control | Playwright/Puppeteer **attached to the user's Chrome via CDP**; observer and overlay as injected JS so they port to an extension content script | Time left after M4 |
| Page parsing | Compact DOM/AX element list with text-ified geometry; vision only as on-demand fallback | Fixture A/B shows image helps broadly |
| Prompting | Factorized typed questions (`intent`, `target`, gates); never action x element | Never |
| STT (primary) | Chrome on-device `SpeechRecognition` (`processLocally`) | Interim latency/stability is poor, then WhisperKit, Moonshine or Parakeet |
| Inference (default) | **Jev `jev-1.13.0` via OpenRouter**, `OPENROUTER_API_KEY` from `.env`, same typed-question request shape (~$0.0002/decision) | Latency can't reach budget, network unreliable at demo time, or offline demo needed → local d1 |
| Inference (backup) | Ollama `/v1/systemone` (`d1:3b`), kept warm; auto-failover after 2 hosted timeouts | Hosted works reliably again |
| Free text | Model never generates; code extracts spans, d1 picks one; dictation mode bypasses d1 | Stretch: small generative model for open-ended requests |

## 4. Architecture

```
 Chrome control page (real Chrome, mic)        Local server (Node or Python)                    Controlled Chrome (CDP)
 ──────────────────────────────────────        ─────────────────────────────                    ───────────────────────
 SpeechRecognition (on-device)                 ws: partial/final transcripts
   interim + final ───────────────────────────▶ scheduler: debounce ~150 ms, 1 in flight,
 live transcript, confidence bars,               latest wins, drop stale                        observer.js (injected)
 gate table, typed-input box   ◀───────────────  │                                                 snapshot on mutation/scroll/nav
                                                 ▼                                                 stable ids e01..eNN
                                               state builder  ◀──────── snapshot cache ◀────────── viewport-first, geometry as text
                                                 │  page block (stable prefix) + transcript + recent actions
                                                 ▼
                                               decision client ──▶ Jev via OpenRouter (default)
                                                 │  failover ──▶ Ollama /v1/systemone (d1:3b)
                                                 │  typed answers + probabilities
                                                 ▼
                                               policy (pure code, unit-testable)
                                                 │  PREVIEW | COMMIT | CONFIRM | DISAMBIGUATE | WAIT | IGNORE
                                                 ▼
                                               executor ─────────────────────────────────────▶  overlay (highlight, toast, numbers)
                                                 │  Playwright actions; code builds URLs/text   click/type/scroll/nav/tabs
                                                 ▼
                                               action log (feeds `recent_actions` + undo)
```

**Speech.** Chrome on-device recognition via `available()` and `install()`. Spike contextual biasing (`recognition.phrases`) with command vocabulary and visible link names; it's experimental. Fallbacks: WhisperKit (Neural Engine, leaves the GPU for d1), Moonshine streaming, Parakeet-MLX. Plain Whisper only for finals and dictation, since it re-decodes windows and revises partials heavily. Keep one interface, `partial(text, words[], is_final)`, so engines swap.

**Observer.** Collects interactive elements (links, buttons, inputs, `role=`, `cursor:pointer` divs). Each gets `id role "name"(≤60 chars) host?` plus **text-ified geometry**: landmark region, ordinal within group ("3rd result"), coarse position. Viewport first. Re-snapshot only on mutation, scroll or navigation (debounced), never per partial. Known gaps: iframes, shadow DOM, canvas.

**Candidate selection.** Full list in `state` (watch the 64 KiB cap). The `target` question gets a shortlist of ≤ 24: viewport elements plus top lexical/fuzzy matches against the transcript from the **whole** page. The union avoids losing the true target to a misheard word.

**Decision client.** One request per scheduler tick. Default backend is Jev via OpenRouter (key from `.env`); the local Ollama d1 endpoint is a drop-in backup speaking the same protocol, selected on hosted timeout/error or when offline, so the demo survives dead conference Wi-Fi. State order: stable page block first, volatile transcript last, in case the backend reuses the KV prefix (spike 2).

**Policy.** Pure functions from answers to a decision. All thresholds in one file.

**Executor.** Code builds URLs, search templates and typed text. Every action is logged with its outcome so "no, the other one" and "undo" work.

## 5. Question set

Per-partial (4 questions, to keep calls fast):

| id | type | purpose |
|---|---|---|
| `is_command` | noul | Is the user addressing the browser? (ignores side conversation) |
| `intent` | choice | click, type, dictate, scroll_up/down, back/forward, reload, tab ops, navigate, search, confirm, cancel, complex_request, **none**. Each option: what / not for / examples |
| `target` | choice | shortlist ids + **none** |
| `complete` | noul | Has the user finished the command? |

Commit-time only (once the preview is firm): `destructive` (noul), `scroll_amount` (score), `text_span` and `url_span` (choice over code-extracted candidates), `is_correction` (noul, only when history exists), `tab_direction`, `site`.

State also carries page url/title, the previous page, and the last ~3 executed actions with outcomes; that is what makes "the other one" and "go back" resolvable.

Image (384px, optionally with numbered marks) is added **only** when target confidence is low or top candidates have empty names. Liquid reports no vision decision benchmark, so treat it as an experiment (spike 5).

## 6. Policy: anticipation tiers

Signals per tick: `is_command`; `intent` and `target` confidence and margin; `complete`; stability (same top-1 for 2–3 consecutive partials, which absorbs ASR revisions); action reversibility.

| Tier | When | What happens |
|---|---|---|
| **T0 Preview** | `is_command ≥ 0.5`, intent/target above low thresholds | Highlight predicted target, no side effects. This is the "it knew before I finished" effect |
| **T1 Reversible commit** | stable, `complete ≥ 0.6` (or short silence); scroll/history/tab/focus, or click on a non-destructive target | Execute on partial; log for undo. Short wait for modifiers ("a bit") |
| **T2 Gated** | free text, submit/buy/delete/send, `destructive ≥ 0.5` | Wait for final or ~600 ms silence; confirm required for destructive |
| **Disambiguate** | target confidence low | Numbered overlays on top 2–3; user says "two" (no model call) |
| **Correction** | `is_correction ≥ 0.6` | Reverse last action, or exclude the previous target and retry |

Starting thresholds from Jev's README — usable as-is for hosted Jev (our default backend); **recalibrate** before running on local d1: `is_command ≥ 0.5`, intent confidence ≥ 0.55, `complete ≥ 0.6`, target confidence ≥ 0.45 with top ≥ 0.35, `destructive ≥ 0.5`. Never early-fire irreversible actions.

## 7. Latency budget (target ≤ ~350 ms from word to preview)

| Stage | Budget | Notes |
|---|---|---|
| STT interim | unknown | Spike 4 |
| Debounce | 150 ms | Tune |
| Model call | **≤ 300 ms** | Hosted Jev via OpenRouter: expect ~250–350 ms round trip from our network (verify in spike 1). Local d1 ~500 ms–1.8 s at ~1k tokens is the *backup-mode* gap |
| Policy + overlay | < 30 ms | Local |

Levers for the backup-mode gap (local d1): fewer per-partial questions, smaller state (shortlist, short names), prefix-cache reuse if available, verify Q8 runs on Metal, try the llama.cpp server. In hosted mode the levers are state size and per-partial question count only. Liquid's figures (company-reported): 640 ms for a 3.4K-token state on an M5 Pro, so 500 ms at 1k tokens suggests headroom.

**M2 Air constraints (16 GB, fanless):** call the model only when a new partial arrived; one request in flight with latest-wins; keep STT off the GPU where possible (Chrome on-device or WhisperKit/ANE); benchmark under sustained load, not cold. Hosted Jev moves decision compute off the laptop entirely — a thermal and memory win; in backup mode watch total unified-memory use across d1, STT and Chrome.

## 8. Accessibility features (the differentiators)

- **Timing tolerance:** model-judged `complete` plus a user-configurable patience setting, so slow or halting speakers aren't cut off by a fixed silence timeout.
- **Visible feedback:** live transcript, predicted-target highlight, confidence bars, toast of the executed action.
- **Audio feedback** for low-vision users (browser TTS of element names and results; code-generated text).
- **Always-on or push-to-talk:** `is_command` gates side conversation; hotkey/switch as alternative.
- **Dictation mode:** once a field is focused, STT finals go straight into it; d1 only watches for control phrases ("stop dictating", "delete that", "press enter").
- **Undo and correction** as first-class commands.
- **Privacy:** audio stays on-device; in default (hosted) mode, page text and transcripts go to the model API — say so plainly in the demo. Backup mode is fully local (model included) for offline or privacy-sensitive runs. Position as an accessibility aid and prototype; no medical claims.

**Safety.** Destructive actions need a spoken confirm (a convenience, not a guarantee). Use a dedicated browser profile and warn against logging into sensitive accounts during demos. Control server on localhost only.

## 9. Eval harness (build early)

Capture page snapshots from 6–10 real pages (search results, article, form, dashboard, video site) as fixtures, paired with utterances (full and prefix-truncated, replayed word by word) and the expected action or expected `wait`. Metrics:

- Action accuracy on finals and the **wrong-action rate** (the safety number)
- **Lead time:** ms between preview/action firing and the end of the utterance (the "snappiness" number)
- **False-early-fire rate:** early actions that differ from the final-transcript decision
- Preview-correct rate at T0
- Word-end to decision latency, p50/p95, including STT
- Ablations: image fallback on/off, shortlist size 12 vs 24, lexical prefilter on/off

Use it to tune thresholds and decide the image fallback instead of guessing. Jev's repo has a 34-case version to crib structure from.

## 10. First-4-hour spikes (each yields a number)

1. Model latency vs state tokens (250/500/1000/2000) and question count (1/4/8): **hosted Jev via OpenRouter vs local d1**; confirm the OpenRouter request/response shape for the typed-question protocol and the failover switching time.
2. Does Ollama reuse prefix cache when only the transcript changes? Does a client abort stop work mid-prefill?
3. Max `choice` options on d1; accuracy at 12 vs 24 candidates.
4. Chrome on-device STT: do `available()`/`install()` work; word-end-to-interim latency and revision rate on 10 sentences. Compare Moonshine or WhisperKit if weak.
5. Image on/off on 10 hard cases (icon-only buttons, "the blue one").

## 11. Schedule (64 h, with buffer)

| Hours | Milestone |
|---|---|
| 0–4 | Spikes 1–5; pick backend and STT |
| 4–12 | **M1:** typed command → snapshot → model (OpenRouter Jev, local backup) → action on a real site; fixture capture starts |
| 12–24 | **M2:** streaming STT, scheduler, policy tiers, preview overlay, disambiguation |
| 24–34 | **M3:** dictation mode, undo/correction, confirm, audio feedback, patience setting |
| 34–44 | **M4:** eval numbers, threshold tuning, image-fallback decision |
| 44–54 | Robustness on the demo route: search engine, Wikipedia, 1–2 key pages (search/scroll/back-forward rehearsal); extension port only if M4 is solid |
| 54–60 | Feature freeze; demo script; recorded backup video |
| 60–64 | Write-up, slides, buffer |

If a milestone slips more than 4 h, cut from the list below rather than shrinking the buffer.

## 12. Cut list (in order) and stretch

**Cut:** 1. Chrome extension port. 2. System 2 generative model. 3. Image fallback. 4. Audio feedback. 5. Multilingual. **Never cut:** eval harness, typed fallback, destructive confirm.
**Stretch:** contextual biasing with page vocabulary; vision-grounded numbered marks; small generative model routed by `intent=complex_request`.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Model call too slow for per-partial use | Spikes 1–2; hosted Jev is the fast path; on local d1 use fewer questions and commit-on-pause for non-reversible actions |
| OpenRouter unreachable, rate-limited, or key invalid at demo time | Local d1 auto-failover (tested in spike 1), offline fixtures, typed fallback, recorded video |
| STT partials unstable, late, or unavailable | Swap engine; hysteresis (2–3 agreeing partials); measure before building on it |
| 26-option cap | Shortlist; two-stage target pass at commit time |
| d1 on UI grounding and ASR-garbled text (no public benchmark) | Measure on fixtures |
| Modifier words after a fired closed-set command ("scroll down" / "a bit") | Short modifier window in T1 |
| Real sites: iframes, shadow DOM, canvas, cookie banners, div-buttons, bot protection | Pick demo sites early; handle only what the demo needs |
| Wrong action on mishearing | Reversibility tiers, confirm for destructive, undo |
| Demo-day flakiness | Typed fallback, recorded video, offline fixtures, hosted→local failover |
| Licensing and rules | Check TypeSafe/OpenRouter terms of use, the d1 model-card license (LFM 1.0) for backup mode, and hackathon rules on pre-existing code; credit Jev (MIT) |

## 14. Repo layout (proposed)

```
src/stt/          Chrome on-device client (+ adapters for WhisperKit/Moonshine/Parakeet)
src/observer/     injected snapshot + overlay JS
src/decision/     systemone client (OpenRouter Jev default, Ollama d1 backup + failover), state builder, question definitions
src/policy/       tiers, thresholds, hysteresis (pure, unit-tested)
src/executor/     Playwright actions, undo log
src/server/       ws + scheduler (latest-wins, drop stale)
src/ui/           control page (transcript, bars, typed input)
eval/             fixtures, utterances, runner, report
PLAN.md           earlier draft
PLAN-v2.md        this file
```

## 15. References

- Liquid AI, "Multimodal open d1 decision models for the edge" (Hugging Face blog) and model card `LiquidAI/d1-3b`.
- Ollama docs: Decision models and `/v1/systemone` API; llama.cpp server decision-model support.
- `moritzkremb/jev-voice-browser` README.
- Web Speech API on-device recognition (`processLocally`, `available()`, `install()`); Chrome 139.