"""Speech-to-text with word timings (faster-whisper, runs on CPU)."""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # plain HTTPS download of the model works behind more firewalls


@lru_cache(maxsize=1)
def _model(name: str):
    from faster_whisper import WhisperModel
    return WhisperModel(name, device="auto", compute_type="int8")


def transcribe_range(audio: Path, start: float, end: float, model: str = "small") -> list[dict]:
    """Words spoken between start and end (seconds, absolute to the audio file)."""
    start = max(0.0, start)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(audio),
                          "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True, check=True).stdout
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if len(pcm) < 16000:
        return []
    segments, _ = _model(model).transcribe(pcm, word_timestamps=True, vad_filter=True,
                                           condition_on_previous_text=False)
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"w": text, "s": round(start + w.start, 2), "e": round(start + w.end, 2)})
    return words


def words_to_text(words: list[dict]) -> str:
    return " ".join(w["w"] for w in words)
