# Refactor plan: low-latency decisions, context, and multi-action chains

## Goal

- One Jev request per partial; **200 ms debounce**; act before the recognizer's final when the
  model says the current clause is executable.
- A rolling, interleaved history of committed turns passed as model state.
- Chained clauses via **prefix consumption**: executed clauses move to history, the remainder is
  evaluated as the next command against the new page.
- Fix text/URL span extraction (no more `"and search for …"` payloads).

## Non-goals

- No free-form generation. Jev picks among code-generated candidates only.
- No full up-front planner. Chains are sequential and reactive.
- No threshold retuning of the keyword fallback (it stays as fast path / no-key mode).

## 1. Context model — new `backend/context.py`

```python
@dataclass
class Turn:
    said: str            # transcript slice that was executed
    intent: str | None
    target: str | None   # element id / label
    text: str | None     # query / typed text / url
    outcome: str         # "running" -> "navigated to …" / "done" / "failed"
    ts: float

class ConversationContext:
    turns: deque[Turn]           # maxlen=6, oldest -> newest
    previous_page: dict | None   # set on navigation only
    active_prefix: str | None    # text already consumed from the current utterance
```

Rules:

- **Only committed turns** (actions and explicit corrections/undos). Ignored chatter is not stored.
- Append a turn when the action is **committed**, `outcome="running"`; update it after settle.
- **One interleaved list**, not separate transcript/action arrays.
- `active_prefix` is lowercased; a later transcript is sliced only if it still starts with it
  (recognizer revisions that rewrite the prefix are ignored).
- Drop the oldest turn beyond 6.

## 2. Decision request — `backend/model.py`, `backend/state_builder.py`

Merge the two Jev calls into **one**. Always ask:

| id | type | notes |
|---|---|---|
| `act_now` | choice | `not_a_command · incomplete · clause_complete · utterance_complete` |
| `intent` | choice | existing list |
| `target` | choice | shortlist + `none` |
| `destructive` | noul | always; needed when acting on partials |
| `scroll_amount` | score | little / page / end |
| `tab_direction` | choice | next / previous / first / none |
| `text_span` | choice | code-generated spans + `none`, only when candidates exist |
| `url_span` | choice | code-generated spans + `none`, only when candidates exist |
| `is_correction` | noul | only when `context.turns` is non-empty |

State layout, stable prefix first (prompt cache):

```
PAGE: url / title / site
ELEMENTS: one compact line per shortlisted element
CONTEXT: previous_page + turns (oldest -> newest, ~5 fields each)
PENDING_CONFIRMATION: summary or absent
TRANSCRIPT: current text (<= 400 chars)
```

Delete `about_to_commit` and Jev call #2.

## 3. Scheduler — `backend/scheduler.py`, `main.py`

- Replace cancel-everything with **single-flight + coalesce**: at most 2 in flight, abort only the
  oldest; keep the newest partial as `pending` and fire it as soon as a slot frees.
- A stale response may act if it already commits to a closed-set intent; never for free text.
- **Actions are never cancelled.** Separate `decision_task` from an action worker with a
  size-1 slot + latest pending decision.
- While an action runs, buffer new transcripts; re-evaluate with a fresh snapshot when it settles.
- Keep a 900 ms silence timer that re-evaluates with `silent_ms` (bypasses `act_now`).
- Free-text retry: `max(50, 600 - silent_ms)`.

## 4. Policy — `backend/policy.py`

```
ignore  : is_command < 0.50
wait    : intent none/conf < 0.55, or act_now == incomplete (and silent_ms < 900, not final)
confirm : destructive >= 0.50
disambiguate : target conf/top-prob below threshold (use probabilities, not top-2 gap)
act     : act_now in {clause_complete, utterance_complete}
          or final
          or silent_ms >= 900
free text additionally: final or silent_ms >= 600, OR the clause ends at a comma and
          act_now == clause_complete (bounded payload rule)
```

Return `retry_in_ms` from `wait` so `main.py` can schedule the silence retry.

## 5. Chains — `main.py` + `backend/context.py`

- On `act`: `context.commit(slice, decision, action)`; set `active_prefix`.
- Following transcripts that start with `active_prefix`:
  - slice the remainder;
  - ignore if `active_prefix` no longer matches (recognizer rewrote executed words);
  - ignore if the remainder has fewer than 2 words;
  - otherwise this is the **current** transcript, with a synthetic id `<physical>+<gen>`.
- Evaluate the remainder only after the previous action settles; then refresh the snapshot.
- For `"search X, now scroll down"`: the comma makes `search X` `clause_complete`; commit and start
  loading; `now scroll down` arrives during load, is buffered, then decided on the results page.

## 6. Span candidates — `backend/spans.py`

- `extract_text_candidates(transcript) -> list[str]` (max 8, ordered):
  1. quoted spans;
  2. tails after **any** payload verb (`search for|look up|google|find|type|enter|write|fill`),
     not anchored at `^`; strip trailing destinations (`into the search box`);
  3. comma/semicolon-bounded tails after the verb (the chain case);
  4. tail after the first `" for "`;
  5. nothing else — **never the whole transcript**.
- Strip leading `and / now / then / also / please` and trailing punctuation.
- `extract_url_candidates` mirrors this for domain-like spans.
- `_extract_text_for_intent` returns `None` on no match; callers `wait` instead of fabricating.
- Jev picks `text_span` / `url_span`; code copies the picked string verbatim.

## 7. Overlay — minor

- Keep `listening` until final (already done); `thinking` keeps the words (already done).
- Show the committed clause + the buffered remainder when a chain is running, so the user can see
  what was consumed.

## 8. Migration order (each step shippable)

1. `spans.py` candidates + `text_span`/`url_span` + remove `or transcript` fallbacks.
2. Single Jev request (`act_now`, `destructive`, `scroll_amount`, `is_correction` merged; call #2
   removed).
3. `backend/context.py`, encode into state, record turns with `running` outcome, pass
   `pending_confirmation`.
4. Policy: act on `act_now` / silence / final; add silence retry.
5. Scheduler single-flight/coalesce + action worker; stop cancelling actions.
6. Prefix consumption + buffered remainder; comma-bounded payload commit.
7. Optional later: clause splitter producing a plan queue, each step decided against the new page.

## 9. Tests

- `spans`: `"and search for physiotherapy services in edmonton, and scroll down"` →
  `"physiotherapy services in edmonton"`; no full-transcript fallback.
- `policy`: `clause_complete` acts without `is_final`; `incomplete` waits; comma-bounded free text
  acts; 900 ms silence bypasses; destructive still confirms.
- `scheduler`: N partials in 1 s → ≤2 in flight, ≥1 decision, no action cancelled.
- `context`: interleaved order, ignored utterances absent, executed prefix moves to history,
  `running` outcome updated.
- Chain: search then scroll, with a simulated 500 ms page load → scroll decided on the results
  page, not the old one.

## 10. Targets and risks

- Last word → decision ≈ **500 ms** (200 ms debounce + ~300 ms Jev).
- ~1 request per 200 ms while speaking; ~$0.0002 per request.
- Risk: committing free text on a comma can truncate if the user pauses mid-query. Mitigation:
  only when `act_now == clause_complete` *and* a comma bounds the span; else wait for
  final/600 ms silence.
- Risk: recognizer revises already-executed words. Mitigation: prefix match before slicing;
  discard mismatches.
