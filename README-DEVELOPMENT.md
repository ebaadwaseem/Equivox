# Equivox — Development Guide

[Back to the presentation README](README.md)

This guide retains the detailed usage, configuration, and development notes from
the original README. For installation and the basic demo, start with the
[quick start](README.md#quick-start-macos).

- [Voice input and permissions](#real-voice-input-macos-on-device)
- [Incremental commands, history, and testing](#incremental-commands-and-history)
- [Voice choices and basic controls](#voice-choices-and-basic-controls)
- [Page descriptions and visual choices](#optional-page-descriptions-and-visual-choices)
- [Alternative local streaming speech](#alternative-local-streaming-speech)
- [Streaming forms and option ranking](#streaming-forms-and-option-ranking)

## Real voice input (macOS, on-device)

`main.py` listens on three inputs at once: the terminal, the Vox panel's typed-command
box, and (on macOS, once built) your actual microphone via Apple's Speech framework --
speech recognition runs on-device. With Jev configured, transcripts and page context
are sent to the decision endpoint. Typed input always keeps working even if voice
isn't set up.

```
scripts/build_listener.sh
python main.py
```

The first run will prompt for Microphone and Speech Recognition permission -- approve
both. If recognition fails with "on-device recognition unavailable", turn on
System Settings → Keyboard → Dictation (and enable the offline/on-device option) and
try again. `VB_TRANSCRIBER=typed python main.py` always forces typed-only input,
and `VB_TRANSCRIBER=apple` forces the mic even if auto-detection would've skipped it.

Say "stop listening" / "pause listening" to mute the mic without closing anything,
"start listening" to unmute, or click the Vox droplet to toggle -- it works even while
the panel is minimized.


## Incremental commands and history

Speak or type a chain such as `open a new tab then search for physiotherapy clinics
in edmonton okay now scroll down`. Each clause is decided against the page produced
by the previous action. Complete commands can execute on partial speech; growing
search/type payloads wait for a following command, recognition final, or quiet.
Quote dictated text containing literal command connectors.

Vox's **Recent speech & actions** shows up to 100 session actions across navigation.
Highlighted rows are the twelve most recent actions included in Jev's context.
History is kept in memory for the current process.

`VB_VISION=1` optionally sends a JPEG page thumbnail (up to 768×512) with each
request. Use a compatible vision decision model/endpoint; the default text/DOM path
is faster. `VB_JEV_URL` overrides the OpenRouter System One endpoint when needed.
The image transport uses OpenRouter state content parts, not TypeSafe's assets map.

Install pytest with `python -m pip install pytest`, then run `python -m pytest -q`
for offline regressions (tests ignore local API keys). Run
`python -m scripts.benchmark_decisions --live` for a small synthetic API latency and
policy probe using your configured key/model. It does not execute browser actions.
See [refactor-plan.md](refactor-plan.md) for implementation findings, research,
measured limits, and next steps.

## Voice choices and basic controls

Say **show options**, then **click option one** or **click option two**. Vox shows
one row per choice with its number and label. The same phrases resolve an ambiguous
click or a “which page?” question. A focused native dropdown exposes its own choices.
Choices belong to the page that produced them. Changed or expired visual targets
require a fresh set of options.

New tabs open Google by default. Set `VB_NEW_TAB_URL` to another HTTP(S) home page.
Switch with **next tab**, **previous tab**, **switch to tab two**, **switch to the
first tab**, or **switch to the appointment tab**. Tab numbers follow the browser's
open-tab order. A missing or ambiguous tab name reports a problem instead of silently
staying put.

Other exact commands bypass Jev's intent question:

- **reload tab** or **refresh the page**
- **clear text**, **backspace**, **press delete key**
- **press enter**, **press tab**, **press escape**, **click x key**
- **select all text**, **press backspace three times**

Editing commands use the focused field. Click or name the field first. Key commands
accept a small allowlist rather than arbitrary browser shortcuts. The last transcript
stays in Vox after an action. The short **Say a browser command** hint returns after
30 seconds without a new command.

## Optional page descriptions and visual choices

**Tell me what you can see**, **describe the page**, and **help me ...** can use a
separate generative vision model. Set `VB_VLM_MODEL` to a compatible model slug.
`VB_VLM_URL` defaults to OpenRouter's chat-completions endpoint. Authentication uses
`VB_VLM_API_KEY`, falling back to `OPENROUTER_API_KEY`. A local OpenAI-compatible
vision endpoint can also be configured with these settings.

This path runs only on request (or an ambiguous Maps target when configured). Jev's
normal intent question stays unchanged. The VLM returns a description, suggested
steps, and visible choices. It never runs a plan automatically. **Click option one**
executes one displayed choice. Coordinate choices require an unchanged page scene
and screenshot. Without a configured VLM, descriptions and options use labelled page
controls and visible text, and say so.

Maps use the same general choices. Small additional shortcuts support **pan map
left**, **directions to Edmonton City Hall**, and searches within an open Google Maps
page. This is a browser prototype, not a complete autonomous trip planner.

## Alternative local streaming speech

Apple Speech remains available. Moonshine is an optional local streaming adapter:

```sh
python -m pip install -r requirements-voice.txt
python -m scripts.setup_voice --size medium
VB_TRANSCRIBER=moonshine python main.py
```

Choose `--size small` for less inference work, or `--size medium` for the larger
English model. Setup downloads the selected model once into the ignored `.cache/`
directory. Application startup loads cached files and never starts a model download.
`VB_TRANSCRIBER=auto` (the default) uses a prepared Moonshine model, otherwise Apple
Speech if built, otherwise typed input. Force `VB_TRANSCRIBER=apple` to compare the
old recognizer. `VB_AUDIO_DEVICE` accepts a PortAudio input device index or name.

The adapter emits partials and a final with the same utterance ID, uses a dedicated
inference thread, and forwards microphone levels separately. Pausing/resuming starts
a fresh native stream so a resumed turn cannot repeat consumed speech. Transcript
matching tolerates number words changing to digits, such as “option one” to “option 1.”

For a repeatable recording test, use a mono PCM16 WAV:

```sh
python -m scripts.benchmark_voice --wav sample.wav --realtime
```

This reports emitted partials and finals, plus native inference times. A synthetic
speech replay confirms integration, not accuracy on the user's microphone or accent.
The adapter follows the [Moonshine streaming API](https://moonshine-voice.readthedocs.io/en/latest/using/transcription/).

## Streaming forms and option ranking

Commands separated by `and`, `then`, or punctuation execute in order against a fresh
page. A following command bounds the current payload, including without an API key.
For example, `open a new tab and search for edmonton oilers` opens the tab as soon as
`and search` arrives, while the growing search query waits for final speech or quiet.

`Fill in my name Sarah Chen, email sarah@gmail.com, phone 780 555 1234, and pick Tuesday`
uses observed field labels. Labelled form lists also work without ASR punctuation.
Arbitrary labels such as `Organization` work with `fill Organization Acme`; quote
values that themselves contain field names or command connectors. Email fields accept
spoken `at` and `dot`. Duplicate labels ask which field; unnamed typing requires a
focused field or a single available text field.

Clicking a native `<select>` focuses it and presents its enabled choices in Vox.
Choose by number or say `pick Tuesday`. Disabled options and disabled option groups
are excluded. ARIA dropdowns use their visible option controls, waiting for the popup
to render before choosing a named option.

Ambiguous action options use Jev target probabilities when supplied, filter by action
role, and omit zero or very weak alternatives. Without Jev they use the lexical DOM
shortlist. **Show options** prioritizes focused controls, popup suggestions and native
dropdown values; otherwise it lists visible DOM controls with page content ahead of
navigation chrome. A configured VLM can supply visual choices. These rankings are
suggestions, not a general autonomous planner.

Apple Speech now uses the [dictation task hint](https://developer.apple.com/documentation/speech/sfspeechrecognitiontaskhint/dictation)
and extra browser vocabulary. Rebuild with `scripts/build_listener.sh` after updating.
Set `VB_SPEECH_LOCALE=en-CA` to select Canadian English (requires the corresponding
on-device recognizer), or `VB_SPEECH_TERMS='Equivox,Oilers,backspace'` for personal
vocabulary. These tune recognition; accuracy needs comparison on real recordings.
Moonshine remains the optional alternative described above.
