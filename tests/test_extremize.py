"""
Offline tests for the extremizing transform and agreeing-signal counter in main.py.

No network calls, no Claude CLI, no DB access.
Run: python -m pytest -q
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from main import _extremize, _count_agreeing_signals


# ─── _extremize ───────────────────────────────────────────────────────────────

def test_extremize_identity_at_alpha_one():
    """alpha=1.0 must be a no-op."""
    assert _extremize(0.65, 1.0) == pytest.approx(0.65, abs=1e-6)


def test_extremize_pushes_above_half_higher():
    """A probability > 0.5 with alpha > 1 should increase."""
    p = 0.65
    assert _extremize(p, 1.3) > p


def test_extremize_pushes_below_half_lower():
    """A probability < 0.5 with alpha > 1 should decrease."""
    p = 0.35
    assert _extremize(p, 1.3) < p


def test_extremize_symmetric():
    """_extremize(p) and 1 - _extremize(1-p) must be equal (symmetry around 0.5)."""
    p = 0.70
    ext_p  = _extremize(p,       1.2)
    ext_1p = _extremize(1 - p,   1.2)
    assert ext_p == pytest.approx(1 - ext_1p, abs=1e-6)


def test_extremize_clips_boundary_low():
    """Values at or below 0.001 pass through unchanged."""
    assert _extremize(0.0, 1.3) == 0.0
    assert _extremize(0.001, 1.3) == pytest.approx(0.001)


def test_extremize_clips_boundary_high():
    """Values at or above 0.999 pass through unchanged."""
    assert _extremize(1.0, 1.3) == 1.0
    assert _extremize(0.999, 1.3) == pytest.approx(0.999)


def test_extremize_alpha_15_stronger_than_alpha_115():
    """Higher alpha = more extreme shift."""
    p = 0.70
    assert _extremize(p, 1.30) > _extremize(p, 1.15)


def test_extremize_at_half_is_unchanged():
    """0.5 is a fixed point for any alpha (symmetry)."""
    assert _extremize(0.5, 1.3) == pytest.approx(0.5, abs=1e-6)


# ─── _count_agreeing_signals ──────────────────────────────────────────────────

def _market(**kwargs):
    return {
        "heuristic_direction": None,
        "poly": None,
        "ext_consensus": {},
        "whale_data": {},
        "ob_flag": False,
        "ob_direction": None,
        "watchlist_signal": False,
        "watchlist_direction": None,
        **kwargs,
    }


def test_count_no_signals():
    m = _market()
    assert _count_agreeing_signals(m, "YES") == 0


def test_count_heuristic_yes():
    m = _market(heuristic_direction="YES")
    assert _count_agreeing_signals(m, "YES") == 1
    assert _count_agreeing_signals(m, "NO")  == 0


def test_count_polymarket_yes_gap():
    m = _market(poly={"price_gap": 0.12})
    assert _count_agreeing_signals(m, "YES") == 1
    assert _count_agreeing_signals(m, "NO")  == 0


def test_count_polymarket_no_gap():
    m = _market(poly={"price_gap": -0.10})
    assert _count_agreeing_signals(m, "NO")  == 1
    assert _count_agreeing_signals(m, "YES") == 0


def test_count_polymarket_gap_below_threshold_ignored():
    m = _market(poly={"price_gap": 0.03})
    assert _count_agreeing_signals(m, "YES") == 0


def test_count_external_consensus():
    m = _market(ext_consensus={"consensus_gap": 0.08, "consensus_dir": "YES"})
    assert _count_agreeing_signals(m, "YES") == 1
    assert _count_agreeing_signals(m, "NO")  == 0


def test_count_external_consensus_below_threshold_ignored():
    m = _market(ext_consensus={"consensus_gap": 0.03, "consensus_dir": "YES"})
    assert _count_agreeing_signals(m, "YES") == 0


def test_count_whale_yes():
    m = _market(whale_data={"whale_detected": True, "whale_direction": "YES"})
    assert _count_agreeing_signals(m, "YES") == 1
    assert _count_agreeing_signals(m, "NO")  == 0


def test_count_whale_not_detected_skipped():
    m = _market(whale_data={"whale_detected": False, "whale_direction": "YES"})
    assert _count_agreeing_signals(m, "YES") == 0


def test_count_order_book():
    m = _market(ob_flag=True, ob_direction="NO")
    assert _count_agreeing_signals(m, "NO")  == 1
    assert _count_agreeing_signals(m, "YES") == 0


def test_count_watchlist():
    m = _market(watchlist_signal=True, watchlist_direction="YES")
    assert _count_agreeing_signals(m, "YES") == 1
    assert _count_agreeing_signals(m, "NO")  == 0


def test_count_all_agree_yes():
    """All six sources agree on YES → count = 6."""
    m = _market(
        heuristic_direction="YES",
        poly={"price_gap": 0.15},
        ext_consensus={"consensus_gap": 0.10, "consensus_dir": "YES"},
        whale_data={"whale_detected": True, "whale_direction": "YES"},
        ob_flag=True, ob_direction="YES",
        watchlist_signal=True, watchlist_direction="YES",
    )
    assert _count_agreeing_signals(m, "YES") == 6


def test_count_mixed_sources():
    """Heuristic says YES, whale says NO → YES count = 1, NO count includes whale = 1."""
    m = _market(
        heuristic_direction="YES",
        whale_data={"whale_detected": True, "whale_direction": "NO"},
    )
    assert _count_agreeing_signals(m, "YES") == 1
    assert _count_agreeing_signals(m, "NO")  == 1
