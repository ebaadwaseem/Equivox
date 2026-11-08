# voice-browser

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
cp .env.example .env
python main.py
```
Type commands into the terminal: `click new`, `scroll down`, `back`, `search pizza`, `go to github.com`.
It works with no API key (keyword fallback). Add a key and VB_MODEL to use the real model.

## Real voice input (macOS, on-device)

`main.py` listens on three inputs at once: the terminal, the Vox panel's typed-command
box, and (on macOS, once built) your actual microphone via Apple's Speech framework --
fully on-device, nothing sent over the network. Typed input always keeps working even
if voice isn't set up.

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

## Who owns what
| Folder | Owner | First job |
|---|---|---|
| `audio/` | Person 1 | Real mic → streaming partial + final `Transcript`s (replace `TypedTranscriber`) |
| `perception/` | Person 2 | Better element extraction (a11y tree?), smarter `shortlist`, screenshot |
| `backend/` | Person 3 | OpenRouter/Jev call in `model.py`, partials through `scheduler.py` |
| `executor/` | (later) | Works already; extend when needed |
| `shared/protocol.py` | Everyone | **The contract. Agree before changing.** |

```
audio/transcriber.py  →  Transcript
perception/perceive.py →  PageState / Element list
backend/model.py      →  Decision
executor/act.py       →  does it in the browser
main.py               →  wires it all together
```

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
