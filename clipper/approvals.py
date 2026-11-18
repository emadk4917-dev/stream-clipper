"""Approval queue + publisher: clips wait for a tap on Telegram, then get a posting slot.

Flow: pending -> (tap Approve) -> approved (slot assigned) -> due: posting pack sent to
phone -> (tap Posted) -> posted. Reject at any point -> rejected.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from . import telegram as tg
from .config import db, env, env_float, env_int, now

TZ = ZoneInfo(env("TIMEZONE", "Europe/London"))
SPACING_MIN = env_int("POST_SPACING_MIN", 150)       # gap between posts on one account set
POST_HOURS = env("POST_HOURS", "8-23")                # local hours we post in
AUTO_APPROVE = env_float("AUTO_APPROVE_SCORE", 11)    # 11 = never auto-approve (week 1: a human taps everything)


def _in_hours(ts: float) -> float:
    """Push a timestamp into the posting window (e.g. 08:00-23:00 local)."""
    lo, hi = (int(x) for x in POST_HOURS.split("-"))
    t = dt.datetime.fromtimestamp(ts, TZ)
    if t.hour < lo:
        t = t.replace(hour=lo, minute=0, second=0)
    elif t.hour >= hi:
        t = (t + dt.timedelta(days=1)).replace(hour=lo, minute=0, second=0)
    return t.timestamp()


def fmt_time(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, TZ).strftime("%a %H:%M")


def add_clip(campaign: dict, vod: str, pick: dict, path: Path, tiktok_path: Path | None,
             description: str) -> str | None:
    """Queue a clip. pick["peak"] is wall-clock time of the moment; returns None for a duplicate."""
    cid = uuid.uuid4().hex[:8]
    with db() as con:
        if pick.get("peak") and con.execute(
                "SELECT 1 FROM clips WHERE streamer=? AND ABS(peak-?)<60", (campaign["streamer"], pick["peak"])).fetchone():
            return None  # the real-time and catch-up clippers both caught this moment
        con.execute("""INSERT INTO clips(id, campaign_id, streamer, vod, peak, start, "end", title, hook, description,
                       tags, score, reason, path, tiktok_path, status, account_set, created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?, ?)""",
                    (cid, campaign["id"], campaign["streamer"], vod, pick.get("peak"), pick["start"], pick["end"],
                     pick["title"], pick.get("hook", ""), description, json.dumps(pick.get("tags", [])),
                     pick["score"], pick.get("reason", ""), str(path), str(tiktok_path or ""),
                     campaign.get("account_set") or "Set 1", now()))
    return cid


def get(cid: str) -> dict | None:
    with db() as con:
        r = con.execute("SELECT c.*, k.name AS campaign, k.rules AS rules FROM clips c "
                        "LEFT JOIN campaigns k ON k.id = c.campaign_id WHERE c.id=?", (cid,)).fetchone()
        return dict(r) if r else None


def request_approval(cid: str) -> None:
    c = get(cid)
    fire = "🔥" if c["score"] >= 8 else "👍"
    cap = (f"{fire} {c['score']:.0f}/10 · {c['streamer']} · {c['campaign']}\n"
           f"📝 {c['title']}\n" + (f"🪝 {c['hook']}\n" if c["hook"] else "") +
           f"💭 {c['reason']}\n⏱ {c['end'] - c['start']:.0f}s" + (" (+ TikTok 61s+ cut)" if c["tiktok_path"] else ""))
    if c["score"] >= AUTO_APPROVE:
        approve(cid, note="auto-approved (score)")
        tg.send_video(Path(c["path"]), "🤖 AUTO-APPROVED\n" + cap)
        return
    msgs = tg.send_video(Path(c["path"]), cap,
                         markup=tg.buttons([("✅ Approve", f"ok:{cid}"), ("❌ Reject", f"no:{cid}")]))
    with db() as con:
        con.execute("UPDATE clips SET tg_message_id=? WHERE id=?", (msgs[0]["message_id"] if msgs else None, cid))


def approve(cid: str, note: str = "") -> str:
    c = get(cid)
    if not c or c["status"] != "pending":
        return f"already {c['status'] if c else 'gone'}"
    with db() as con:
        last = con.execute("SELECT MAX(slot_at) m FROM clips WHERE account_set=? AND status IN ('approved','sent','posted')",
                           (c["account_set"],)).fetchone()["m"] or 0
        slot = _in_hours(max(now() + 120, last + SPACING_MIN * 60))
        con.execute("UPDATE clips SET status='approved', slot_at=? WHERE id=?", (slot, cid))
    return f"✅ Approved → {c['account_set']} at {fmt_time(slot)} {note}".strip()


def reject(cid: str) -> str:
    with db() as con:
        con.execute("UPDATE clips SET status='rejected' WHERE id=? AND status IN ('pending','approved','sent')", (cid,))
    return "❌ Rejected"


def mark_posted(cid: str) -> str:
    with db() as con:
        con.execute("UPDATE clips SET status='posted', posted_at=? WHERE id=?", (now(), cid))
    return (f"🎉 Logged as posted. Now submit the post links to the campaign, then send:\n"
            f"/link {cid} <url>  (one per platform)")


def add_link(cid: str, url: str) -> str:
    with db() as con:
        con.execute("UPDATE clips SET links = TRIM(links || ' ' || ?) WHERE id=?", (url, cid))
    return "🔗 Saved."


def send_due_posts() -> int:
    """Publisher: when a clip's slot arrives, send the ready-to-post pack to the phone."""
    with db() as con:
        due = [r["id"] for r in con.execute("SELECT id FROM clips WHERE status='approved' AND slot_at<=?", (now(),))]
    for cid in due:
        c = get(cid)
        tags = " ".join("#" + t.lstrip("#") for t in json.loads(c["tags"] or "[]"))
        tg.send_text(f"📤 POST NOW → {c['account_set']} ({c['campaign']})\n\n"
                     f"▶️ YOUTUBE SHORTS\nTitle: {c['title']}\nDescription:\n{c['description']}\n\n"
                     f"🎵 TIKTOK / 📸 INSTAGRAM caption:\n{c['title']} {tags} #{c['streamer']}\n\n"
                     f"Campaign rules: {c['rules'] or 'none'}")
        tg.send_document(Path(c["path"]), f"Shorts/Reels file · {cid}")
        if c["tiktok_path"] and Path(c["tiktok_path"]).exists():
            tg.send_document(Path(c["tiktok_path"]), f"TikTok 61s+ file (Creator Rewards) · {cid}")
        tg.send_text(f"Tap when it's live on all platforms ({cid}):",
                     markup=tg.buttons([("✅ Posted", f"posted:{cid}"), ("❌ Skip", f"no:{cid}")]))
        with db() as con:
            con.execute("UPDATE clips SET status='sent' WHERE id=?", (cid,))
    return len(due)
