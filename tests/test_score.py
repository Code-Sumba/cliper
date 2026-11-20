"""Volume blending."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cliper.chat import Candidate
from cliper.score import baseline_volume, blend, volume_at


def mk(off, score):
    return Candidate(offset_seconds=off, start_seconds=off - 10, end_seconds=off + 28,
                     score=score, messages=100, reaction_weight=0.5, sample="KEKW")


def test_no_curve_changes_nothing():
    c = [mk(100, 50.0)]
    assert blend(c, None) == c


def test_shout_promotes_a_lower_chat_spike():
    curve = [-30.0] * 600
    for t in range(295, 310):
        curve[t] = -18.0
    out = blend([mk(100, 50.0), mk(300, 48.0)], curve)
    assert out[0].offset_seconds == 300, "the spike aligned with a shout should win"


def test_flat_audio_preserves_order():
    out = blend([mk(100, 50.0), mk(300, 48.0)], [-30.0] * 600)
    assert [c.offset_seconds for c in out] == [100, 300]


def test_bonus_is_capped():
    curve = [-30.0] * 600
    for t in range(295, 310):
        curve[t] = 40.0                      # absurdly loud
    out = blend([mk(300, 10.0)], curve)
    assert out[0].score <= 10.0 * 1.71, "volume bonus must stay bounded"


def test_baseline_and_lookup_handle_empties():
    assert baseline_volume([]) == 0.0
    assert volume_at(None, 10) == 0.0
