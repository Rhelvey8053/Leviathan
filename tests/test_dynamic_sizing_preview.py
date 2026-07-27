"""
tests/test_dynamic_sizing_preview.py — Offline tests for
analysis/dynamic_sizing_preview.py's compute_preview() (2026-07-27).

Pure function, no DB/network access -- takes a list of row dicts directly.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis.dynamic_sizing_preview import compute_preview


def _row(pnl, stake=None):
    r = {"pnl_if_traded": pnl}
    if stake is not None:
        r["stake_size_hypothetical"] = stake
    return r


def test_empty_rows_gives_zero_totals():
    result = compute_preview([], {"betting": {"unit_size": 10}})
    assert result["n"] == 0
    assert result["flat_total"] == 0.0
    assert result["dynamic_total"] == 0.0
    assert result["delta"] == 0.0


def test_matches_headline_flat_total_at_unit_size():
    """flat_total must equal the same figure calibration.py/README report:
    sum(pnl_if_traded) * unit_size."""
    rows = [_row(0.70, 10.0), _row(-0.30, 10.0), _row(0.20, 10.0)]
    result = compute_preview(rows, {"betting": {"unit_size": 10}})
    assert result["flat_total"] == pytest.approx((0.70 - 0.30 + 0.20) * 10, abs=1e-6)


def test_dynamic_total_uses_stake_size_hypothetical():
    rows = [_row(0.70, stake=15.0), _row(-0.30, stake=5.0)]
    result = compute_preview(rows, {"betting": {"unit_size": 10}})
    assert result["dynamic_total"] == pytest.approx(0.70 * 15.0 + (-0.30) * 5.0)


def test_missing_stake_falls_back_to_unit_size():
    """A resolved row from before stake_size_hypothetical existed (NULL) must
    fall back to the flat unit_size, not raise or silently drop the row."""
    rows = [_row(0.70)]  # no stake_size_hypothetical key at all
    result = compute_preview(rows, {"betting": {"unit_size": 10}})
    assert result["dynamic_total"] == pytest.approx(0.70 * 10)


def test_rows_without_pnl_are_excluded_from_n():
    """Unresolved rows (pnl_if_traded is None) must not count toward n."""
    rows = [_row(0.70, 10.0), {"pnl_if_traded": None, "stake_size_hypothetical": None}]
    result = compute_preview(rows, {"betting": {"unit_size": 10}})
    assert result["n"] == 1


def test_delta_zero_when_ineligible_matches_flat_stake_everywhere():
    """When every row's stake_size_hypothetical equals the flat unit_size
    (the real state until the gate clears), delta must be exactly 0 --
    confirms nothing has silently activated early."""
    rows = [_row(0.70, 10.0), _row(-0.30, 10.0), _row(0.05, 10.0)]
    result = compute_preview(rows, {"betting": {"unit_size": 10}})
    assert result["delta"] == pytest.approx(0.0)
