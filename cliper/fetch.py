"""Download only the seconds we need.

This is the module that makes the pipeline fit on free infrastructure. A GitHub
Actions runner gives you roughly 14 GB of free disk and a 6-hour job limit. A
5-hour Twitch VOD is 20-40 GB, so downloading it is not an option.

yt-dlp's --download-sections fetches just the byte ranges covering a time
window. Twenty 38-second clips is a couple of hundred megabytes instead of
tens of gigabytes, and it is faster than downloading the whole thing and
throwing 99% of it away.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .chat import Candidate


def _hhmmss(seconds: int) -> str:
    h, rem = divmod(max(0, int(seconds)), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def check_tools() -> dict[str, bool]:
    return {t: shutil.which(t) is not None for t in ("yt-dlp", "ffmpeg", "ffprobe")}


def fetch_segment(
    url: str,
    start_seconds: int,
    end_seconds: int,
    dest: Path,
    max_height: int = 1080,
    cookies_from: str | None = None,
) -> Path:
    """Pull one time range out of a remote VOD."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    section = f"*{_hhmmss(start_seconds)}-{_hhmmss(end_seconds)}"
    cmd = [
        "yt-dlp",
        "--download-sections", section,
        # force_keyframes gives clean cuts at the cost of a little re-encoding
        "--force-keyframes-at-cuts",
        "-f", f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--retries", "5",
        "--fragment-retries", "10",
        "-o", str(dest),
        url,
    ]
    if cookies_from:
        cmd[1:1] = ["--cookies-from-browser", cookies_from]
    subprocess.run(cmd, check=True)
    return dest


def fetch_candidates(
    url: str, candidates: list[Candidate], work_dir: Path, prefix: str
) -> list[tuple[Candidate, Path]]:
    """Fetch every candidate window. A failure on one does not stop the rest."""
    out: list[tuple[Candidate, Path]] = []
    for i, cand in enumerate(candidates):
        dest = work_dir / f"{prefix}_{i:02d}_raw.mp4"
        try:
            fetch_segment(url, cand.start_seconds, cand.end_seconds, dest)
            if dest.exists() and dest.stat().st_size > 0:
                out.append((cand, dest))
        except subprocess.CalledProcessError as exc:
            print(f"[fetch] candidate {i} at {cand.offset_seconds}s failed: {exc}")
    return out
