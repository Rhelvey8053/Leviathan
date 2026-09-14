"""
Tests for analysis/track_record.py's P&L computation.

backlog: per-group-pnl-tables-still-flat-not-stake-weighted found this
file's own _print_section()/COMBINED SUMMARY totals as a second instance
of the same bug class that commit 5cca7f0 fixed in the 12 get_stats_by_*()
functions -- this file computes its totals independently from raw rows,
so that fix never touched it. _row_pnl_dollars() is the fix: same
COALESCE(stake_size_hypothetical, 10.0) convention as everywhere else.

No DB, no network -- pure function tests against plain dicts.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from analysis.track_record import _row_pnl_dollars, _fmt_dollars


def _row(pnl_if_traded, stake=None):
    d = {"pnl_if_traded": pnl_if_traded}
    if stake is not None:
        d["stake_size_hypothetical"] = stake
    return d


# ─── _row_pnl_dollars ──────────────────────────────────────────────────────

def test_row_pnl_uses_its_own_stake():
    """A row with a real stake_size_hypothetical uses that, not the default."""
    row = _row(0.30, stake=50.0)
    assert _row_pnl_dollars(row, default_stake=10.0) == pytest.approx(15.0)


def test_row_pnl_falls_back_to_default_stake_when_missing():
    """Pre-2026-09-08 rows with no backfilled stake use the fallback."""
    row = _row(0.30)  # no stake_size_hypothetical key at all
    assert _row_pnl_dollars(row, default_stake=10.0) == pytest.approx(3.0)


def test_row_pnl_none_stake_falls_back_to_default():
    row = {"pnl_if_traded": 0.30, "stake_size_hypothetical": None}
    assert _row_pnl_dollars(row, default_stake=10.0) == pytest.approx(3.0)


def test_row_pnl_negative_pnl():
    row = _row(-0.20, stake=50.0)
    assert _row_pnl_dollars(row, default_stake=10.0) == pytest.approx(-10.0)


def test_row_pnl_missing_pnl_if_traded_returns_none():
    assert _row_pnl_dollars({}, default_stake=10.0) is None


def test_row_pnl_zero_pnl_is_zero_not_none():
    """PASS rows have pnl_if_traded=0 -- must contribute $0, not be excluded."""
    row = _row(0.0, stake=50.0)
    assert _row_pnl_dollars(row, default_stake=10.0) == pytest.approx(0.0)


def test_row_pnl_different_stakes_scale_differently():
    """The whole point of the fix: two rows with identical pnl_if_traded but
    different confidence-derived stakes must NOT show the same dollar P&L."""
    high_conf = _row(0.40, stake=75.0)   # HIGH confidence, 1.5x multiplier at $50 unit
    low_conf  = _row(0.40, stake=25.0)   # LOW confidence, 0.5x multiplier at $50 unit
    assert _row_pnl_dollars(high_conf, 10.0) == pytest.approx(30.0)
    assert _row_pnl_dollars(low_conf, 10.0)  == pytest.approx(10.0)
    assert _row_pnl_dollars(high_conf, 10.0) != _row_pnl_dollars(low_conf, 10.0)


# ─── total aggregation (mirrors _print_section's / COMBINED SUMMARY's own sum) ─

def test_total_pnl_sums_stake_weighted_rows_not_flat():
    """A section total must equal the sum of each row's OWN stake-weighted
    P&L, not len(rows) * a single flat per-contract value -- the exact
    inconsistency this fix closes (same signals showing different dollar
    totals in different parts of the same report)."""
    rows = [_row(0.30, stake=50.0), _row(-0.10, stake=75.0), _row(0.20, stake=25.0)]
    total = sum(p for r in rows if (p := _row_pnl_dollars(r, 10.0)) is not None)
    expected = (0.30 * 50.0) + (-0.10 * 75.0) + (0.20 * 25.0)
    assert total == pytest.approx(expected)
    # A flat-unit_size computation (the old, buggy behavior) would have
    # given a materially different number -- confirm the fix actually
    # changes the answer, not just refactors the same result.
    flat_wrong = sum(r["pnl_if_traded"] for r in rows) * 50.0
    assert total != pytest.approx(flat_wrong)


def test_total_pnl_skips_none_pnl_rows():
    rows = [_row(0.30, stake=50.0), {"stake_size_hypothetical": 50.0}]  # no pnl_if_traded
    total = sum(p for r in rows if (p := _row_pnl_dollars(r, 10.0)) is not None)
    assert total == pytest.approx(15.0)


# ─── _fmt_dollars ────────────────────────────────────────────────────────

def test_fmt_dollars_no_double_multiplication():
    """_fmt_dollars must print a stake-weighted value as-is -- no
    per-contract multiplier, unlike the removed _fmt_pnl()."""
    assert _fmt_dollars(15.0) == "$+15.00"
    assert _fmt_dollars(-3.5) == "$-3.50"


def test_fmt_dollars_handles_bad_input():
    assert _fmt_dollars(None) == "—"
    assert _fmt_dollars("not a number") == "—"
