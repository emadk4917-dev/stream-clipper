"""Command line: analyze a stream, then render the chosen moments into Shorts.

  python -m clipper analyze <twitch/youtube/kick URL or file> [--from 1:00:00 --to 3:00:00]
  python -m clipper render runs/<run> --picks runs/<run>/picks.json
  python -m clipper auto <URL> --clips 8          # no review, machine picks everything
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

from . import candidates as C
from . import download as D
from . import signals as S
from .render import find_offset, render_clip
from .transcribe import transcribe_range

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


def log(*a):
    print("[clipper]", *a, flush=True)


def parse_time(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    parts = [float(p) for p in str(v).strip().split(":")]
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s or "").strip("-").lower()
    return s[:n].strip("-") or "clip"


# ------------------------------------------------------------------ analyze
def analyze(src: str, out: Path | None, t_from=None, t_to=None, chat=None, top: int = 25, model: str = "small") -> Path:
    t0 = time.time()
    log("reading stream info…")
    info = D.fetch_info(src)
    channel = info.get("uploader") or info.get("channel") or "stream"
    run_dir = out or RUNS / f"{slug(channel, 20)}_{slug(str(info.get('id')), 20)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    a, b = parse_time(t_from), parse_time(t_to)
    info.update({"source": src, "audio_offset": a or 0.0, "range": [a, b]})
    (run_dir / "info.json").write_text(json.dumps(info, indent=2))
    log(f"'{info.get('title')}' by {channel}, {int(info.get('duration') or 0) // 60} min")

    log("downloading audio (this is the slow part for long streams)…")
    audio = D.download_audio(src, run_dir, a, b)
    log("measuring loudness every second…")
    loud = S.loudness_per_second(audio)
    chat_arr = S.chat_per_second(Path(chat), len(loud)) if chat else None
    if chat_arr is not None and a:
        chat_arr = S.chat_per_second(Path(chat), len(loud) + int(a))[int(a):]
    curve = S.hype_curve(loud, chat_arr)
    np.save(run_dir / "hype.npy", curve)

    log(f"finding the top {top} moments and transcribing them…")
    cands = C.build_candidates(audio, curve, n=top, offset=a or 0.0, model=model, log=log)
    (run_dir / "candidates.json").write_text(json.dumps(cands, indent=1))
    (run_dir / "candidates.md").write_text(C.candidates_markdown(info, cands))
    log(f"done in {time.time() - t0:.0f}s → {run_dir / 'candidates.md'}")
    return run_dir


# ------------------------------------------------------------------ picks
def auto_picks(run_dir: Path, n: int) -> dict:
    cands = json.loads((run_dir / "candidates.json").read_text())
    info = json.loads((run_dir / "info.json").read_text())
    channel = info.get("uploader") or info.get("channel") or ""
    clips = []
    for c in cands[:n]:
        quote = " ".join(w["w"] for w in c["words"] if c["suggested_start"] <= w["s"] <= c["suggested_end"])
        quote = " ".join(quote.split()[:9]).strip(" ,.")
        clips.append({"id": c["id"], "start": c["suggested_start"], "end": c["suggested_end"],
                      "title": f"{channel}: \"{quote}…\"" if quote else f"{channel} moment {c['peak_ts']}",
                      "hook": "", "layout": "auto"})
    return {"clips": clips}


def words_for(run_dir: Path, audio: Path, start: float, end: float, audio_offset: float, model: str) -> list[dict]:
    for c in json.loads((run_dir / "candidates.json").read_text()):
        if c["context_start"] <= start and end <= c["context_end"]:
            return c["words"]
    log(f"  transcribing {C.fmt_ts(start)}–{C.fmt_ts(end)} (outside analysed context)")
    ws = transcribe_range(audio, start - audio_offset - 1, end - audio_offset + 1, model=model)
    return [{**w, "s": w["s"] + audio_offset, "e": w["e"] + audio_offset} for w in ws]


def description(info: dict, clip: dict) -> str:
    channel = info.get("uploader") or info.get("channel") or ""
    url = info.get("webpage_url") or ""
    chan_url = f"https://twitch.tv/{info.get('uploader_id') or channel}" if "twitch" in url else url
    tags = clip.get("tags") or [slug(channel, 30).replace("-", ""), "twitch", "streamer", "clips", "funny"]
    body = clip.get("description") or clip["title"]
    return (f"{body}\n\n🎥 Full credit: {channel} → {chan_url}\n"
            f"Clipped from the stream on {time.strftime('%d %b %Y', time.gmtime(info['timestamp'])) if info.get('timestamp') else 'stream'}.\n\n"
            + " ".join("#" + t.lstrip("#") for t in ["shorts", *tags]))


# ------------------------------------------------------------------ render
def render(run_dir: Path, picks_path: Path | None = None, auto_n: int | None = None, captions: bool = True,
           model: str = "small") -> list[Path]:
    info = json.loads((run_dir / "info.json").read_text())
    audio = run_dir / "audio.m4a"
    picks = auto_picks(run_dir, auto_n or 8) if picks_path is None else json.loads(Path(picks_path).read_text())
    out_dir = run_dir / "clips"
    out_dir.mkdir(exist_ok=True)
    rows, made = [], []
    cam_file = run_dir / "facecam.json"
    known_cam = json.loads(cam_file.read_text()) if cam_file.exists() else None
    for i, clip in enumerate(picks["clips"], 1):
        start, end = parse_time(clip["start"]), parse_time(clip["end"])
        if end - start > 59:
            log(f"  clip {i} is {end - start:.0f}s, trimming to 59s (keep Shorts under a minute for best reach)")
            end = start + 59
        name = f"{i:02d}_{slug(clip['title'], 40)}"
        log(f"clip {i}/{len(picks['clips'])}: {C.fmt_ts(start)}–{C.fmt_ts(end)} {clip['title']}")
        sec_start = max(0.0, start - 8)
        section = D.download_video_section(info["source"], sec_start, end + 8, out_dir / "src" / f"{name}.mp4")
        real = find_offset(section, audio, sec_start, info.get("audio_offset", 0.0))
        local_s, local_e = start - real, end - real
        words = words_for(run_dir, audio, start, end, info.get("audio_offset", 0.0), model) if captions else []
        mp4 = out_dir / f"{name}.mp4"
        lay = render_clip(section, local_s, local_e, words, start, clip.get("hook", ""), mp4,
                          clip.get("layout"), clip.get("cam"), captions, known_cam)
        if lay["layout"] == "split" and not known_cam:
            known_cam = lay["cam"]
            cam_file.write_text(json.dumps(known_cam))
        desc = description(info, clip)
        (out_dir / f"{name}.txt").write_text(f"TITLE:\n{clip['title'][:100]}\n\nDESCRIPTION:\n{desc}\n")
        rows.append({"file": mp4.name, "title": clip["title"][:100], "description": desc,
                     "start": C.fmt_ts(start), "length_s": round(end - start), "layout": lay["layout"]})
        made.append(mp4)
        log(f"  ✓ {mp4.name} ({lay['layout']} layout)")
    with open(out_dir / "upload_sheet.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["file"])
        w.writeheader()
        w.writerows(rows)
    log(f"{len(made)} clips ready in {out_dir}")
    return made


def main(argv=None):
    p = argparse.ArgumentParser(prog="clipper", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze", help="find the best moments in a stream")
    a.add_argument("src")
    a.add_argument("--out", type=Path)
    a.add_argument("--from", dest="t_from", help="only look from this time, e.g. 1:30:00")
    a.add_argument("--to", dest="t_to", help="only look until this time")
    a.add_argument("--chat", help="optional chat export (TwitchDownloader JSON) for better picks")
    a.add_argument("--top", type=int, default=25)
    a.add_argument("--model", default="small", help="whisper model: tiny/base/small/medium")
    r = sub.add_parser("render", help="make Shorts from picks")
    r.add_argument("run_dir", type=Path)
    r.add_argument("--picks", type=Path)
    r.add_argument("--auto", type=int, help="skip review: render the top N machine picks")
    r.add_argument("--no-captions", action="store_true")
    r.add_argument("--model", default="small")
    u = sub.add_parser("auto", help="analyze + render top N with no review")
    u.add_argument("src")
    u.add_argument("--clips", type=int, default=8)
    u.add_argument("--from", dest="t_from")
    u.add_argument("--to", dest="t_to")
    u.add_argument("--chat")
    u.add_argument("--model", default="small")
    args = p.parse_args(argv)
    if args.cmd == "analyze":
        analyze(args.src, args.out, args.t_from, args.t_to, args.chat, args.top, args.model)
    elif args.cmd == "render":
        render(args.run_dir, args.picks, args.auto, not args.no_captions, args.model)
    else:
        rd = analyze(args.src, None, args.t_from, args.t_to, args.chat, max(args.clips * 2, 12), args.model)
        render(rd, None, args.clips, True, args.model)


if __name__ == "__main__":
    sys.exit(main())
