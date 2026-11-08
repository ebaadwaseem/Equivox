# voice-browser

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
cp .env.example .env
python main.py
```
Type commands into the terminal: `click new`, `scroll down`, `back`, `search pizza`, `go to github.com`.
It works with no API key (keyword fallback). Add a key and VB_MODEL to use the real model.

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
