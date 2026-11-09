# Voice-control changes

## Demo commands

- Open a new tab
- Next tab / previous tab / switch to tab two / switch to the first tab
- Switch to the appointment tab
- Reload tab
- Show options
- Click option one / click option two
- Clear text / delete text / backspace / press delete key
- Click x key / press enter / press tab / press escape
- Select all text / press backspace three times
- Tell me what you can see

New tabs now open `https://www.google.com/`, configurable with `VB_NEW_TAB_URL`.
Switching brings the selected page to the front, supports tab numbers, and reports
missing/ambiguous names. A page visibility binding keeps the active page aligned
with manual tab changes. Actions retain their reserved page during execution.

## Options

Options use separate numbered rows with readable labels and details. Physical
buttons send the same `click option N` command as voice. Number words and digits
both work. Choice replies bypass Jev. Disambiguation uses the model's target
probabilities and relevant control roles instead of blindly showing the first
three shortlisted elements.

The pending question consumes the original clause without recording a successful
action. That lets a reply arrive in the same physical utterance. Number normalization
(`one` becoming `1`) does not discard the remaining speech. A noisy partial reply
does not close the question. A numbered reply waits for final, a following command,
or 600 ms of actual quiet before it executes. This avoids immediately acting on
an option number that the recognizer is still revising.

Choices expire after a minute and belong to one page. DOM choices recheck the
selected control's identity. Visual coordinate choices additionally recheck scene
revision and the screenshot before clicking. Destructive DOM labels retain the
confirmation step. The prototype does not offer ungrounded destructive coordinate
clicks.

A focused native select can expose its option labels through `show options`.
General page choices and descriptions work without a VLM using observed labels.

Open autocomplete suggestions take priority over general page controls. The reader
follows the input's `aria-controls`/`aria-owns` popup and supports both listbox
options and nested grid rows, as used by Google Maps. Say `click option two` or
`choose the second option` directly; `show options` lists those same suggestions
with names and addresses. Numbering follows the current visible order. This does
not require a VLM or a Jev call for the numbered selection.

## Text controls and hints

Exact reload, tab and keyboard commands use code, without enlarging Jev's main
intent question. Editing acts on the focused native or contenteditable field.
The key allowlist covers common editing/navigation keys and single characters,
with at most twenty repeated presses. It does not expose arbitrary shortcuts.

Vox keeps the last received transcript after an action or an ignored utterance.
After thirty seconds without a new command, it restores the brief `Say a browser
command` hint. The main placeholder no longer includes the physiotherapy example
or a click-to-type instruction. Typed input remains available.

## Optional visual descriptions and planning

Set `VB_VLM_MODEL` to a compatible generative vision model. `VB_VLM_URL` can target
OpenRouter or a local compatible chat-completions endpoint. `VB_VLM_API_KEY` overrides
the usual OpenRouter key. `tell me what you can see`, `describe the page`, `show
options`, and `help me ...` can then request a screenshot description, suggested
steps and up to eight grounded choices. The VLM does not run those steps itself.
Each action still comes from the user's selected option or subsequent command.

This is a general page feature. Maps use the same mechanism. Small map shortcuts
also support panning, in-map search and directions through Google's documented
Maps URL API. They are deliberately limited in this prototype.

The VLM wire format and validation are covered with mock responses. No live VLM
accuracy claim is made. API reference:
https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request
Google Maps URL reference:
https://developers.google.com/maps/documentation/urls/get-started

## Local speech alternative

Moonshine Voice 0.1.6 is available through `requirements-voice.txt`. The adapter uses
its public streaming API, a dedicated inference thread, and separate audio capture.
Microphone levels reach the UI independently of decoding. Audio never uploads.
The native stream and physical utterance IDs remain distinct across pause/resume.

Both small and medium English streaming models were prepared locally in the ignored
`.cache/` directory. Medium is the current prepared model. Startup in auto mode uses
it when available, otherwise the built Apple listener, otherwise typed input. Set
`VB_TRANSCRIBER=apple` or `moonshine` explicitly for comparisons. Startup never
performs an implicit model download.

A synthetic 10.23-second spoken command recording replayed at real speed through
both sizes. Each produced the expected complete command sequence, including
physiotherapy clinics in Edmonton and option 1 / option 2. Both also produced a
short extra `Key` fragment at the end and unstable intermediate wording. Small
reported roughly 80-403 ms native inference on the main line, medium roughly
107-446 ms. These are single synthetic replays, not microphone accuracy or a
controlled latency comparison with Apple Speech. The option-reply gating and
non-command fragment regression cover issues exposed by the replay.

The adapter requests a 160 ms update interval, but actual partial emission depends
on model scheduling and inference cost. It should not be described as guaranteed
160 ms speech-to-text latency. Record the user's voice and compare both adapters
before deciding which model works best for the demo.

Setup and replay:

```sh
python -m pip install -r requirements-voice.txt
python -m scripts.setup_voice --size medium
VB_TRANSCRIBER=moonshine python main.py
python -m scripts.benchmark_voice --wav sample.wav --realtime
```

Primary library documentation:
https://moonshine-voice.readthedocs.io/en/latest/using/transcription/
