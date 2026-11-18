from __future__ import annotations

import tomllib
from pathlib import Path

from .detector import DetectorParams


def load_config(path: Path) -> dict:
    path = Path(path)
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    base = path.parent.resolve()
    g = cfg.setdefault("general", {})
    for key, default in [("buffer_dir", "buffer"), ("clips_dir", "clips"),
                         ("queue_path", "review_queue.jsonl"), ("chat_log_dir", "chatlogs")]:
        g[key] = (base / g.get(key, default)).resolve()
    cfg.setdefault("detector", {})
    cfg.setdefault("channels", [])
    for ch in cfg["channels"]:
        ch["name"] = ch["name"].lower()
        ch.setdefault("platform", "twitch")
        ch.setdefault("campaign", False)
        ch.setdefault("record", True)
        ch.setdefault("source", "twitch")
    return cfg


def detector_params_for(cfg: dict, channel: str) -> DetectorParams:
    d = dict(cfg.get("detector", {}))
    for ch in cfg.get("channels", []):
        if ch["name"] == channel.lower():
            d.update(ch.get("detector", {}))
    return DetectorParams.from_dict(d)
