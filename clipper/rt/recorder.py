"""Ring-buffer recorder: one per channel.

Writes the live stream as short MPEG-TS segments (no re-encode) into
buffer/<channel>/seg_000123.ts and deletes segments older than buffer_minutes.

Sources:
  twitch   - streamlink (python -m streamlink) piped into ffmpeg. Needs: pip install streamlink
  url      - any URL ffmpeg can open directly (HLS .m3u8, file). Use with `yt-dlp -g <page>` for YouTube/Kick.
  testsrc  - ffmpeg's synthetic test pattern. Used for offline testing of the record -> clip path.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger("clipbot.recorder")


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as e:
        raise RuntimeError("ffmpeg not found: install it or `pip install imageio-ffmpeg`") from e


def streamlink_available() -> bool:
    try:
        import streamlink  # noqa: F401
        return True
    except ImportError:
        return False


class RingRecorder(threading.Thread):
    def __init__(self, channel: str, out_dir: Path, stop: threading.Event, source: str = "twitch",
                 url: str | None = None, quality: str = "720p60,720p,best",
                 segment_s: int = 2, buffer_minutes: float = 10.0):
        super().__init__(daemon=True, name=f"rec-{channel}")
        self.channel, self.source, self.url, self.quality = channel, source, url, quality
        self.dir = Path(out_dir) / channel
        self.dir.mkdir(parents=True, exist_ok=True)
        self.stop_evt = stop
        self.segment_s = segment_s
        self.buffer_s = buffer_minutes * 60
        self._procs: list[subprocess.Popen] = []

    # ---- process plumbing ----------------------------------------------------
    def _segment_args(self) -> list[str]:
        start = self._next_index()
        return ["-f", "segment", "-segment_time", str(self.segment_s), "-segment_format", "mpegts",
                "-segment_start_number", str(start), "-reset_timestamps", "1",
                str(self.dir / "seg_%06d.ts")]

    def _next_index(self) -> int:
        nums = [int(p.stem[4:]) for p in self.dir.glob("seg_*.ts") if p.stem[4:].isdigit()]
        return (max(nums) + 1) if nums else 0

    def _launch(self) -> subprocess.Popen:
        ff = ffmpeg_exe()
        quiet = ["-hide_banner", "-loglevel", "error", "-y"]
        if self.source == "testsrc":
            cmd = [ff, *quiet, "-re", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
                   "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                   "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", *self._segment_args()]
            return subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
        if self.source == "url":
            cmd = [ff, *quiet, "-i", self.url, "-c", "copy", *self._segment_args()]
            return subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
        if self.source == "twitch":
            if not streamlink_available():
                raise RuntimeError("streamlink not installed: pip install streamlink")
            sl = subprocess.Popen(
                [sys.executable, "-m", "streamlink", "--twitch-disable-ads", "--stdout",
                 f"https://www.twitch.tv/{self.channel}", self.quality],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            ffp = subprocess.Popen([ff, *quiet, "-i", "pipe:0", "-c", "copy", *self._segment_args()],
                                   stdin=sl.stdout)
            sl.stdout.close()  # let ffmpeg own the pipe
            self._procs.append(sl)
            return ffp
        raise ValueError(f"unknown source {self.source!r}")

    def run(self):
        while not self.stop_evt.is_set():
            try:
                proc = self._launch()
            except Exception as e:
                log.error("[%s] recorder can't start: %s", self.channel, e)
                return
            self._procs.append(proc)
            log.info("[%s] recording (%s) into %s", self.channel, self.source, self.dir)
            while proc.poll() is None and not self.stop_evt.is_set():
                self.prune()
                self.stop_evt.wait(2)
            self.terminate()
            if not self.stop_evt.is_set():
                log.info("[%s] stream ended/offline; retrying in 30 s", self.channel)
                self.stop_evt.wait(30)

    def terminate(self):
        for p in self._procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        self._procs.clear()

    def prune(self, now: float | None = None):
        cutoff = (now or time.time()) - self.buffer_s
        for seg in self.dir.glob("seg_*.ts"):
            try:
                if seg.stat().st_mtime < cutoff:
                    seg.unlink()
            except OSError:
                pass  # being written / already gone
