---
name: clip-stream
description: Turn a streamer's VOD into ready-to-post YouTube Shorts / TikToks / Reels. Use when the user gives a Twitch/YouTube/Kick stream link or a streamer's name and asks to clip it, find the best moments, or "get clips out".
---

# Clip a stream into Shorts

The engine lives in `clipper/` (run from the repo root). You (Claude) are the editor:
the engine finds loud/hype moments and transcribes them, **you** pick the real bangers,
set exact cut points, and write the titles. Then the engine renders 1080x1920 clips
with face-tracked layout and burned-in animated captions.

## 0. Setup (once per machine/session)
```bash
bash setup.sh            # installs yt-dlp, faster-whisper, opencv; checks ffmpeg
```

## 1. Work out what to clip
- **A VOD link** (`twitch.tv/videos/…`, YouTube, Kick): use it directly.
- **A streamer name**: list their latest VODs and take the newest finished stream:
  ```bash
  yt-dlp --flat-playlist --playlist-end 5 --print "%(url)s | %(duration)s s | %(title)s" "https://www.twitch.tv/<name>/videos?filter=archives&sort=time"
  ```
- **Permission check:** look at `streamers.md`. Only clip streamers listed there (an active paid
  campaign or written permission). If the streamer isn't listed, tell the user and ask whether
  there is a campaign/permission before rendering anything for upload. Unlicensed reposts get
  copyright strikes and get channels rejected from monetisation.

## 1b. Streamer is live right now? Clip it NOW (speed wins campaigns)
```bash
python3 -m clipper latest <twitch_name> --hours 1 --top 15
```
Analyses the last N hours of their newest VOD, including the stream that is still going.
Re-run every 30–60 min during big streams; each run gets its own folder.

## 2. Analyse
```bash
python3 -m clipper analyze "<URL>" --top 30
```
- Full streams (6–10 h) take a while because the whole audio is downloaded first (roughly 1–3 min per hour of stream in the cloud). Run it in the background for long VODs.
- To go faster, or when the user names a part of the stream, add `--from 2:00:00 --to 4:00:00`.
- If the user provides a chat export (TwitchDownloader JSON), add `--chat path.json`. Chat spikes are the best signal.
- Output: `runs/<channel>_<id>/candidates.md` (read this) and `candidates.json`.

## 3. Pick the clips (this is your job: be ruthless)
Read **all** of `candidates.md`. Each candidate has a timestamped transcript with absolute VOD seconds.
Pick the best **6–12** (fewer if the stream is dull; never pad with weak clips). A clip is good if:
- It makes sense to someone who has **never seen this streamer** (no inside context needed).
- The **first 2 seconds** grab attention (reaction, shocking line, question, or the moment starting).
- There's a **payoff**: a punchline, rage, a clutch play, a wild take, a genuine reaction.
- 15–45 s is ideal; **under 59 s** always.

Reject: dead air, mid-conversation rambling, reading donations, anything where the payoff is
missing from the transcript, tragedies, slurs, sexual content, gambling sponsor segments, and copyrighted music playing
as the main audio (it gets Content ID claimed).

Cut points: start ~0.3 s before the first word of the set-up line (use the transcript seconds);
end ~0.5 s after the payoff. Never leave a slow lead-in, so cut straight into the action. You may go
outside the "context" window; the engine transcribes extra audio when needed.

Write `runs/<run>/picks.json`:
```json
{"clips": [
  {"start": 4402.6, "end": 4431.9,
   "title": "xQc Can't Believe This Police Chase 😳",
   "hook": "HE DIDN'T SEE IT COMING",
   "description": "xQc reacts to the craziest police chase of the year",
   "tags": ["xqc", "twitch", "policechase", "funny"],
   "layout": "auto"}
]}
```
- **title** (≤70 chars): streamer name + curiosity/emotion. No clickbait that the clip doesn't deliver.
- **hook**: ≤6 words, shown big for the first 3.5 s. Leave `""` if the opening line is already the hook.
- **layout**: `auto` (default) | `split` (facecam top / gameplay bottom) | `face` (crop on streamer) | `blur` (full frame).
  `"cam": [x, y, w, h]` (fractions of the frame) forces a facecam box if auto-detection gets it wrong.
- Campaign rules override everything: if the campaign brief requires a specific caption, tag, or link, put it in.

## 4. Render
```bash
python3 -m clipper render runs/<run> --picks runs/<run>/picks.json
```
Outputs in `runs/<run>/clips/`: `NN_title.mp4`, `NN_title.txt` (title + description to paste),
and `upload_sheet.csv`.

## 5. Quality check (always)
Grab a frame from each clip and look at it:
```bash
for f in runs/<run>/clips/*.mp4; do ffmpeg -v error -y -ss 4 -i "$f" -frames:v 1 -vf scale=360:-2 "${f%.mp4}.jpg"; done
```
Read the images. Check: facecam crop shows the face (not half a face or gameplay), captions readable,
nothing important hidden. Fix with `layout`/`cam` in picks.json and re-render that clip
(delete its mp4 first).

## 6. Hand over
- Send the finished `.mp4` files to the user (SendUserFile) with the title for each.
- Tell them which channel each clip belongs on (see `PLAYBOOK.md`: one streamer per YouTube channel) and
  the posting schedule. Update `tracker.csv` with the clips made (date, streamer, file, title, channel).
- Remind them: also post the same clip to TikTok + Instagram Reels if the campaign pays on those.

## Auto mode (no review, faster but worse)
```bash
python3 -m clipper auto "<URL>" --clips 8
```
Use only if the user explicitly wants zero review.
