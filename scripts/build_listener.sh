#!/bin/bash
# Builds the on-device Apple Speech listener (audio/apple_speech/main.swift) into
# bin/vox-listen, with Info.plist embedded so macOS attributes mic/speech permission
# prompts to this binary (with usage-description strings) instead of just "a process".
#
# Run once: ./scripts/build_listener.sh
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p bin

swiftc -O audio/apple_speech/main.swift -o bin/vox-listen \
    -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker audio/apple_speech/Info.plist

echo "built bin/vox-listen"
echo "on first run, macOS will ask for Microphone and Speech Recognition permission --"
echo "it may be attributed to Terminal/iTerm/VS Code (whatever launched this script)."
echo "if it's denied or the listener crashes immediately, enable it manually in"
echo "System Settings -> Privacy & Security -> Microphone / Speech Recognition."
