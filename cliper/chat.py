"""Chat-velocity spike detection.

The signal that finds a viral moment in a gaming stream is not what was said,
it is how hard chat reacted. This module turns a chat log into a ranked list of
candidate clip timestamps.

Why not raw message count: a stream's chat rate drifts with viewer count, so a
raw top-N picks twenty moments from the single busiest hour. We score each
bucket against a rolling local baseline instead, so a spike counts as a spike
relative to how chat was behaving a few minutes either side.
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

BUCKET_SECONDS = 5
BASELINE_WINDOW_BUCKETS = 60  # +/- 5 minutes at 5s buckets

# Tokens that mean "chat is losing it". Weighted because a burst of these is a
# far better viral predictor than a burst of ordinary conversation.
REACTION_TOKENS = {
    "kekw": 1.0, "lulw": 1.0, "omegalul": 1.0, "lul": 0.6, "lmao": 0.8,
    "lmfao": 0.9, "pogchamp": 0.9, "poggers": 0.9, "pog": 0.7, "pepelaugh": 1.0,
    "icant": 0.9, "sheesh": 0.7, "clipit": 1.5, "clipped": 1.3, "clip": 1.0,
    "holdup": 0.8, "wtf": 0.7, "omg": 0.6, "nahh": 0.7, "crazy": 0.5,
    "aura": 0.6, "cooked": 0.7, "goated": 0.7, "ratio": 0.4, "w": 0.3,
    "l": 0.3, "deadass": 0.6, "hellnah": 0.9, "bruh": 0.5, "sybau": 0.5,
}
_WORD = re.compile(r"[a-z]+")


@dataclass
class Candidate:
    """One clip-worthy moment."""
    offset_seconds: int      # where in the VOD the spike peaks
    start_seconds: int       # where the clip should start (peak minus pre-roll)
    end_seconds: int
    score: float             # higher = chat reacted harder, baseline-adjusted
    messages: int            # raw message count in the peak bucket
    reaction_weight: float   # how much of the spike was reaction spam
    sample: str              # a few of the actual messages, for your review

    def as_dict(self) -> dict:
        return asdict(self)


def bucket_messages(
    messages: list[tuple[float, str]], bucket_seconds: int = BUCKET_SECONDS
) -> tuple[list[int], list[float], list[list[str]]]:
    """Bin (offset, text) pairs into fixed windows.

    Returns parallel lists: message counts, summed reaction weight, and the
    texts themselves so we can show you why a moment was picked.
    """
    if not messages:
        return [], [], []
    span = int(max(off for off, _ in messages)) // bucket_seconds + 1
    counts = [0] * span
    weights = [0.0] * span
    texts: list[list[str]] = [[] for _ in range(span)]
    for offset, text in messages:
        idx = int(offset) // bucket_seconds
        if idx < 0 or idx >= span:
            continue
        counts[idx] += 1
        weights[idx] += _reaction_weight(text)
        if len(texts[idx]) < 6:
            texts[idx].append(text)
    return counts, weights, texts


def _reaction_weight(text: str) -> float:
    words = _WORD.findall(text.lower())
    if not words:
        return 0.0
    return sum(REACTION_TOKENS.get(w, 0.0) for w in words)


def _local_baseline(counts: list[int], i: int, window: int) -> tuple[float, float]:
    """Median and mean-absolute-deviation of chat rate around bucket i."""
    lo = max(0, i - window)
    hi = min(len(counts), i + window + 1)
    neighbourhood = counts[lo:i] + counts[i + 1:hi]
    if not neighbourhood:
        return 0.0, 1.0
    med = statistics.median(neighbourhood)
    mad = statistics.median([abs(c - med) for c in neighbourhood]) or 1.0
    return med, mad


def find_candidates(
    messages: list[tuple[float, str]],
    clip_seconds: int = 38,
    pre_roll: int = 10,
    max_candidates: int = 20,
    bucket_seconds: int = BUCKET_SECONDS,
    min_messages: int = 5,
) -> list[Candidate]:
    """Rank clip-worthy moments in a chat log, highest score first."""
    counts, weights, texts = bucket_messages(messages, bucket_seconds)
    if not counts:
        return []

    scored: list[Candidate] = []
    for i, n in enumerate(counts):
        if n < min_messages:
            continue
        med, mad = _local_baseline(counts, i, BASELINE_WINDOW_BUCKETS)
        z = (n - med) / mad
        if z <= 0:
            continue
        # Reaction spam multiplies the score; ordinary chatter does not.
        reaction_per_msg = weights[i] / n
        score = z * (1.0 + reaction_per_msg)
        peak = i * bucket_seconds
        scored.append(
            Candidate(
                offset_seconds=peak,
                start_seconds=max(0, peak - pre_roll),
                end_seconds=max(0, peak - pre_roll) + clip_seconds,
                score=round(score, 3),
                messages=n,
                reaction_weight=round(reaction_per_msg, 3),
                sample=" | ".join(texts[i][:4])[:300],
            )
        )

    scored.sort(key=lambda c: c.score, reverse=True)
    return _suppress_overlaps(scored, clip_seconds)[:max_candidates]


def _suppress_overlaps(cands: list[Candidate], clip_seconds: int) -> list[Candidate]:
    """Keep the best candidate in any overlapping group (non-max suppression)."""
    kept: list[Candidate] = []
    for c in cands:
        if all(abs(c.offset_seconds - k.offset_seconds) >= clip_seconds for k in kept):
            kept.append(c)
    return kept


# --------------------------------------------------------------------------
# Chat sources
# --------------------------------------------------------------------------

def load_twitchdownloader_json(path: Path) -> list[tuple[float, str]]:
    """Parse a chat export from TwitchDownloaderCLI (lay295/TwitchDownloader).

    Only the chat is downloaded, never the video - a 5-hour chat log is tens of
    megabytes, where the VOD is tens of gigabytes. That difference is what lets
    this whole pipeline run inside a GitHub Actions runner.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[tuple[float, str]] = []
    for c in data.get("comments", []):
        offset = c.get("content_offset_seconds")
        body = (c.get("message") or {}).get("body") or ""
        if offset is not None and body:
            out.append((float(offset), body))
    out.sort(key=lambda t: t[0])
    return out


def download_twitch_chat(vod_id: str, dest: Path, cli: str = "TwitchDownloaderCLI") -> Path:
    """Shell out to TwitchDownloaderCLI for a VOD's chat as JSON."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [cli, "chatdownload", "--id", str(vod_id), "-o", str(dest), "--collision", "overwrite"],
        check=True,
    )
    return dest
