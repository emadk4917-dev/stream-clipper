"""Replay a chat log (.jsonl, one {"t","channel","user","text"} per line) through the detector.

Use it to tune thresholds on recorded GTA 5 / GTA 6 chat without a live stream:
    python -m clipper.rt.replay chatlogs/xqc_2026-11-18.jsonl --config config.toml
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from .tomlconfig import load_config, detector_params_for
from .detector import DetectorParams, SpikeDetector, SpikeEvent


def load_log(path: Path):
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def replay(messages, params_for=lambda ch: DetectorParams()) -> list[SpikeEvent]:
    """messages: iterable of dicts sorted by t. Ticks every whole second, like the live loop."""
    dets: dict[str, SpikeDetector] = {}
    events: list[SpikeEvent] = []
    next_tick = None

    def tick_until(t):
        nonlocal next_tick
        while next_tick is not None and next_tick <= t:
            for d in dets.values():
                ev = d.tick(next_tick)
                if ev:
                    events.append(ev)
            next_tick += 1.0

    last_t = None
    for m in messages:
        t = float(m["t"])
        if next_tick is None:
            next_tick = math.floor(t) + 1.0
        tick_until(t)
        ch = m["channel"]
        if ch not in dets:
            dets[ch] = SpikeDetector(ch, params_for(ch))
            dets[ch].tick(math.floor(t))  # start its clock
        dets[ch].feed(t, m["user"], m["text"])
        last_t = t
    if last_t is not None:
        tick_until(last_t + 5)
    return events


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=Path)
    ap.add_argument("--config", type=Path, default=None)
    a = ap.parse_args()
    cfg = load_config(a.config) if a.config else None
    pf = (lambda ch: detector_params_for(cfg, ch)) if cfg else (lambda ch: DetectorParams())
    events = replay(load_log(a.log), pf)
    for e in events:
        ts = time.strftime("%H:%M:%S", time.localtime(e.t)) if e.t > 1e9 else f"t={e.t:.0f}s"
        print(f"{ts} [{e.channel}] SPIKE z={e.z} rate={e.rate}/s base={e.baseline}/s "
              f"chatters={e.unique_chatters} score~{e.strength()} top={e.top_tokens[:3]}")
    print(f"{len(events)} spike(s)")


if __name__ == "__main__":
    main()
