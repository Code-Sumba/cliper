"""Per-second loudness for a stream.

Audio only: a 5-hour stream is ~250 MB as audio against 20-40 GB as video, so
this is cheap enough to run over a whole broadcast on a free runner.

The curve feeds cliper/score.py, which lifts chat spikes that line up with the
streamer getting loud. Chat reacts to things that make poor clips too - a
donation, a raid, a joke in chat itself - and loudness is the cheapest way to
tell those apart from something actually happening on screen.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

SAMPLE_RATE = 8000       # plenty for loudness; keeps the decode fast


def download_audio(url: str, dest: Path, extra_args: list[str] | None = None) -> Path:
    """Fetch a stream's audio track only."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp", "-f", "bestaudio/best", "--extract-audio",
        "--audio-format", "wav", "--audio-quality", "0",
        "--no-playlist", "--retries", "5",
        "-o", str(dest.with_suffix("")) + ".%(ext)s",
        *(extra_args or []), url,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    wav = dest.with_suffix(".wav")
    return wav if wav.exists() else dest


def volume_curve(audio: Path, seconds_per_bin: int = 1) -> list[float]:
    """dBFS per bin, straight from a mono PCM decode.

    Returns a list where index i is the loudness of second i. Silence is
    reported as -90 dB rather than -inf so the arithmetic downstream stays sane.
    """
    import numpy as np

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(audio),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True, check=True,
    )
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return []

    per_bin = SAMPLE_RATE * seconds_per_bin
    usable = (samples.size // per_bin) * per_bin
    if usable == 0:
        return []
    blocks = samples[:usable].reshape(-1, per_bin)
    rms = np.sqrt(np.mean(blocks ** 2, axis=1))
    with np.errstate(divide="ignore"):
        db = 20.0 * np.log10(np.maximum(rms, 1e-5))
    return [float(max(v, -90.0)) for v in db]


def curve_for_stream(url: str, work_dir: Path, key: str) -> list[float]:
    """Download audio, measure it, cache the result beside the work files."""
    import json

    cache = work_dir / f"{key}_volume.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass

    audio = work_dir / f"{key}_audio.wav"
    try:
        audio = download_audio(url, audio)
        curve = volume_curve(audio)
    except (subprocess.CalledProcessError, OSError, ImportError) as exc:
        print(f"[audio] volume curve unavailable ({type(exc).__name__}: {exc})")
        return []
    finally:
        audio.unlink(missing_ok=True)       # free runner disk at once

    cache.write_text(json.dumps(curve), encoding="utf-8")
    print(f"[audio] volume curve: {len(curve)} seconds measured")
    return curve
