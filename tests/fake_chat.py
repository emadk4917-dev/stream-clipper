"""Deterministic fake Twitch chat for offline tests.

Channel "campaignguy" (t = 0..600 s, baseline ~2 msgs/s):
  t=200..208  real hype burst (40 users spamming KEKW / CLIP IT / ?????)  -> SHOULD fire
  t=222..228  second burst, inside the 45 s cooldown                     -> should NOT fire again
  t=320..330  one user spamming 20 msgs/s                                -> should NOT fire (per-user cap)
  t=450..460  big hype burst                                             -> SHOULD fire
Channel "slowclimb": chat slowly rises 2 -> 4 msgs/s over 10 min        -> should NOT fire

    python tests/fake_chat.py tests/data/fake_chat.jsonl   # writes the log for the replay CLI
"""
import json
import random
import sys
from pathlib import Path

NORMAL = ["lol", "this map is huge", "what car is that", "vice city looks crazy", "chat is he lost",
          "first time playing?", "gg", "how long has he been on", "the graphics tho", "go north",
          "W stream", "anyone know the song", "Jason is goated", "LUL", "hi chat"]
HYPE = ["KEKW", "KEKW KEKW KEKW", "CLIP IT", "CLIP IT CHAT", "LMAO", "?????", "OMEGALUL", "NO WAY",
        "W", "LMAO WHAT", "HOLY KEKW", "clip that", "WTF ?????"]

EXPECTED_FIRE_WINDOWS = [(200, 212), (450, 464)]  # campaignguy only


def _poisson_times(rng, start, end, rate_fn):
    t = start
    while True:
        r = max(rate_fn(t), 1e-3)
        t += rng.expovariate(r)
        if t >= end:
            return
        yield t


def generate(seed: int = 7):
    rng = random.Random(seed)
    users = [f"viewer{i}" for i in range(120)]
    msgs = []

    def add(ch, t, user, text):
        msgs.append({"t": round(t, 3), "channel": ch, "user": user, "text": text})

    # campaignguy baseline
    for t in _poisson_times(rng, 0, 600, lambda t: 2.0):
        add("campaignguy", t, rng.choice(users), rng.choice(NORMAL))
    # bursts
    for (a, b, rate, n_users) in [(200, 208, 18.0, 40), (222, 228, 15.0, 35), (450, 460, 25.0, 60)]:
        crowd = rng.sample(users, n_users)
        for t in _poisson_times(rng, a, b, lambda t: rate):
            add("campaignguy", t, rng.choice(crowd), rng.choice(HYPE))
    # single spammer
    for t in _poisson_times(rng, 320, 330, lambda t: 20.0):
        add("campaignguy", t, "spambot9000", "CLIP IT KEKW ?????")

    # slowclimb: gradual organic growth, no moment
    for t in _poisson_times(rng, 0, 600, lambda t: 2.0 + 2.0 * t / 600):
        add("slowclimb", t, rng.choice(users), rng.choice(NORMAL))

    msgs.sort(key=lambda m: m["t"])
    return msgs


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/data/fake_chat.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for m in generate():
            f.write(json.dumps(m) + "\n")
    print(f"wrote {out}")
