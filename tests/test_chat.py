"""Tests for the spike engine. Run: python3 -m pytest tests/ -q"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cliper.chat import find_candidates, bucket_messages, _reaction_weight


def synth_stream(seed=7):
    """A 2-hour stream: quiet first half, busy second half, with planted spikes.

    The point of the test: a modest spike at t=600s (quiet section) must
    outrank the generally-high plateau of the busy second half. A raw
    top-N by message count would get this wrong.
    """
    rng = random.Random(seed)
    msgs = []
    for t in range(0, 7200):
        base = 1 if t < 3600 else 8          # busy second half
        for _ in range(rng.randint(0, base)):
            msgs.append((t + rng.random(), rng.choice(["hey", "gg", "nice", "ok"])))
    # Planted spike in the QUIET half: chat explodes with reaction spam.
    for t in range(600, 606):
        for _ in range(40):
            msgs.append((t + rng.random(), rng.choice(["KEKW", "CLIP IT", "OMEGALUL"])))
    # Planted spike in the BUSY half, same absolute size.
    for t in range(5000, 5006):
        for _ in range(40):
            msgs.append((t + rng.random(), rng.choice(["KEKW", "LMAO", "POG"])))
    msgs.sort(key=lambda m: m[0])
    return msgs


def test_reaction_weight_distinguishes_spam_from_chatter():
    assert _reaction_weight("KEKW CLIP IT") > _reaction_weight("what game is this")
    assert _reaction_weight("") == 0.0


def test_bucketing_counts_every_message():
    msgs = [(0.0, "a"), (1.0, "b"), (7.0, "c")]
    counts, weights, texts = bucket_messages(msgs, bucket_seconds=5)
    assert sum(counts) == 3
    assert counts[0] == 2 and counts[1] == 1
    assert texts[0] == ["a", "b"]


def test_finds_planted_spikes():
    cands = find_candidates(synth_stream(), clip_seconds=38, pre_roll=10, max_candidates=10)
    assert cands, "no candidates found"
    peaks = [c.offset_seconds for c in cands]
    assert any(595 <= p <= 615 for p in peaks), f"missed quiet-half spike: {peaks[:5]}"
    assert any(4995 <= p <= 5015 for p in peaks), f"missed busy-half spike: {peaks[:5]}"


def test_quiet_spike_outranks_busy_plateau():
    """Baseline-relative scoring: the quiet-half spike should rank top-3."""
    cands = find_candidates(synth_stream(), max_candidates=20)
    top3 = [c.offset_seconds for c in cands[:3]]
    assert any(595 <= p <= 615 for p in top3), f"quiet spike not in top 3: {top3}"


def test_no_overlapping_clips():
    cands = find_candidates(synth_stream(), clip_seconds=38, max_candidates=20)
    offs = sorted(c.offset_seconds for c in cands)
    for a, b in zip(offs, offs[1:]):
        assert b - a >= 38, f"overlapping clips at {a} and {b}"


def test_clip_window_math():
    cands = find_candidates(synth_stream(), clip_seconds=38, pre_roll=10, max_candidates=3)
    for c in cands:
        assert c.start_seconds == max(0, c.offset_seconds - 10)
        assert c.end_seconds - c.start_seconds == 38
        assert c.start_seconds >= 0


def test_empty_input_is_safe():
    assert find_candidates([]) == []
