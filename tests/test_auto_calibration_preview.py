"""
tests/test_auto_calibration_preview.py — Offline tests for
analysis/auto_calibration_preview.py's compute_calibration_weights() and
compute_preview() (2026-09-07, auto-calibration-loop shadow mode).

Pure functions, no DB/network access -- take row dicts directly.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis.auto_calibration_preview import compute_calibration_weights, compute_preview
from core import sizing


def _label_row(label, total, wins, avg_edge=0.1, total_pnl=1.0):
    return {
        "heuristic_label": label, "total": total, "wins": wins,
        "losses": total - wins, "win_rate": round(wins / total * 100, 1) if total else None,
        "avg_edge": avg_edge, "total_pnl": total_pnl,
    }


def _track_row(label, pnl, stake=None):
    r = {"heuristic_label": label, "pnl_if_traded": pnl}
    if stake is not None:
        r["stake_size_hypothetical"] = stake
    return r


# ─── compute_calibration_weights ───────────────────────────────────────────

def test_below_per_category_floor_gets_no_adjustment():
    n = sizing.MIN_RESOLVED_PER_CATEGORY - 1
    rows = [_label_row("SCOTUS", n, wins=n)]  # even 100% win rate
    out = compute_calibration_weights(rows)
    assert out[0]["suggested_multiplier"] == 1.0
    assert out[0]["wilson_lower"] is None


def test_none_win_rate_gets_no_adjustment():
    rows = [_label_row("EMPTY", 0, 0)]
    out = compute_calibration_weights(rows)
    assert out[0]["suggested_multiplier"] == 1.0


def test_strong_win_rate_at_sufficient_n_suggests_upweight():
    n = sizing.MIN_RESOLVED_PER_CATEGORY * 4
    rows = [_label_row("STRONG", n, wins=int(n * 0.75))]
    out = compute_calibration_weights(rows)
    assert out[0]["suggested_multiplier"] > 1.0
    assert out[0]["wilson_lower"] is not None


def test_weak_win_rate_at_sufficient_n_suggests_downweight():
    n = sizing.MIN_RESOLVED_PER_CATEGORY * 4
    rows = [_label_row("WEAK", n, wins=int(n * 0.25))]
    out = compute_calibration_weights(rows)
    assert out[0]["suggested_multiplier"] < 1.0


def test_multiplier_capped_within_floor_and_ceiling():
    n = sizing.MIN_RESOLVED_PER_CATEGORY * 20
    strong = [_label_row("MAXED", n, wins=n)]  # 100% win rate, huge n
    out = compute_calibration_weights(strong)
    from analysis.auto_calibration_preview import MULTIPLIER_CEIL, MULTIPLIER_FLOOR
    assert out[0]["suggested_multiplier"] <= MULTIPLIER_CEIL
    assert out[0]["suggested_multiplier"] >= MULTIPLIER_FLOOR


def test_small_n_above_floor_produces_muted_adjustment_vs_large_n():
    """Same win rate, different n: the barely-qualifying n's suggestion must be
    closer to 1.0 than the much-larger n's -- the Wilson interval width, not a
    separate hand-tuned damping rule, is what mutes small samples."""
    small_n = sizing.MIN_RESOLVED_PER_CATEGORY
    large_n = sizing.MIN_RESOLVED_PER_CATEGORY * 10
    small = compute_calibration_weights([_label_row("SMALL", small_n, wins=int(small_n * 0.75))])[0]
    large = compute_calibration_weights([_label_row("LARGE", large_n, wins=int(large_n * 0.75))])[0]
    assert abs(small["suggested_multiplier"] - 1.0) < abs(large["suggested_multiplier"] - 1.0)


def test_wide_ci_straddling_neutral_never_flips_sign():
    """A weak point estimate (<50%) whose CI upper bound crosses 50% (small n,
    real 'weather' case: win_rate=31.2%, n=16) must never produce an upweight
    -- the multiplier can be muted all the way to 1.0, but never cross to the
    wrong side of neutral relative to the point estimate."""
    rows = [_label_row("WEATHER", 16, wins=5)]  # 31.25% win rate
    out = compute_calibration_weights(rows)
    assert out[0]["suggested_multiplier"] <= 1.0


def test_does_not_mutate_input_rows():
    rows = [_label_row("SCOTUS", sizing.MIN_RESOLVED_PER_CATEGORY * 2, wins=10)]
    original = dict(rows[0])
    compute_calibration_weights(rows)
    assert rows[0] == original


# ─── compute_preview ────────────────────────────────────────────────────────

def test_empty_rows_gives_zero_totals():
    result = compute_preview([], [])
    assert result == {"n": 0, "baseline_total": 0.0, "calibrated_total": 0.0, "delta": 0.0}


def test_unweighted_label_falls_back_to_multiplier_one():
    rows = [_track_row("UNKNOWN_LABEL", 0.5, stake=10.0)]
    result = compute_preview(rows, weighted_labels=[])
    assert result["baseline_total"] == pytest.approx(5.0)
    assert result["calibrated_total"] == pytest.approx(5.0)
    assert result["delta"] == pytest.approx(0.0)


def test_calibrated_total_applies_label_multiplier_on_top_of_existing_stake():
    weighted = [{"heuristic_label": "SCOTUS", "suggested_multiplier": 1.2}]
    rows = [_track_row("SCOTUS", 0.5, stake=10.0)]
    result = compute_preview(rows, weighted)
    assert result["baseline_total"] == pytest.approx(5.0)
    assert result["calibrated_total"] == pytest.approx(6.0)
    assert result["delta"] == pytest.approx(1.0)


def test_missing_stake_falls_back_to_one():
    weighted = [{"heuristic_label": "SCOTUS", "suggested_multiplier": 1.2}]
    rows = [_track_row("SCOTUS", 0.5)]  # no stake_size_hypothetical key at all
    result = compute_preview(rows, weighted)
    assert result["baseline_total"] == pytest.approx(0.5)
    assert result["calibrated_total"] == pytest.approx(0.6)


def test_rows_without_pnl_are_excluded_from_n():
    rows = [_track_row("SCOTUS", 0.5, stake=10.0), {"heuristic_label": "SCOTUS", "pnl_if_traded": None}]
    result = compute_preview(rows, [])
    assert result["n"] == 1
