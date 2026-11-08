#!/usr/bin/env python3
"""Fake vox-listen process: plays back a scripted sequence of JSON lines on stdout,
so audio/apple_transcriber.py can be tested without a real microphone or the Swift
listener built.

Usage: fake_listen.py <script_file> [--endpoint-ms N] [--allow-server]

The script file has one entry per line:
  - a JSON object: printed as-is to stdout (flushed), after a small delay
  - "__CRASH__": sys.exit(1) immediately (simulates the process dying)
  - "__EXIT__": sys.exit(0) immediately (clean exit)
  - anything else: printed as-is too (e.g. deliberately malformed JSON, to test that
    the Python side ignores bad lines without crashing)

After the script runs out (without an explicit __EXIT__/__CRASH__), the process stays
alive reading stdin (like the real listener would for pause/resume) until stdin closes.
"""
import sys
import time


def main() -> None:
    script_path = None
    for arg in sys.argv[1:]:
        if not arg.startswith("--"):
            script_path = arg
            break
    if script_path is None:
        print("usage: fake_listen.py <script_file>", file=sys.stderr)
        sys.exit(2)

    with open(script_path) as f:
        lines = [line.rstrip("\n") for line in f if line.strip()]

    for line in lines:
        if line == "__CRASH__":
            sys.exit(1)
        if line == "__EXIT__":
            sys.exit(0)
        print(line, flush=True)
        time.sleep(0.02)

    while True:
        line = sys.stdin.readline()
        if not line:
            break


if __name__ == "__main__":
    main()
