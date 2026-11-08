# Incremental voice control: implementation and remaining work

Updated 2026-11-08. This replaces the earlier plan, whose debounce, cancellation,
final-transcript consumption, and silence rules could lose or misexecute commands.

## What was wrong

Comparison baseline: `origin/dev`. Its scheduler cancelled the preceding handler on
*every* update, including a handler that could already be executing an action. The
initial improvement branch separated actions, but still restarted a 200 ms debounce
on every partial and cancelled the oldest decision when both slots filled. Continuous
speech could starve dispatch or cancel useful inference just before completion.

Additional findings in the initial improvement branch:

- `_extract_text_for_intent` accepted speculative `text_span` before checking intent.
  An independently selected search payload could therefore become a new-tab URL.
- `new_tab()` created a page, then awaited navigation before returning it. Failed
  navigation left an orphan tab while `active_page` still referred to the old page.
- Prefix consumption only split some partials at a comma. A final containing several
  commands could consume everything after performing just the first action. There was
  no automatic replay if no further recognizer update arrived.
- A sliced virtual transcript was buffered as though it were a physical transcript,
  losing the identity required to consume successive clauses correctly.
- One buffer slot could overwrite a separate completed command during navigation.
- Decisions could outlive their page/action reservation; stale detection alone did
  not prevent an old-page target being sent to a newly active tab.
- The silence retry labelled itself `900 ms` regardless of the actual elapsed time.
  Both Swift endpointing and Python retries confused stalled recognition with silence.
- `act_now`'s winning label could trigger an action without a readiness-probability gate.
- Six committed actions were the model's entire conversation history. The overlay kept
  only its current status and visually clipped speech to two lines; these were separate
  limitations, not evidence that Jev maintained a hidden conversation.

## Implemented loop

1. Input receives a stable physical utterance ID, including typed commands. The UI
   immediately echoes the physical transcript. Audio activity is tracked separately
   from text changes.
2. The scheduler coalesces partials in a leading 80 ms window. It defaults to one
   decision in flight and preserves the newest update per utterance. Useful calls
   finish; new partials cannot perpetually restart the timer. Finals skip the window.
3. Executed words are removed using per-utterance consumed prefixes. Matching tolerates
   case, whitespace, and punctuation changes, but not changed words.
4. A quote-aware splitter isolates the first explicit clause, including unpunctuated
   `then search …` or `okay now scroll …`. Commas inside a query are preserved unless
   followed by a recognizable new command. This is a conservative grammar, not a planner.
5. A fresh page snapshot supplies up to 40 ranked candidates, options, focused fields,
   visible headings/status, page identity, recent action/speech pairs, and open-tab URLs.
   Element descriptions live once in state; target choices reference IDs.
6. Jev answers independent typed questions in one request. The instruction explicitly
   distinguishes the current clause from historical speech, and permits a newly spoken
   repeat command. Text/URL answers are routed by intent and checked against generated
   candidates. Search text cannot populate a navigation URL.
7. Policy requires semantic completeness, sufficient readiness probability, an actual
   payload/target, and the existing risk/ambiguity gates. A known next command bounds
   a search/type payload so it can execute before recognizer finalization. Otherwise
   free text waits for final or 600 ms of actual quiet. A semantically incomplete
   command remains incomplete after a pause; silence does not invent missing arguments.
8. Actions reserve a page epoch before entering a serial worker. Old-page decisions or
   already-consumed clauses cannot commit. Each completed action replays the latest
   physical transcript, so a single final command chain drains without further input.
   Separate utterances received during navigation are retained in order.
9. Quiet retries measure elapsed quiet and refresh observations. When text, page,
   context, candidates and confirmation state are unchanged, an existing Jev decision
   can be re-evaluated locally instead of making another identical network call.

Example: `open a new tab then search for physiotherapy clinics in edmonton okay now
scroll down` becomes three decisions. The first can commit during speech; the search
is decided on the blank tab; scroll is decided after the search's DOMContentLoaded.
Navigation no longer waits for every resource's `load` event.

## Context and interface

The model receives twelve recent committed speech/action pairs, in chronological
order, with target labels, payloads and updated success/failure outcomes, plus the
page before navigation. This is explicit bounded memory sent with each request.
Longer history is retained in memory for the UI (up to 100 actions), independently
of the model window. It survives navigation/tab changes during the process, not app
restarts. Text fields are bounded when encoded; unlimited dictation is not supported.

Vox has a wider, scrollable transcript area and expandable speech/action history.
A subtle background/border marks rows included in decision context; older rows remain
available. Overlay text spacing, remounting and page-reference lifetime were corrected.
The overlay remains excluded from perception, and the browser tests cover click-through.

## Perception and executor

- Extract controls inside open shadow roots and attached frames; qualify frame target
  IDs and use matching frame locators for execution.
- Recognize contenteditable/textbox/searchbox and ARIA combobox/listbox/option roles.
  Preserve labels, focused state and more native option labels; redact password values.
