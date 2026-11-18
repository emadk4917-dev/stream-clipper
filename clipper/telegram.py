"""Minimal Telegram Bot API client (send messages/videos with buttons, poll for taps)."""
from __future__ import annotations

import json
from pathlib import Path

import requests

from .config import env

API = "https://api.telegram.org/bot{token}/{method}"


def _token() -> str:
    t = env("TELEGRAM_BOT_TOKEN")
    if not t:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in .env")
    return t


def call(method: str, files: dict | None = None, timeout: int = 60, **params) -> dict:
    data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items() if v is not None}
    r = requests.post(API.format(token=_token(), method=method), data=data, files=files, timeout=timeout)
    out = r.json()
    if not out.get("ok"):
        raise RuntimeError(f"telegram {method} failed: {out.get('description')}")
    return out["result"]


def chat_ids() -> list[str]:
    return [c.strip() for c in env("TELEGRAM_CHAT_IDS").split(",") if c.strip()]


def buttons(*rows: list[tuple[str, str]]) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def send_text(text: str, chat: str | None = None, markup: dict | None = None) -> list[dict]:
    sent = []
    for c in ([chat] if chat else chat_ids()):
        sent.append(call("sendMessage", chat_id=c, text=text[:4000], reply_markup=markup,
                         disable_web_page_preview=True))
    return sent


def send_video(path: Path, caption: str, chat: str | None = None, markup: dict | None = None) -> list[dict]:
    sent = []
    for c in ([chat] if chat else chat_ids()):
        with open(path, "rb") as f:
            sent.append(call("sendVideo", files={"video": (Path(path).name, f, "video/mp4")}, timeout=300,
                             chat_id=c, caption=caption[:1024], reply_markup=markup, supports_streaming=True))
    return sent


def send_document(path: Path, caption: str = "", chat: str | None = None) -> list[dict]:
    sent = []
    for c in ([chat] if chat else chat_ids()):
        with open(path, "rb") as f:
            sent.append(call("sendDocument", files={"document": (Path(path).name, f)}, timeout=300,
                             chat_id=c, caption=caption[:1024]))
    return sent


def updates(offset: int, wait: int = 25) -> list[dict]:
    """Long-poll for new messages and button taps."""
    r = requests.post(API.format(token=_token(), method="getUpdates"),
                      data={"offset": offset, "timeout": wait,
                            "allowed_updates": json.dumps(["message", "callback_query"])},
                      timeout=wait + 15)
    return r.json().get("result", [])
