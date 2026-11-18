"""Chat-spike detector.

Pure logic, no I/O: feed() messages with timestamps, call tick(now) about once a
second, and get a SpikeEvent back when chat activity jumps well above that
channel's own normal. Deterministic, so it can be tested by replaying a log.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field


DEFAULT_KEYWORDS = {
    # token (upper-case) -> bonus weight added to the message's base weight of 1.0
    "KEKW": 1.0, "LUL": 1.0, "OMEGALUL": 1.5, "LMAO": 1.0, "LMFAO": 1.0,
    "W": 0.5, "POG": 1.0, "POGGERS": 1.0, "CLIP": 2.0, "HOLY": 0.5,
    "WTF": 0.5, "NO WAY": 1.0, "CLIP IT": 2.0,
}

_QMARKS = re.compile(r"\?{3,}")
_BANGS = re.compile(r"!{3,}")
_TOKEN = re.compile(r"[A-Za-z0-9']+")


@dataclass
class DetectorParams:
    window_s: float = 5.0          # rate = weighted msgs in last window / window_s
    ema_halflife_s: float = 120.0  # how fast the baseline adapts
    spike_adapt_factor: float = 0.1  # baseline adapts this much slower while spiking
    z_threshold: float = 4.5       # fire when z-score >= this ...
    min_ratio: float = 2.0         # ... and rate >= min_ratio * baseline ...
    min_rate: float = 1.5          # ... and rate >= this absolute floor (weighted msgs/s) ...
    min_unique_chatters: int = 5   # ... and at least this many different users in window
    min_hype_share: float = 0.4   # ... and >= this fraction of window msgs contain a hype token
    per_user_cap: float = 3.0      # max weighted contribution of one user per window
    max_msg_weight: float = 4.0
    qmark_bonus: float = 1.0       # "?????"
    bang_bonus: float = 0.5        # "!!!"
    warmup_s: float = 60.0         # no fires until baseline has this much data
    cooldown_s: float = 45.0       # min gap between fires on one channel
    min_std: float = 0.3           # floor on std so a dead-quiet chat can't fire on 2 msgs
    keywords: dict = field(default_factory=lambda: dict(DEFAULT_KEYWORDS))

    @classmethod
    def from_dict(cls, d: dict | None) -> "DetectorParams":
        d = dict(d or {})
        kw = d.pop("keywords", None)
        p = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        if kw:
            p.keywords = {k.upper(): float(v) for k, v in kw.items()}
        return p


@dataclass
class SpikeEvent:
    channel: str
    t: float            # when it fired (unix seconds, or log time in replays)
    z: float
    rate: float         # weighted msgs/s in the window
    baseline: float     # EMA mean rate before the spike
    unique_chatters: int
    top_tokens: list    # [(token, count), ...]
    sample_messages: list

    def strength(self) -> float:
        """Rough 0-10 placeholder score until a real judge (Whisper + Claude) exists."""
        return round(min(10.0, 2.0 + self.z * 0.8 + math.log2(max(1.0, self.rate / max(self.baseline, 0.1)))), 1)


class SpikeDetector:
    def __init__(self, channel: str, params: DetectorParams | None = None):
        self.channel = channel
        self.p = params or DetectorParams()
        self._msgs: deque = deque()  # (t, user, weight, text)
        self._mean: float | None = None
        self._var: float = 0.0
        self._first_t: float | None = None
        self._last_tick: float | None = None
        self._last_fire: float = -math.inf
        self._n = 1
        # multi-word keywords are matched as phrases, single words as tokens
        self._phrases = {k: v for k, v in self.p.keywords.items() if " " in k}
        self._words = {k: v for k, v in self.p.keywords.items() if " " not in k}

    # ---- message weighting -------------------------------------------------
    def weigh(self, text: str) -> float:
        w = 1.0
        up = text.upper()
        tokens = set(_TOKEN.findall(up))
        for tok in tokens:
            w += self._words.get(tok, 0.0)
        for ph, bonus in self._phrases.items():
            if ph in up:
                w += bonus
        if _QMARKS.search(text):
            w += self.p.qmark_bonus
        if _BANGS.search(text):
            w += self.p.bang_bonus
        return min(w, self.p.max_msg_weight)

    def feed(self, t: float, user: str, text: str) -> None:
        if self._first_t is None:
            self._first_t = t
        self._msgs.append((t, user, self.weigh(text), text))

    # ---- per-second update -------------------------------------------------
    def _window_stats(self, now: float):
        cutoff = now - self.p.window_s
        while self._msgs and self._msgs[0][0] < cutoff:
            self._msgs.popleft()
        per_user = defaultdict(float)
        hype = 0
        for _, user, w, _ in self._msgs:
            per_user[user] += w
            hype += w > 1.0
        total = sum(min(v, self.p.per_user_cap) for v in per_user.values())
        share = hype / len(self._msgs) if self._msgs else 0.0
        return total / self.p.window_s, len(per_user), share

    def tick(self, now: float) -> SpikeEvent | None:
        if self._first_t is None:
            self._first_t = now
        dt = 1.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        self._last_tick = now

        rate, uniq, hype_share = self._window_stats(now)

        if self._mean is None:
            self._mean, self._var = rate, 0.0
            return None

        # std floor: never assume chat is calmer than random (Poisson) chat at this rate would be
        poisson_std = math.sqrt(max(self._mean, 0.0) / self.p.window_s)
        std = max(math.sqrt(self._var), self.p.min_std, poisson_std)
        z = (rate - self._mean) / std
        baseline = self._mean
        spiking = z >= self.p.z_threshold

        # EMA update of mean and variance; slower while spiking so hype doesn't become "normal".
        # During warm-up use a plain running average (alpha = 1/n) so the baseline is right
        # by the time firing is allowed, instead of creeping up from 0 for minutes.
        self._n += 1
        alpha = 1.0 - 0.5 ** (dt / self.p.ema_halflife_s)
        in_warmup = (now - self._first_t) < self.p.warmup_s
        if in_warmup:
            alpha = max(alpha, 1.0 / self._n)
        elif spiking:
            alpha *= self.p.spike_adapt_factor
        diff = rate - self._mean
        self._mean += alpha * diff
        self._var = (1.0 - alpha) * (self._var + alpha * diff * diff)

        if in_warmup:
            return None
        if now - self._last_fire < self.p.cooldown_s:
            return None
        if not (spiking
                and rate >= self.p.min_ratio * max(baseline, 1e-6)
                and rate >= self.p.min_rate
                and uniq >= self.p.min_unique_chatters
                and hype_share >= self.p.min_hype_share):
            return None

        self._last_fire = now
        return SpikeEvent(
            channel=self.channel, t=now, z=round(z, 2), rate=round(rate, 2),
            baseline=round(baseline, 2), unique_chatters=uniq,
            top_tokens=self._top_tokens(), sample_messages=[m[3] for m in list(self._msgs)[-8:]],
        )

    def _top_tokens(self, n: int = 5):
        c = Counter()
        for _, _, _, text in self._msgs:
            for tok in set(_TOKEN.findall(text.upper())):
                c[tok] += 1
        return c.most_common(n)
