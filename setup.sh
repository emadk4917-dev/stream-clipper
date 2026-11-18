#!/usr/bin/env bash
# One-time setup for the stream clipper (Linux / macOS / WSL).
set -e
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg is missing. Install it: macOS 'brew install ffmpeg' | Ubuntu 'sudo apt install ffmpeg' | Windows 'winget install ffmpeg'"
  exit 1
fi
python3 -m pip install -q -U -r requirements.txt
python3 -m clipper --help >/dev/null && echo "✅ clipper ready. Try: python3 -m clipper analyze <twitch VOD link>"
