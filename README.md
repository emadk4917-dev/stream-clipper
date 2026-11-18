# Stream Clipper

Turns streamer VODs into finished vertical Shorts (1080×1920, facecam/gameplay layout, animated captions).

- **Strategy & how the money works:** [PLAYBOOK.md](PLAYBOOK.md)
- **Who we may clip:** [streamers.md](streamers.md) · **Results log:** [tracker.csv](tracker.csv)
- **Claude skill:** `.claude/skills/clip-stream/SKILL.md`. In Claude Code, just say *"clip xqc's latest stream"* or paste a VOD link.

## Manual use
```bash
bash setup.sh
python3 -m clipper analyze "https://www.twitch.tv/videos/123456789" --top 30   # find moments
#   → edit runs/<run>/picks.json (or let Claude do it)
python3 -m clipper render runs/<run> --picks runs/<run>/picks.json            # make Shorts
python3 -m clipper auto "https://www.twitch.tv/videos/123456789" --clips 8    # zero-review mode
```

## How it works
1. Downloads only the stream **audio** first (fast), measures loudness every second (+ chat activity if you give a chat export).
2. Finds the biggest spikes, transcribes ~75 s around each with Whisper, ranks them.
3. Claude reads the transcripts, picks the real highlights, sets exact cuts, writes titles/hooks.
4. Downloads only the needed video sections, aligns them to the audio, detects the facecam
   (split / face-crop / blur layout), burns in word-by-word captions and normalises loudness for Shorts.
