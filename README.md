# Equivox

**Control the web with fluid, conversational speech.**

For people living with motor control disabilities, using a mouse and keyboard can
be exhausting or impossible. Equivox aims to make everyday browsing more accessible
by turning spoken commands into browser actions.

## What it does

- Opens and switches tabs, searches the web, clicks links, and scrolls pages.
- Fills labelled form fields and selects dropdown options.
- Follows chained commands, checking the resulting page after each action.
- Shows live transcripts, numbered choices, and recent actions in the **Vox** panel.

Try saying:

> “Open a new tab, then search for physiotherapy clinics in Edmonton, okay now scroll down.”

You can also say **“show options”**, **“click option one”**, or
**“fill in my name Sarah Chen.”** Say **“stop listening”** to pause the microphone
and **“start listening”** to resume.

## How it works

`Speech → live transcript → page context → decision → browser action`

On-device speech recognition feeds partial transcripts into **Jev**, a System One
decision model accessed through OpenRouter. Jev identifies the intent, target, and
whether a command is ready to execute. Python validates the decision, and
**Playwright** carries out the action. Complete commands can begin executing while
you continue speaking.

Speech recognition runs locally; the configured decision endpoint receives
transcripts and page context. Basic commands also have a keyword fallback.

## Access the project

Equivox runs locally and opens a Chromium browser with the Vox control panel.
To try the prototype, download or clone the
[source code on GitHub](https://github.com/garbhj/Equivox) and follow the setup below.

## Quick start (macOS)

### Requirements

- A Mac with a built-in or external microphone for the Apple Speech voice demo.
- Python 3.10 or newer, Git, and the Swift compiler (available with Xcode Command
  Line Tools).
- An available on-device Apple Speech recognizer for your language, with
  Microphone and Speech Recognition permissions enabled.
- Internet access for dependency downloads, web browsing, and the decision API.
  Jev commands require an OpenRouter API key and a compatible System One model ID.
- Python dependencies in [requirements.txt](requirements.txt) and Playwright's
  Chromium browser, installed below. Optional Moonshine speech dependencies are in
  [requirements-voice.txt](requirements-voice.txt).

The demo uses a standard computer and microphone; no additional hardware is
required. Typed commands do not require a microphone or the Apple Speech listener.

### Install and run

Clone the repository, then install the dependencies:

```sh
git clone https://github.com/garbhj/Equivox.git
cd Equivox
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
scripts/build_listener.sh
```

Create a `.env` file with your OpenRouter key and a compatible System One model ID:

```dotenv
OPENROUTER_API_KEY=your-openrouter-api-key
VB_MODEL=your-system-one-model-id
```

Then run:

```sh
python main.py
```

Allow **Microphone** and **Speech Recognition** permissions when prompted. Typed
commands work in the terminal or Vox panel; use `VB_TRANSCRIBER=typed python main.py`
for a typed-only demo. Without API credentials, basic keyword commands still work.

Equivox is a prototype; speech accuracy and complex page controls remain areas for
improvement.

## Supporting materials

- [Project presentation (Google Slides)](https://docs.google.com/presentation/d/1OlD3BUggcFXFsSIUJl_Wy64u8tGmhdyYMwmhPhEcnOg/edit?usp=sharing)
- [Development Guide](README-DEVELOPMENT.md): troubleshooting, optional speech
  models, vision features, and testing.

## Sources and credits

Equivox uses the following third-party tools and references:

- [Apple Speech](https://developer.apple.com/documentation/speech) and
  AVFoundation for on-device speech recognition and microphone capture.
- [Jev / TypeSafe](https://docs.typesafe.ai/introduction), accessed through
  [OpenRouter](https://openrouter.ai/), for structured browser-action decisions.
- [Playwright](https://playwright.dev/python/) and Chromium for browser automation.
- [Moonshine](https://moonshine-voice.readthedocs.io/en/latest/using/transcription/)
  for the optional local streaming speech adapter.
- HTTPX, python-dotenv, Pillow, Jellyfish, and RapidFuzz for HTTP requests,
  configuration, image processing, and text matching; see [requirements.txt](requirements.txt).

Development research also referenced
[Moritz Kremb's Jev voice browser](https://github.com/moritzkremb/jev-voice-browser),
[Jev CUA](https://github.com/edgeteamio/jev-cua), and the
[Jev voice decision benchmark](https://github.com/GaNotchVFX/jev-benchmarks/tree/main/02-voice-decision-layer).
See [development research and verification notes](refactor-plan.md) for comparisons
and prototype measurements.
