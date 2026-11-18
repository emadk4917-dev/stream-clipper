"""24/7 live clipper: watches every active campaign streamer and sends clips for approval.

  python -m clipper.live            # run forever
  python -m clipper.live --once     # one pass over all streamers (for testing)

Every POLL_MIN minutes: which campaign streamers are live? For each one, analyse the part
of the stream since the last check, let Claude judge the spikes, render the good ones and
send them to Telegram for a one-tap approval.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import traceback
from concurrent.futures import ProcessPoolExecutor

from . import cli
from . import download as D
from . import judge as J
from . import approvals as Q
from .config import DATA, db, env, env_float, env_int, now

POLL_MIN = env_int("POLL_MIN", 10)          # how often to check each live streamer
FIRST_WINDOW_MIN = env_int("FIRST_WINDOW_MIN", 30)  # how far back to look the first time we see a stream
MIN_SCORE = env_float("MIN_SCORE", 5)       # Claude score needed to send a clip for approval
WORKERS = env_int("WORKERS", 3)             # streamers processed in parallel
TOP = env_int("CANDIDATES_PER_PASS", 6)
WHISPER = env("WHISPER_MODEL", "small")
KEEP_RUN_HOURS = env_int("KEEP_RUN_HOURS", 72)


def log(*a):
    print(time.strftime("%H:%M:%S"), "[live]", *a, flush=True)


def is_live(login: str) -> bool:
    r = subprocess.run(["yt-dlp", "--no-warnings", "--print", "is_live", f"https://www.twitch.tv/{login}"],
                       capture_output=True, text=True, env=D._env(), timeout=60)
    return r.returncode == 0 and "True" in r.stdout


def process(campaign: dict) -> int:
    """One pass over one streamer. Returns how many clips were sent for approval."""
    login = campaign["streamer"]
    vurl, _, title = cli.latest_vod(login)
    info = D.fetch_info(vurl)
    vod = str(info.get("id"))
    dur = float(info.get("duration") or 0)
    with db() as con:
        row = con.execute("SELECT until FROM progress WHERE vod=?", (vod,)).fetchone()
    until = row["until"] if row else max(0.0, dur - FIRST_WINDOW_MIN * 60)
    start, end = max(0.0, until - 120), dur - 20  # 2 min overlap so moments on the boundary aren't lost
    if end - start < 180:
        return 0
    log(f"{login}: analysing {start / 60:.0f}-{end / 60:.0f} min of '{title[:50]}'")
    run_dir = cli.analyze(vurl, None, start, end, None, TOP, WHISPER)
    cands = json.loads((run_dir / "candidates.json").read_text())
    info = json.loads((run_dir / "info.json").read_text())  # includes source URL + audio offset
    with db() as con:
        seen = {r["peak"] for r in con.execute("SELECT peak FROM seen WHERE vod=?", (vod,))}
    fresh = [c for c in cands if c["peak"] >= until - 30 and not any(abs(c["peak"] - s) < 45 for s in seen)]
    picks = J.judge(fresh, login, campaign.get("rules") or "", game=title)
    with db() as con:
        con.executemany("INSERT OR IGNORE INTO seen(vod, peak) VALUES(?, ?)", [(vod, c["peak"]) for c in fresh])
    sent = 0
    for p in sorted(picks, key=lambda x: -x["score"]):
        if p["score"] < MIN_SCORE:
            continue
        try:
            name = f"{int(p['start'])}_{cli.slug(p['title'], 30)}"
            mp4, _, desc = cli.render_one(run_dir, info, p, name, WHISPER)
            tt = None
            if p.get("tiktok_end", 0) - p.get("tiktok_start", 0) >= 61:
                tclip = {**p, "start": p["tiktok_start"], "end": p["tiktok_end"]}
                tt, _, _ = cli.render_one(run_dir, info, tclip, name + "_tiktok", WHISPER, max_len=90)
            cid = Q.add_clip(campaign, vod, p, mp4, tt, desc)
            Q.request_approval(cid)
            sent += 1
        except Exception:
            log(f"{login}: render failed\n{traceback.format_exc()}")
    with db() as con:
        con.execute("INSERT OR REPLACE INTO progress(vod, until) VALUES(?, ?)", (vod, end))
    (run_dir / "audio.m4a").unlink(missing_ok=True)  # the big file; clips stay
    log(f"{login}: {len(fresh)} moments, {len(picks)} judged, {sent} sent for approval")
    return sent


def _safe_process(campaign: dict) -> int:
    try:
        return process(campaign)
    except Exception:
        log(f"{campaign['streamer']}: failed\n{traceback.format_exc()}")
        return 0


def cleanup() -> None:
    """Delete old run folders so the disk doesn't fill up (keeps queued/approved clip files)."""
    with db() as con:
        keep = {r["path"].split("/clips/")[0] for r in con.execute(
            "SELECT path FROM clips WHERE status IN ('pending','approved','sent')")}
    for d in (cli.RUNS.glob("*") if cli.RUNS.exists() else []):
        if d.is_dir() and str(d) not in keep and now() - d.stat().st_mtime > KEEP_RUN_HOURS * 3600:
            shutil.rmtree(d, ignore_errors=True)


def campaigns() -> list[dict]:
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM campaigns WHERE active=1")]


def one_pass() -> int:
    camps = campaigns()
    if not camps:
        log("no active campaigns. Add one in Telegram: /addcampaign")
        return 0
    live = []
    for c in camps:
        try:
            if is_live(c["streamer"]):
                live.append(c)
        except Exception:
            log(f"{c['streamer']}: live check failed")
    log(f"live now: {', '.join(c['streamer'] for c in live) or 'nobody'}")
    if not live:
        return 0
    with ProcessPoolExecutor(max_workers=max(1, WORKERS)) as ex:
        return sum(ex.map(_safe_process, live))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    while True:
        t0 = time.time()
        try:
            one_pass()
            cleanup()
        except Exception:
            log(traceback.format_exc())
        if args.once:
            break
        time.sleep(max(30, POLL_MIN * 60 - (time.time() - t0)))


if __name__ == "__main__":
    main()
