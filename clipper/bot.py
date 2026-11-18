"""Telegram control bot: approvals, posting reminders, campaigns, daily scout report.

  python -m clipper.bot

Commands (send in your Telegram chat with the bot):
  /addcampaign Name | twitch_login | rate_per_1k | campaign_url | account set | rules
  /campaigns            list campaigns       /pause ID  /resume ID
  /queue                what's waiting       /stats     clips by status
  /link CLIP_ID URL     log a posted link for campaign submission
  /help
"""
from __future__ import annotations

import datetime
import time
import traceback

from . import approvals as Q
from . import telegram as tg
from .config import db, env_int, kv_get, kv_set, now

REPORT_HOUR = env_int("REPORT_HOUR", 9)  # daily scout report, local time

HELP = __doc__.split("Commands", 1)[1]


def log(*a):
    print(time.strftime("%H:%M:%S"), "[bot]", *a, flush=True)


def cmd_addcampaign(arg: str) -> str:
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) < 2 or not parts[1]:
        return ("Format:\n/addcampaign Name | twitch_login | rate | url | account set | rules\n"
                "e.g. /addcampaign Kai GTA | kaicenat | 1.5 | https://... | Set 1 | tag @kaicenat, YT+TT only")
    name, login = parts[0], parts[1].lower().lstrip("@").replace("https://www.twitch.tv/", "")
    rate = float(parts[2]) if len(parts) > 2 and parts[2] else 0
    url = parts[3] if len(parts) > 3 else ""
    aset = parts[4] if len(parts) > 4 and parts[4] else "Set 1"
    rules = parts[5] if len(parts) > 5 else ""
    with db() as con:
        cur = con.execute("INSERT INTO campaigns(name, streamer, rate, url, account_set, rules, created_at) "
                          "VALUES(?,?,?,?,?,?,?)", (name, login, rate, url, aset, rules, now()))
    return f"✅ Campaign #{cur.lastrowid} added. Watching twitch.tv/{login} → {aset}."


def cmd_campaigns() -> str:
    with db() as con:
        rows = con.execute("""SELECT k.*, (SELECT COUNT(*) FROM clips c WHERE c.campaign_id=k.id AND c.status='posted') posted
                              FROM campaigns k ORDER BY active DESC, id""").fetchall()
    if not rows:
        return "No campaigns yet. /addcampaign to add one."
    return "\n".join(f"{'🟢' if r['active'] else '⏸'} #{r['id']} {r['name']} · twitch/{r['streamer']} · "
                     f"${r['rate']}/1k · {r['account_set']} · {r['posted']} posted" for r in rows)


def set_active(arg: str, active: int) -> str:
    with db() as con:
        con.execute("UPDATE campaigns SET active=? WHERE id=?", (active, arg.strip().lstrip("#")))
    return "🟢 Resumed" if active else "⏸ Paused"


def cmd_stats() -> str:
    with db() as con:
        rows = con.execute("SELECT status, COUNT(*) n FROM clips GROUP BY status").fetchall()
        today = con.execute("SELECT COUNT(*) n FROM clips WHERE status='posted' AND posted_at>?",
                            (now() - 86400,)).fetchone()["n"]
    return ("📊 " + " · ".join(f"{r['status']}: {r['n']}" for r in rows)) + f"\nPosted in last 24h: {today}" \
        if rows else "📊 No clips yet."


def cmd_queue() -> str:
    with db() as con:
        rows = con.execute("SELECT id, streamer, title, status, slot_at FROM clips WHERE status IN "
                           "('pending','approved') ORDER BY slot_at IS NULL, slot_at").fetchall()
    if not rows:
        return "Queue is empty."
    return "\n".join(f"{'⏳' if r['status'] == 'pending' else '🗓 ' + Q.fmt_time(r['slot_at'])} "
                     f"{r['id']} {r['streamer']}: {r['title'][:45]}" for r in rows[:30])


def daily_report() -> None:
    """Campaign scout: once a day, the numbers + a nudge to grab new campaigns before budgets drain."""
    tg.send_text("🔎 DAILY SCOUT\n" + cmd_stats() + "\n\n" + cmd_campaigns() +
                 "\n\nTo do now:\n• Check Content Rewards / Whop for NEW gaming campaigns (budgets drain in days)"
                 " → https://contentrewards.com\n• Pause campaigns that are out of budget (/pause ID)"
                 "\n• Submit yesterday's post links for payment")


def handle(u: dict) -> None:
    allowed = set(tg.chat_ids())
    if "callback_query" in u:
        q = u["callback_query"]
        chat = str(q["message"]["chat"]["id"])
        if chat not in allowed:
            return
        action, cid = q["data"].split(":", 1)
        msg = {"ok": Q.approve, "no": Q.reject, "posted": Q.mark_posted}[action](cid)
        tg.call("answerCallbackQuery", callback_query_id=q["id"], text=msg[:190])
        tg.call("editMessageReplyMarkup", chat_id=chat, message_id=q["message"]["message_id"],
                reply_markup={"inline_keyboard": []})
        tg.send_text(msg, chat=chat)
        return
    m = u.get("message") or {}
    chat = str(m.get("chat", {}).get("id", ""))
    text = (m.get("text") or "").strip()
    if not text.startswith("/"):
        return
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if chat not in allowed:
        # tell a new user their chat id so it can go in .env
        tg.send_text(f"👋 Your chat id is {chat}. Put it in .env as TELEGRAM_CHAT_IDS and restart the bot.", chat=chat)
        return
    reply = {
        "/start": lambda: "Clipper bot online ✅\n" + HELP,
        "/help": lambda: HELP,
        "/addcampaign": lambda: cmd_addcampaign(arg),
        "/campaigns": cmd_campaigns,
        "/pause": lambda: set_active(arg, 0),
        "/resume": lambda: set_active(arg, 1),
        "/stats": cmd_stats,
        "/queue": cmd_queue,
        "/link": lambda: Q.add_link(*arg.split(None, 1)) if len(arg.split()) == 2 else "Format: /link CLIP_ID URL",
        "/report": lambda: (daily_report(), "")[1],
    }.get(cmd, lambda: "Unknown command. /help")()
    if reply:
        tg.send_text(reply, chat=chat)


def main():
    offset = int(kv_get("tg_offset", "0") or 0)
    log("bot running")
    while True:
        try:
            for u in tg.updates(offset):
                offset = u["update_id"] + 1
                kv_set("tg_offset", str(offset))
                try:
                    handle(u)
                except Exception:
                    log(traceback.format_exc())
            n = Q.send_due_posts()
            if n:
                log(f"sent {n} posting packs")
            local = datetime.datetime.now(Q.TZ)
            today = local.strftime("%Y-%m-%d")
            if local.hour >= REPORT_HOUR and kv_get("last_report") != today:
                daily_report()
                kv_set("last_report", today)
        except Exception:
            log(traceback.format_exc())
            time.sleep(10)


if __name__ == "__main__":
    main()
