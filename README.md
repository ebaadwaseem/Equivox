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

## Quick start (macOS)

Requires Python and the Swift compiler for the Apple Speech listener. From the
repository root:

```sh
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

For troubleshooting, optional speech models, vision features, and testing, see the
[Development Guide](README-DEVELOPMENT.md).
