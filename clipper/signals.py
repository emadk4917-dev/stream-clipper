"""Per-second "hype" signals: loudness of the stream audio, plus optional chat activity."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

SR = 8000  # plenty for loudness


def loudness_per_second(audio: Path) -> np.ndarray:
    """RMS loudness in dB for every second of audio (streams the decode, so 10h VODs are fine)."""
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", str(SR),
                             "-f", "s16le", "-"], stdout=subprocess.PIPE)
    out = []
    chunk = SR * 2 * 60  # one minute of samples per read
    leftover = b""
    while True:
        buf = proc.stdout.read(chunk)
        if not buf:
            break
        buf = leftover + buf
        n = len(buf) // (SR * 2)
        if n:
            a = np.frombuffer(buf[: n * SR * 2], dtype=np.int16).astype(np.float32).reshape(n, SR)
            rms = np.sqrt((a ** 2).mean(axis=1)) + 1e-6
            out.append(20 * np.log10(rms / 32768))
        leftover = buf[n * SR * 2:]
    proc.wait()
    return np.concatenate(out) if out else np.zeros(0)


def chat_per_second(chat_file: Path, n_seconds: int) -> np.ndarray | None:
    """Messages per second from a chat export.

    Accepts TwitchDownloader JSON ({"comments":[{"content_offset_seconds":..}]}),
    a plain JSON list of {"t": seconds}, or a text file with one offset in seconds per line.
    """
    if not chat_file or not Path(chat_file).exists():
        return None
    p = Path(chat_file)
    offsets: list[float] = []
    try:
        data = json.loads(p.read_text())
        items = data.get("comments", data) if isinstance(data, dict) else data
        for c in items:
            if isinstance(c, dict):
                t = c.get("content_offset_seconds", c.get("t", c.get("offset")))
                if t is not None:
                    offsets.append(float(t))
            else:
                offsets.append(float(c))
    except json.JSONDecodeError:
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    offsets.append(float(line.split()[0]))
                except ValueError:
                    pass
    counts = np.zeros(n_seconds, dtype=np.float32)
    for t in offsets:
        i = int(t)
        if 0 <= i < n_seconds:
            counts[i] += 1
    return counts


def robust_z(x: np.ndarray, window: int = 600) -> np.ndarray:
    """How unusual each second is compared with the ~10 minutes around it.

    Using a rolling baseline means a loud game section doesn't drown out a big moment later.
    """
    if len(x) == 0:
        return x
    from numpy.lib.stride_tricks import sliding_window_view
    pad = window // 2
    xp = np.pad(x, pad, mode="edge")
    step = 10  # compute baseline every 10s for speed, then interpolate
    idx = np.arange(0, len(x), step)
    med = np.empty(len(idx))
    mad = np.empty(len(idx))
    for k, i in enumerate(idx):
        w = xp[i: i + window]
        m = np.median(w)
        med[k] = m
        mad[k] = np.median(np.abs(w - m)) + 1e-3
    med_full = np.interp(np.arange(len(x)), idx, med)
    mad_full = np.interp(np.arange(len(x)), idx, mad)
    return (x - med_full) / (1.4826 * mad_full)


def smooth(x: np.ndarray, k: int) -> np.ndarray:
    if len(x) == 0 or k <= 1:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


def hype_curve(loud: np.ndarray, chat: np.ndarray | None, chat_lag: int = 6) -> np.ndarray:
    """Combine signals into one per-second score. Chat reacts a few seconds late, so shift it back."""
    score = np.clip(smooth(robust_z(loud), 4), -3, 8)
    if chat is not None and chat.sum() > 50:
        c = smooth(chat, 5)
        c = np.roll(c, -chat_lag)
        cz = np.clip(robust_z(c), -3, 10)
        score = 0.45 * score + 0.55 * cz
    return smooth(score, 6)
