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
