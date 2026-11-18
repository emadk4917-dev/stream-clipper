"""Fetching stream VODs with yt-dlp (Twitch, YouTube, Kick, or a local file)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def is_local(src: str) -> bool:
    return os.path.exists(src)


def _env() -> dict:
    # ffmpeg only reads `http_proxy`; in proxied cloud sandboxes mirror HTTPS_PROXY into it.
    env = dict(os.environ)
    hp = env.get("HTTPS_PROXY") or env.get("https_proxy")
    if hp and not env.get("http_proxy"):
        env["http_proxy"] = hp
    return env


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, env=_env(), **kw)


def fetch_info(src: str) -> dict:
    """Title, channel, duration for a URL or local file."""
    if is_local(src):
        dur = probe_duration(src)
        return {"title": Path(src).stem, "uploader": "", "duration": dur, "webpage_url": src, "id": Path(src).stem}
    out = run(["yt-dlp", "-J", "--no-warnings", "--no-playlist", src], capture_output=True, text=True).stdout
    info = json.loads(out)
    keep = ("id", "title", "uploader", "uploader_id", "channel", "duration", "webpage_url", "extractor_key", "timestamp")
    return {k: info.get(k) for k in keep}


def probe_duration(path: str) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
              capture_output=True, text=True).stdout.strip()
    return float(out)


def _section_arg(start: float | None, end: float | None) -> list[str]:
    if start is None and end is None:
        return []
    s = start or 0
    e = "inf" if end is None else f"{end:.2f}"
    return ["--download-sections", f"*{s:.2f}-{e}"]


def download_audio(src: str, out_dir: Path, start: float | None = None, end: float | None = None) -> Path:
    """Audio-only copy of the stream (small and quick, used to find highlights)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "audio.m4a"
    if target.exists():
        return target
    if is_local(src):
        cmd = ["ffmpeg", "-y", "-v", "error"]
        if start:
            cmd += ["-ss", str(start)]
        if end:
            cmd += ["-to", str(end)]
        cmd += ["-i", src, "-vn", "-ac", "1", "-c:a", "aac", "-b:a", "96k", str(target)]
        run(cmd)
        return target
    tmpl = str(out_dir / "audio_dl.%(ext)s")
    run(["yt-dlp", "--no-warnings", "--no-playlist", "-f", "Audio_Only/bestaudio/worstaudio/worst",
         *_section_arg(start, end), "-o", tmpl, src])
    got = next(p for p in out_dir.glob("audio_dl.*") if not p.name.endswith(".part"))
    # normalise to mono AAC so every later step sees the same thing
    run(["ffmpeg", "-y", "-v", "error", "-i", str(got), "-vn", "-ac", "1", "-c:a", "aac", "-b:a", "96k", str(target)])
    got.unlink(missing_ok=True)
    return target


def download_video_section(src: str, start: float, end: float, out_path: Path, max_height: int = 1080) -> Path:
    """Download just the part of the VOD a clip needs (with a little padding)."""
    if out_path.exists():
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if is_local(src):
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", src,
             "-c", "copy", "-avoid_negative_ts", "make_zero", str(out_path)])
        return out_path
    tmpl = str(out_path.with_suffix("")) + ".dl.%(ext)s"
    fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/best"
    run(["yt-dlp", "--no-warnings", "--no-playlist", "-f", fmt, "--merge-output-format", "mp4",
         "--download-sections", f"*{start:.2f}-{end:.2f}", "-o", tmpl, src])
    got = next(p for p in out_path.parent.glob(out_path.stem + ".dl.*") if not p.name.endswith(".part"))
    shutil.move(str(got), out_path)
    return out_path
