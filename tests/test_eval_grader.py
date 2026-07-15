"""
tests/test_eval_grader.py — Tests for analysis/eval_grader.py.

Pure arithmetic, no DB, no network. Includes a reproduction of the
hand-calculated Brier score from the goal-6 reconciliation (8 resolved
signals, all outcome=NO) to lock in the exact expected value.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis import eval_grader


# ─── brier_score ──────────────────────────────────────────────────────────────

def test_brier_score_empty():
    assert eval_grader.brier_score([]) is None


def test_brier_score_perfect_calibration():
    assert eval_grader.brier_score([(1.0, 1), (0.0, 0)]) == 0.0


def test_brier_score_worst_case():
    assert eval_grader.brier_score([(1.0, 0), (0.0, 1)]) == 1.0


def test_brier_score_random_baseline():
    assert eval_grader.brier_score([(0.5, 1), (0.5, 0)]) == 0.25


def test_brier_score_reconciled_dataset():
    """
    The 8 real resolved signals as of 2026-07-14 (all outcome=NO). One
    high-conviction miss (est 0.65) dominates; the other 7 are near-zero
    error. Hand-calculated total: 0.462354 / 8 = 0.05779425.
    """
    pairs = [
        (0.65,  0), (0.15,  0), (0.10,  0), (0.002, 0),
        (0.07,  0), (0.04,  0), (0.025, 0), (0.015, 0),
    ]
    brier = eval_grader.brier_score(pairs)
    assert abs(brier - 0.05779425) < 1e-8


# ─── hit_rate ─────────────────────────────────────────────────────────────────

def test_hit_rate_empty():
    assert eval_grader.hit_rate([]) is None


def test_hit_rate_all_correct():
    assert eval_grader.hit_rate([(0.9, 1), (0.1, 0)]) == 1.0


def test_hit_rate_all_wrong():
    assert eval_grader.hit_rate([(0.9, 0), (0.1, 1)]) == 0.0


def test_hit_rate_boundary_counts_as_yes():
    """estimate == threshold predicts YES."""
    assert eval_grader.hit_rate([(0.5, 1)], threshold=0.5) == 1.0


def test_hit_rate_custom_threshold():
    assert eval_grader.hit_rate([(0.3, 0)], threshold=0.5) == 1.0


# ─── calibration_by_decile ────────────────────────────────────────────────────

def test_calibration_by_decile_buckets_correctly():
    pairs = [(0.05, 0), (0.65, 0), (0.95, 1)]
    buckets = eval_grader.calibration_by_decile(pairs)
    ranges = {b["range"] for b in buckets}
    assert "0.0-0.1" in ranges
    assert "0.6-0.7" in ranges
    assert "0.9-1.0" in ranges


def test_calibration_by_decile_estimate_of_one_in_last_bucket():
    buckets = eval_grader.calibration_by_decile([(1.0, 1)])
    assert len(buckets) == 1
    assert buckets[0]["range"] == "0.9-1.0"


def test_calibration_by_decile_reports_mean_and_actual_rate():
    pairs = [(0.60, 0), (0.65, 0)]
    buckets = eval_grader.calibration_by_decile(pairs)
    assert len(buckets) == 1
    b = buckets[0]
    assert b["n"] == 2
    assert abs(b["mean_estimate"] - 0.625) < 1e-9
    assert b["actual_rate"] == 0.0


def test_calibration_by_decile_empty():
    assert eval_grader.calibration_by_decile([]) == []


# ─── grade ────────────────────────────────────────────────────────────────────

def test_grade_combines_all_three():
    pairs = [(0.9, 1), (0.1, 0)]
    report = eval_grader.grade(pairs)
    assert report["n"] == 2
    assert report["brier"] == eval_grader.brier_score(pairs)
    assert report["hit_rate"] == eval_grader.hit_rate(pairs)
    assert report["calibration_by_decile"] == eval_grader.calibration_by_decile(pairs)
