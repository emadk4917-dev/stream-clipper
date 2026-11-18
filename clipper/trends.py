"""Trend radar: what's blowing up around GTA 6 right now, turned into filming briefs.

  python -m clipper.trends          # run forever (every TREND_MIN minutes)
  python -m clipper.trends --once

Sources: YouTube search (fresh Shorts, ranked by views per hour; needs YOUTUBE_API_KEY)
and Reddit (rising posts). Claude groups the fast movers into 3-5 briefs: what's trending,
where, how to film it, our angle, post-by time, risk flag. Sent to Telegram.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import traceback

import requests

from . import telegram as tg
from .config import db, env, env_int, now
from .judge import JUDGE_MODEL, _ask

KEYWORDS = [k.strip() for k in env("TREND_KEYWORDS", "GTA 6,GTA VI").split(",") if k.strip()]
SUBREDDITS = [s.strip() for s in env("TREND_SUBREDDITS", "GTA6,GTA,GTAVI").split(",") if s.strip()]
TREND_MIN = env_int("TREND_MIN", 60)
UA = {"User-Agent": "clipper-trend-radar/1.0"}


def log(*a):
    print(time.strftime("%H:%M:%S"), "[trends]", *a, flush=True)


def youtube_fast_movers() -> list[dict]:
    key = env("YOUTUBE_API_KEY")
    if not key:
        return []
    since = (dt.datetime.utcnow() - dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = []
    for q in KEYWORDS:
        s = requests.get("https://www.googleapis.com/youtube/v3/search", timeout=30, params={
            "part": "snippet", "q": q, "type": "video", "order": "viewCount", "videoDuration": "short",
            "publishedAfter": since, "maxResults": 25, "key": key}).json()
        ids = [i["id"]["videoId"] for i in s.get("items", [])]
        if not ids:
            continue
        v = requests.get("https://www.googleapis.com/youtube/v3/videos", timeout=30, params={
            "part": "statistics,snippet", "id": ",".join(ids), "key": key}).json()
        for it in v.get("items", []):
            pub = dt.datetime.fromisoformat(it["snippet"]["publishedAt"].replace("Z", "+00:00")).timestamp()
            hours = max(0.5, (now() - pub) / 3600)
            views = int(it["statistics"].get("viewCount", 0))
            items.append({"key": "yt:" + it["id"], "source": f"YouTube · {it['snippet']['channelTitle']}",
                          "title": it["snippet"]["title"], "url": f"https://youtube.com/shorts/{it['id']}",
                          "velocity": views / hours, "detail": f"{views:,} views in {hours:.0f}h"})
    return items


def reddit_rising() -> list[dict]:
    items = []
    for sub in SUBREDDITS:
        try:
            r = requests.get(f"https://www.reddit.com/r/{sub}/rising.json?limit=20", headers=UA, timeout=30).json()
        except Exception:
            continue
        for p in r.get("data", {}).get("children", []):
            d = p["data"]
            hours = max(0.25, (now() - d["created_utc"]) / 3600)
            items.append({"key": "rd:" + d["id"], "source": f"Reddit r/{sub}", "title": d["title"],
                          "url": "https://reddit.com" + d["permalink"],
                          "velocity": (d["score"] + 2 * d["num_comments"]) / hours,
                          "detail": f"{d['score']} upvotes, {d['num_comments']} comments in {hours:.1f}h"})
    return items


BRIEF_SCHEMA = {
    "type": "object",
    "properties": {"briefs": {"type": "array", "items": {
        "type": "object",
        "properties": {k: {"type": "string"} for k in
                       ("trending", "where", "how_to_film", "our_angle", "post_by", "risk_flag", "sources")},
        "required": ["trending", "where", "how_to_film", "our_angle", "post_by", "risk_flag", "sources"],
        "additionalProperties": False}}},
    "required": ["briefs"], "additionalProperties": False,
}

BRIEF_RULES = """You run the trend desk for a GTA 6 short-form gameplay page. From the fast-moving
posts below, find 3-5 things our player can RECREATE in their own copy of the game and post within
hours (we never repost other people's footage). Group posts about the same thing. Prefer: rising
topics several creators are covering, "can you...?" questions, glitches that are funny (never money
glitches or exploits), hidden spots, comparisons, and real news (confirm news only from Rockstar).
Skip story spoilers, leaks, AI fakes. Each brief: trending (what + evidence), where (in-game place),
how_to_film (specific shots, first-person/third-person, time of day), our_angle (the title/hook),
post_by (e.g. "within 2 hours"), risk_flag (none / spoiler / exploit / unverified), sources (URLs)."""


def run_once() -> int:
    items = youtube_fast_movers() + reddit_rising()
    with db() as con:
        sent = {r["key"] for r in con.execute("SELECT key FROM trends WHERE sent=1")}
    fresh = sorted([i for i in items if i["key"] not in sent], key=lambda i: -i["velocity"])[:30]
    if not fresh:
        log("nothing new")
        return 0
    lines = "\n".join(f"- [{i['source']}] {i['title']} ({i['detail']}) {i['url']}" for i in fresh)
    out = _ask(JUDGE_MODEL, BRIEF_RULES, "Fast movers right now:\n" + lines, BRIEF_SCHEMA, "medium")
    briefs = (out or {}).get("briefs", [])
    for b in briefs:
        tg.send_text(f"📈 TREND BRIEF\n🔥 Trending: {b['trending']}\n📍 Where: {b['where']}\n"
                     f"🎥 How to film: {b['how_to_film']}\n💡 Our angle: {b['our_angle']}\n"
                     f"⏰ Post by: {b['post_by']}\n⚠️ Risk: {b['risk_flag']}\n🔗 {b['sources']}")
    with db() as con:
        con.executemany("INSERT OR REPLACE INTO trends(key, source, title, url, velocity, seen_at, sent) "
                        "VALUES(?,?,?,?,?,?,1)",
                        [(i["key"], i["source"], i["title"], i["url"], i["velocity"], now()) for i in fresh])
    log(f"{len(fresh)} fast movers → {len(briefs)} briefs")
    return len(briefs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    while True:
        try:
            run_once()
        except Exception:
            log(traceback.format_exc())
        if args.once:
            break
        time.sleep(TREND_MIN * 60)


if __name__ == "__main__":
    main()
