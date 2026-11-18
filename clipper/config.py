"""Settings (from .env) and the shared SQLite database."""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_env() -> None:
    """Read KEY=VALUE lines from .env into the environment (real env vars win)."""
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def env_int(key: str, default: int) -> int:
    try:
        return int(env(key, str(default)))
    except ValueError:
        return default


def env_float(key: str, default: float) -> float:
    try:
        return float(env(key, str(default)))
    except ValueError:
        return default


SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns(
  id INTEGER PRIMARY KEY, name TEXT, streamer TEXT, rate REAL, url TEXT,
  account_set TEXT, rules TEXT, active INTEGER DEFAULT 1, created_at REAL);
CREATE TABLE IF NOT EXISTS clips(
  id TEXT PRIMARY KEY, campaign_id INTEGER, streamer TEXT, vod TEXT, peak REAL,
  start REAL, "end" REAL, title TEXT, hook TEXT, description TEXT, tags TEXT,
  score REAL, reason TEXT, path TEXT, tiktok_path TEXT, status TEXT,
  tg_message_id INTEGER, account_set TEXT, slot_at REAL, created_at REAL,
  posted_at REAL, links TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS seen(vod TEXT, peak REAL, PRIMARY KEY(vod, peak));
CREATE TABLE IF NOT EXISTS progress(vod TEXT PRIMARY KEY, until REAL);
CREATE TABLE IF NOT EXISTS trends(key TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT,
  velocity REAL, seen_at REAL, sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


def db() -> sqlite3.Connection:
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DATA / "clipper.db", timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def kv_get(key: str, default: str = "") -> str:
    with db() as con:
        row = con.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default


def kv_set(key: str, value: str) -> None:
    with db() as con:
        con.execute("INSERT OR REPLACE INTO kv(k, v) VALUES(?, ?)", (key, value))


def now() -> float:
    return time.time()
