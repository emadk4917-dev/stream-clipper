"""Real-time clipper: a moment on stream becomes a clip on your phone in ~2-3 minutes.

  python -m clipper.realtime                 # run (systemd: clipper-realtime)
  python -m clipper.realtime --chat-only     # tuning: log chat + print spikes, no video

How it works (the launch plan's "live clipper", section 04):
  1. Reads Twitch chat of every ACTIVE campaign streamer (anonymous, no token).
  2. Records each of those streams into a 10-minute rolling buffer (streamlink -> 2 s segments).
  3. When chat spikes (KEKW / LMAO / CLIP IT / ????? well above that channel's normal), it waits
     for the punchline, cuts the last ~90 s from the buffer, transcribes it, and Claude picks the
     exact 15-59 s cut, title and hook (and a 61 s+ TikTok cut when it holds up).
  4. Rendered with facecam layout + captions, then sent to Telegram for one-tap approval.

Only streamers on a campaign you added with /addcampaign are ever recorded or clipped.
Campaign changes are picked up by restarting automatically (systemd brings it back).
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import threading
import time
import traceback
from pathlib import Path

from . import approvals as Q
from . import judge as J
from .config import DATA, db, env, env_float, env_int
from .render import render_clip
from .rt.chat import TwitchIRCReader
from .rt.detector import DetectorParams, SpikeDetector, SpikeEvent
from .rt.recorder import RingRecorder, ffmpeg_exe
from .transcribe import transcribe_range

BUFFER = DATA / "buffer"
OUT = DATA / "rt_clips"
CHATLOG = DATA / "chatlogs"
PRE = env_float("RT_PRE_ROLL_S", 70)     # the launch plan's long cut: 70 s before + 20 s after
POST = env_float("RT_POST_ROLL_S", 20)
SEG = env_int("RT_SEGMENT_S", 2)
MIN_SCORE = env_float("MIN_SCORE", 5)
WHISPER = env("WHISPER_MODEL", "small")
QUALITY = env("RT_QUALITY", "1080p60,1080p,720p60,720p,best")

log = logging.getLogger("clipper.realtime")


def detector_params() -> DetectorParams:
    """Detector thresholds; override any of them in .env, e.g. RT_Z_THRESHOLD=5."""
    p = DetectorParams()
    for field in ("z_threshold", "min_ratio", "min_rate", "min_hype_share", "cooldown_s", "warmup_s"):
        v = env("RT_" + field.upper())
        if v:
            setattr(p, field, float(v))
    v = env("RT_MIN_UNIQUE_CHATTERS")
    if v:
        p.min_unique_chatters = int(v)
    return p


def campaigns() -> list[dict]:
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM campaigns WHERE active=1")]


# ---------------------------------------------------------------- cut from the ring buffer
def cut_raw(ev: SpikeEvent) -> tuple[Path, float] | None:
    """Join the buffered segments around the spike into one file. Returns (path, spike offset in file)."""
    seg_dir = BUFFER / ev.channel
    segs = sorted(seg_dir.glob("seg_*.ts"), key=lambda p: int(p.stem[4:]))[:-1]  # newest is still being written
    spans, prev = [], None
    for p in segs:
        try:
            end = p.stat().st_mtime
        except OSError:
            continue
        start = prev if (prev is not None and 0 < end - prev < SEG * 3) else end - SEG
        spans.append((p, start, end))
        prev = end
    t0, t1 = ev.t - PRE, ev.t + POST
    use = [s for s in spans if s[2] > t0 and s[1] < t1]
    if not use:
        return None
    OUT.mkdir(parents=True, exist_ok=True)
    stem = f"{ev.channel}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(ev.t))}"
    lst = OUT / f"{stem}.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p, _, _ in use))
    raw = OUT / f"{stem}_raw.mp4"
    subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", str(raw)], check=True)
    lst.unlink(missing_ok=True)
    return raw, ev.t - use[0][1]


def probe_len(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


# ---------------------------------------------------------------- judge + edit + queue
def process_raw(raw: Path, spike_at: float, ev: SpikeEvent, campaign: dict) -> int:
    """Transcribe the cut, let Claude choose the clip, render it, send for approval."""
    dur = probe_len(raw)
    words = transcribe_range(raw, 0, dur, model=WHISPER)
    cand = {"id": "c1", "peak": spike_at, "hype": ev.z, "context_start": 0.0, "context_end": dur,
            "words": words, "suggested_start": max(0.0, spike_at - 25), "suggested_end": min(dur, spike_at + 8)}
    chat = f"{ev.rate}/s vs normal {ev.baseline}/s, {ev.unique_chatters} chatters, top: {ev.top_tokens[:4]}, " \
           f"e.g. {ev.sample_messages[-5:]}"
    picks = J.judge([cand], campaign["streamer"], campaign.get("rules") or "", chat=chat)
    sent = 0
    for p in picks:
        if p["score"] < MIN_SCORE:
            log.info("[%s] judged %.0f/10, dropped: %s", ev.channel, p["score"], p.get("reason", "")[:80])
            continue
        p["start"], p["end"] = max(0.0, p["start"]), min(dur, p["end"])
        p["peak"] = ev.t  # wall-clock time, used to avoid queueing the same moment twice
        mp4 = raw.with_name(raw.stem.replace("_raw", "") + ".mp4")
        cam_file = DATA / f"facecam_{campaign['streamer']}.json"
        known = json.loads(cam_file.read_text()) if cam_file.exists() else None
        lay = render_clip(raw, p["start"], p["end"], words, p["start"], p.get("hook", ""), mp4,
                          None, None, True, known)
        if lay["layout"] == "split" and not known:
            cam_file.write_text(json.dumps(lay["cam"]))
        tt = None
        if p.get("tiktok_end", 0) - p.get("tiktok_start", 0) >= 61:
            tt = raw.with_name(raw.stem.replace("_raw", "") + "_tiktok.mp4")
            ts, te = max(0.0, p["tiktok_start"]), min(dur, p["tiktok_end"])
            render_clip(raw, ts, te, words, ts, p.get("hook", ""), tt, None, None, True, known)
        desc = (f"{p.get('description') or p['title']}\n\n🎥 Full credit: {campaign['streamer']} → "
                f"https://twitch.tv/{campaign['streamer']}\n\n"
                + " ".join("#" + t.lstrip("#") for t in ["shorts", *p.get("tags", [])]))
        cid = Q.add_clip(campaign, "live", p, mp4, tt, desc)
        if cid:
            Q.request_approval(cid)
            sent += 1
    return sent


def handle_spike(ev: SpikeEvent, campaign: dict) -> None:
    try:
        time.sleep(max(0.0, ev.t + POST + SEG * 1.5 - time.time()))  # wait for the punchline to be recorded
        got = cut_raw(ev)
        if not got:
            log.warning("[%s] spike but no video in buffer (stream offline / recorder down?)", ev.channel)
            return
        raw, spike_at = got
        n = process_raw(raw, spike_at, ev, campaign)
        log.info("[%s] spike z=%.1f → %d clip(s) sent for approval", ev.channel, ev.z, n)
        if not n:
            raw.unlink(missing_ok=True)
    except Exception:
        log.error("[%s] spike handling failed\n%s", ev.channel, traceback.format_exc())


# ---------------------------------------------------------------- main loop
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chat-only", action="store_true", help="no recording/clipping: log chat + print spikes")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    camps = {c["streamer"].lower(): c for c in campaigns()}
    if not camps:
        log.info("no active campaigns yet (add one in Telegram: /addcampaign). Checking again in 5 min.")
        time.sleep(300)
        return
    stop = threading.Event()
    lock = threading.Lock()
    params = detector_params()
    detectors = {name: SpikeDetector(name, params) for name in camps}
    CHATLOG.mkdir(parents=True, exist_ok=True)
    logs = {name: open(CHATLOG / f"{name}_{time.strftime('%Y-%m-%d')}.jsonl", "a", encoding="utf-8") for name in camps}

    def on_message(platform, channel, t, user, text):
        with lock:
            d = detectors.get(channel)
            if d:
                d.feed(t, user, text)
                logs[channel].write(json.dumps({"t": round(t, 3), "user": user, "text": text}, ensure_ascii=False) + "\n")

    TwitchIRCReader(list(camps), on_message, stop).start()
    recorders = []
    if not a.chat_only:
        for name in camps:
            r = RingRecorder(name, BUFFER, stop, source="twitch", quality=QUALITY, segment_s=SEG,
                             buffer_minutes=env_float("RT_BUFFER_MIN", 10))
            r.start()
            recorders.append(r)
    log.info("watching chat of %d campaign streamer(s): %s", len(camps), ", ".join(camps))

    signature = sorted(camps)
    last_check = time.time()
    try:
        while True:
            now = time.time()
            with lock:
                events = [ev for d in detectors.values() if (ev := d.tick(now))]
                for f in logs.values():
                    f.flush()
            for ev in events:
                log.info("[%s] SPIKE z=%.1f rate=%.1f/s (normal %.1f) chatters=%d top=%s",
                         ev.channel, ev.z, ev.rate, ev.baseline, ev.unique_chatters, ev.top_tokens[:3])
                if not a.chat_only:
                    threading.Thread(target=handle_spike, args=(ev, camps[ev.channel]), daemon=True).start()
            if now - last_check > 300:  # campaigns added/paused? restart to pick them up
                last_check = now
                if sorted(c["streamer"].lower() for c in campaigns()) != signature:
                    log.info("campaign list changed, restarting")
                    break
            time.sleep(max(0.0, 1.0 - (time.time() - now)))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for r in recorders:
            r.terminate()
        for f in logs.values():
            f.close()


if __name__ == "__main__":
    main()
