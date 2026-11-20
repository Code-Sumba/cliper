"""Pick clip-worthy moments WITHOUT an AI call.

The vendored stream-clips skill picks moments by shelling out to a coding-agent
CLI the user is signed in to (`claude -p`, `codex exec`, `gemini -p`). That is
a good design on a desktop and it cannot work in GitHub Actions, which has no
subscription auth.

So the headless pipeline combines three cheap signals instead:

  chat   message velocity against a rolling local baseline   (cliper/chat.py)
  volume how much louder than the surrounding minutes        (vendored listen.py)
  words  reaction vocabulary density in chat                 (cliper/chat.py)

Each is weak alone. Together they find the same moments an AI would for the
obvious cases (something loud happened and chat erupted), and miss the subtle
ones an AI would catch (a quiet devastating line). That trade is the price of
running unattended for free.

Set CLIPER_BRAIN=api and an API key to swap this for a real model later.
"""
from __future__ import annotations

from dataclasses import replace

from .chat import Candidate

VOLUME_WEIGHT = 0.35     # how much a loudness spike can lift a chat-driven score


def volume_at(curve: list[float] | None, second: int, window: int = 6) -> float:
    """Mean dB over a window of the per-second volume curve, or 0 if absent."""
    if not curve:
        return 0.0
    lo = max(0, second - window // 2)
    hi = min(len(curve), lo + window)
    seg = [v for v in curve[lo:hi] if v is not None]
    return sum(seg) / len(seg) if seg else 0.0


def baseline_volume(curve: list[float] | None) -> float:
    if not curve:
        return 0.0
    vals = sorted(v for v in curve if v is not None)
    return vals[len(vals) // 2] if vals else 0.0


def blend(
    candidates: list[Candidate],
    volume_curve: list[float] | None = None,
    volume_weight: float = VOLUME_WEIGHT,
) -> list[Candidate]:
    """Re-rank chat candidates using the audio volume curve.

    A chat spike that lines up with the streamer getting loud is worth more
    than one that does not: chat reacts to screen events too (a donation, a
    raid), and those make poor clips.
    """
    if not volume_curve:
        return candidates
    base = baseline_volume(volume_curve)
    out: list[Candidate] = []
    for c in candidates:
        db_over = max(0.0, volume_at(volume_curve, c.offset_seconds) - base)
        lift = 1.0 + volume_weight * min(db_over / 6.0, 2.0)   # cap the bonus
        out.append(replace(c, score=round(c.score * lift, 3)))
    out.sort(key=lambda c: c.score, reverse=True)
    return out
