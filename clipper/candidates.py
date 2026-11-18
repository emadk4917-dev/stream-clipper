"""Turn the hype curve into a ranked list of candidate moments, each with a transcript."""
from __future__ import annotations

import re

import numpy as np

from .transcribe import transcribe_range, words_to_text

HYPE_PHRASES = [
    "oh my god", "no way", "what the", "let's go", "lets go", "clip that", "clip it", "holy", "are you serious",
    "i'm dead", "im dead", "bro", "dude", "yo", "wait", "what", "chat", "insane", "crazy", "screaming",
    "fuck", "shit", "damn", "omg", "nah", "bruh", "laugh", "scream", "why", "how", "help",
]


def fmt_ts(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 3600:d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def find_peaks(curve: np.ndarray, n: int, min_gap: int = 90, edge: int = 30) -> list[int]:
    """Highest points of the curve, at least `min_gap` seconds apart."""
    order = np.argsort(curve)[::-1]
    picked: list[int] = []
    for i in order:
        i = int(i)
        if i < edge or i > len(curve) - 5:
            continue
        if all(abs(i - p) >= min_gap for p in picked):
            picked.append(i)
        if len(picked) >= n:
            break
    return picked


def phrase_score(text: str) -> float:
    t = text.lower()
    hits = sum(len(re.findall(r"\b" + re.escape(p) + r"\b", t)) for p in HYPE_PHRASES)
    exclaim = t.count("!")
    return min(hits * 0.4 + exclaim * 0.2, 4.0)


def snap_window(words: list[dict], start: float, end: float) -> tuple[float, float]:
    """Nudge cut points into pauses so clips don't start or end mid-word."""
    if not words:
        return start, end
    gaps = [(words[i - 1]["e"], words[i]["s"]) for i in range(1, len(words)) if words[i]["s"] - words[i - 1]["e"] > 0.35]
    def best(target, pick):
        cands = [pick(g) for g in gaps if abs(pick(g) - target) <= 6]
        return min(cands, key=lambda x: abs(x - target)) if cands else target
    s = best(start, lambda g: g[1] - 0.15)
    e = best(end, lambda g: g[0] + 0.4)
    return round(s, 2), round(e, 2)


def transcript_lines(words: list[dict], every: float = 4.0) -> list[str]:
    """Readable transcript with a timestamp roughly every few seconds (for picking exact cuts)."""
    lines, cur, cur_t = [], [], None
    for w in words:
        if cur_t is None:
            cur_t = w["s"]
        cur.append(w["w"])
        if w["e"] - cur_t >= every:
            lines.append(f"[{fmt_ts(cur_t)} | {cur_t:.1f}s] {' '.join(cur)}")
            cur, cur_t = [], None
    if cur:
        lines.append(f"[{fmt_ts(cur_t)} | {cur_t:.1f}s] {' '.join(cur)}")
    return lines


def build_candidates(audio, curve: np.ndarray, n: int = 25, offset: float = 0.0,
                     model: str = "small", pre: float = 45, post: float = 30, log=print) -> list[dict]:
    """`offset` = where the audio file starts inside the original VOD (if only part was downloaded)."""
    peaks = find_peaks(curve, n)
    out = []
    for k, p in enumerate(sorted(peaks)):
        a, b = max(0, p - pre), min(len(curve), p + post)
        log(f"  transcribing moment {k + 1}/{len(peaks)} at {fmt_ts(p + offset)}")
        words = transcribe_range(audio, a, b, model=model)
        text = words_to_text(words)
        s, e = snap_window(words, max(a, p - 22), min(b, p + 10))
        hype = float(curve[p])
        out.append({
            "id": f"c{k + 1:02d}",
            "peak": p + offset,
            "peak_ts": fmt_ts(p + offset),
            "hype": round(hype, 2),
            "phrase_score": phrase_score(text),
            "score": round(hype + phrase_score(text) + min(len(words) / 60, 1.0), 2),
            "context_start": a + offset,
            "context_end": b + offset,
            "suggested_start": round(s + offset, 2),
            "suggested_end": round(e + offset, 2),
            "words": [{**w, "s": round(w["s"] + offset, 2), "e": round(w["e"] + offset, 2)} for w in words],
            "text": text,
        })
    out.sort(key=lambda c: c["score"], reverse=True)
    return out


def candidates_markdown(info: dict, cands: list[dict]) -> str:
    lines = [f"# Candidate moments: {info.get('title', '')}",
             f"Channel: {info.get('uploader') or info.get('channel') or '?'} | Source: {info.get('webpage_url')}",
             "Times are seconds from the start of the VOD. Ranked by machine score (loudness/chat spike + hype words).", ""]
    for c in cands:
        lines += [f"## {c['id']}  @ {c['peak_ts']}  (score {c['score']}, hype {c['hype']})",
                  f"Suggested cut: {c['suggested_start']} → {c['suggested_end']}  "
                  f"({c['suggested_end'] - c['suggested_start']:.0f}s). Context available: "
                  f"{c['context_start']:.0f} → {c['context_end']:.0f}", ""]
        lines += transcript_lines(c["words"]) or ["(no speech detected: maybe music, gameplay or a scream)"]
        lines.append("")
    return "\n".join(lines)