- Fill native inputs, textareas, contenteditable editors and input-wrapper widgets.
- Match native select labels/values case-insensitively, then allow unique substring
  matches. Ambiguous options cause no selection. ARIA option selection is supported
  through an exact option label after opening the control.
- Validate HTTP(S) URLs before creating/navigating tabs. Close a newly created tab if
  its requested navigation fails, preserving active-page ownership.
- `VB_VISION=1` optionally attaches a JPEG thumbnail, at most 768×512, to the same
  typed decision request. Vox is hidden in that screenshot. This requires a compatible
  multimodal decision endpoint/model and adds capture/upload latency; default is off.
  Request shape and thumbnail size are tested; live vision accuracy is uncalibrated.

## Research comparison

[Moritz Kremb's browser implementation](https://github.com/moritzkremb/jev-voice-browser)
uses one request on partial speech, code-generated spans, a compact page snapshot,
explicit action history, and a separate execution policy. Its README reports roughly
300 ms decisions and documents one-action-per-utterance and snapshot limitations.
It is useful prior art, not a complete solution to arbitrary multi-command speech.

[TypeSafe's model documentation](https://docs.typesafe.ai/introduction) confirms that
questions are independent and evaluated in parallel. Adding a separate network call
for each gate defeats that advantage. [Confidence guidance](https://docs.typesafe.ai/confidence)
also distinguishes a choice's concentration statistic from the probability of its
selected option; readiness here uses summed probability of the executable categories.

[The Jev CUA project](https://github.com/edgeteamio/jev-cua) reports warm decision latency
around 178 ms p50 and 250 ms p95 and tests word-by-word prefixes and premature actions.
The [voice decision benchmark](https://github.com/GaNotchVFX/jev-benchmarks/tree/main/02-voice-decision-layer)
reports approximately 149 ms p50 with described options in its particular setup. These
are author-reported measurements under different workloads, not end-to-end voice
latencies for Equivox. The specific faster Twitter clip could not be identified confidently.

Images are supported in decision-model APIs, but the wire format is provider-specific:
see [TypeSafe images](https://docs.typesafe.ai/images) and
[OpenRouter's examples](https://openrouter.ai/blog/insights/what-is-jev/).
Equivox currently uses OpenRouter's state content parts, not TypeSafe's assets map.

## Verification and measurement

`python -m scripts.benchmark_decisions --live` runs seven synthetic decisions without
browser actions. The final prompt probe on 2026-11-08 produced the expected five
executable decisions and waited on both incomplete fragments. Decision p50 was
187 ms, range 177–406 ms, including the first connection. Seven cases do not establish
accuracy or p95. An earlier probe found that wording about ignoring historical actions
suppressed explicit repeats; the final wording and regression probes cover that case.

Regression tests set offline defaults, independently of local API keys.
Logs separate snapshot, model-decision and action durations. Browser tests cover blank
new-tab search with URL encoding, frames, shadow editors, native dropdowns, thumbnail
size, and history across navigation. Loop tests cover complete final chains, early
new-tab execution, loading before scrolling, multiple buffered utterances, punctuation
revisions, continuous input without decision starvation, and audible speech during
stalled recognition. Swift listener compilation is checked independently of mic access.

## Next improvements, in order

1. Record a replay corpus from actual ASR partials with expected earliest safe commit
   boundaries. Measure premature-commit rate, duplicate/lost actions, and p50/p95 from
   partial arrival to action start. Include corrections, hesitations and proper names.
   A seven-command synthetic probe is insufficient for threshold calibration.
2. Evaluate the microphone energy thresholds on the user's devices/noise. The new
   energy guard prevents a common false endpoint but is not a trained VAD. A streaming
   VAD with speech timestamps and ASR stability scores would give better payload gates.
3. Benchmark direct TypeSafe versus OpenRouter with identical pinned models, state,
   questions and warmed connections. Do not assume changing gateways is faster.
4. Cache page observations by actual DOM/viewport/focus revisions, excluding assistant
   mutations. A background snapshot can overlap recognition; arbitrary TTL caching
   risks grounding a click on an old page. Current snapshots remain fresh per decision.
5. Add explicit per-frame geometry and an on-demand accessibility/vision fallback for
   ambiguous controls. Test the optional image path on a held-out icon/spatial corpus
   before enabling it by default. Closed shadow roots/canvas need another perception path.
6. Grow the clause grammar from replay failures. Dictation that literally contains
   unquoted command connectors remains ambiguous. Do not precompute a long plan against
   the old DOM. Keep each grounded action conditional on the previous action's outcome.
7. Improve custom/virtualized dropdown scoping and rich editors that require keystrokes
   or application-specific events. Current ARIA fallback needs one unambiguous visible
   exact-label option; it does not infer arbitrary widget protocols.
8. Introduce a bounded semantic session summary only if twelve actions are insufficient.
   Keep exact recent payloads and action outcomes alongside it, and show the summary's
   inclusion in the UI. Context is supplied state, not persistent memory inside Jev.
