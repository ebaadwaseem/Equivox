# Live English Subtitles

## Setup

Use Python 3 with Tkinter on Windows or macOS.

1. Install dependencies: `python -m pip install -r requirements.txt` (macOS: use `python3`).
2. Download the English model from https://alphacephei.com/vosk/models and extract `vosk-model-small-en-us-0.15` beside `transcribe.py`. The model folder must contain `am/` and `conf/` directly.
3. Open `transcribe.py` in your IDE and click Run. No model arguments are required. Allow microphone access.

Keep `display.py` and `transcribe.py` in the same folder. Running `display.py` alone opens an empty window; run `transcribe.py` for recognition.

The window shows one line. New words extend to the right; when they exceed the width, older text scrolls left. Temporary hypotheses are replaced as recognition changes them, then committed once. Always on Top and Clear remain available. Clear removes visible history; the current spoken phrase may reappear with subsequent recognition updates. Up to 20,000 characters are retained.

Microphone status and errors appear below the subtitles. Full diagnostics remain in the console. Close the window or press Enter in the console to stop. On errors, the window stays open so you can read the message.

Optional arguments: `--list-devices`, `--device 1`, `--model PATH`, `--block-ms 200`, `--samplerate 16000`.

## Changes

- `transcribe.py`: recognition now runs in a worker thread; Tk runs on the main thread. Partial and final results are sent to `display.py`; microphone status also reaches the window. Closing the window signals capture to stop. Existing offline English recognition, model path, audio settings and diagnostics are retained.
- `display.py`: retains the last requested single-line layout, horizontal following, English UI, topmost option and Clear. Adds separate partial/final methods and a thread-safe update queue to avoid duplicated or stale hypotheses. Removes simulated demo input.

Syntax and partial/final update logic were checked locally. Actual microphone recognition and native window behavior still need testing on Windows and macOS.
