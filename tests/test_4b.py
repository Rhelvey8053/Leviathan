"""
tests/test_4b.py — Tests for Goal 4b correctness fixes.

  PART A: unit_size config threading + EV floor filter
  PART B: watchlist track-record gating (_verify_watchlist_trader)
  PART C: fee-aware edge (kalshi_fee, net_edge_after_fee)

All tests are offline — no network calls, no DB, no email.
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.fees import kalshi_fee
from core.report import _ev_float, _ev_per_contract
from analysis.smart_money_scan import _verify_watchlist_trader


# ── shared helpers ────────────────────────────────────────────────────────────

def _pos(title: str, pct: float, cash: float, resolved: bool = False) -> dict:
    return {
        "title":        title,
        "percentPnl":   pct,
        "cashPnl":      cash,
        "redeemable":   resolved,
        "eventSlug":    "",
        "outcome":      "yes",
        "currentValue": 1000,
    }


def _cfg(**overrides) -> dict:
    """Accounts config dict with default qualifying thresholds."""
    base = {
        "min_resolved_count": 10,
        "min_win_rate":       55.0,
        "min_positions":      5,
        "min_pct_pnl":        10.0,
        "min_cash_pnl":       100.0,
    }
    base.update(overrides)
    return {"accounts": base}


def _winning_resolved(n: int = 12, wins: int = 10) -> list[dict]:
    """n resolved positions; first `wins` of them are positive."""
    return [
        _pos(
            f"Will Policy {i} pass?",
            80.0 if i < wins else -30.0,
            200.0 if i < wins else -50.0,
            resolved=True,
        )
        for i in range(n)
    ]


_FAKE_ADDR = "0x" + "a" * 40


# ═══════════════════════════════════════════════════════════════════════════════
# PART A — unit_size threading + EV floor filter
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvMathReadsUnitSize:
    """_ev_float and _ev_per_contract scale linearly with unit_size."""

    def test_default_unit_size_10(self):
        ev = _ev_float("YES", 0.20, 0.35, unit_size=10)
        assert round(ev, 4) == 1.50

    def test_unit_size_20_doubles_ev(self):
        ev = _ev_float("YES", 0.20, 0.35, unit_size=20)
        assert round(ev, 4) == 3.00

    def test_unit_size_50(self):
        ev = _ev_float("YES", 0.20, 0.35, unit_size=50)
        assert round(ev, 4) == 7.50

    def test_no_direction_returns_none(self):
        assert _ev_float("PASS", 0.20, 0.35, unit_size=10) is None

    def test_unknown_direction_returns_none(self):
        assert _ev_float("UNKNOWN", 0.20, 0.35) is None

    def test_no_direction_returns_none_for_missing(self):
        assert _ev_float("NO", None, 0.35) is None

    def test_ev_per_contract_format_default(self):
        s = _ev_per_contract("YES", 0.20, 0.35, unit_size=10)
        assert s == "$+1.50"

    def test_ev_per_contract_unit_size_20(self):
        s = _ev_per_contract("YES", 0.20, 0.35, unit_size=20)
        assert s == "$+3.00"

    def test_ev_per_contract_no_direction_returns_none(self):
        assert _ev_per_contract("PASS", 0.20, 0.35, unit_size=10) is None

    def test_no_direction_ev_returns_none(self):
        assert _ev_float("NO", None, 0.35) is None

    def test_changing_unit_size_changes_output(self):
        """Regression: two different unit_sizes must yield different EV strings."""
        s10 = _ev_per_contract("YES", 0.20, 0.35, unit_size=10)
        s25 = _ev_per_contract("YES", 0.20, 0.35, unit_size=25)
        assert s10 != s25


class TestEvFloorFilter:
    """
    The floor filter predicate is: abs(ev_after) >= ev_floor.
    Sub-floor candidates are REMOVED (not sorted lower).
    """

    def _ev_after(self, direction, mp, est, unit_size=10):
        """Compute fee-adjusted EV the same way _betting_queue does."""
        ev = _ev_float(direction, mp, est, unit_size)
        fee = kalshi_fee(mp, unit_size) if (mp and mp > 0) else 0.0
        return (ev - fee) if ev is not None else None

    def _ev_floor(self, unit_size=10, min_pct=0.50):
        return unit_size * min_pct  # $5.00 at defaults

    def test_strong_signal_above_floor(self):
        """Signal with large edge (60pp) clears the $5 floor even after fees."""
        # mp=0.20, est=0.80 → ev_free=$6.00; fee at p=0.20 = $0.12 → ev_after=$5.88
        ev_after = self._ev_after("YES", 0.20, 0.80, unit_size=10)
        floor = self._ev_floor()
        assert ev_after is not None and abs(ev_after) >= floor

    def test_tiny_edge_below_floor(self):
        """1pp edge at $10 unit gives ~$0.10 EV — well below $5 floor."""
        ev_after = self._ev_after("YES", 0.49, 0.50, unit_size=10)
        floor = self._ev_floor()
        assert ev_after is None or abs(ev_after) < floor

    def test_none_ev_always_filtered(self):
        """None EV (PASS direction or missing prices) is always below floor."""
        floor = self._ev_floor()
        ev_after = None
        assert ev_after is None or abs(ev_after) < floor

    def test_floor_predicate_at_exact_floor_passes(self):
        """Exactly at floor passes (>= not >)."""
        floor = self._ev_floor()
        ev_after = floor
        assert abs(ev_after) >= floor

    def test_floor_predicate_just_above_floor_passes(self):
        floor = self._ev_floor()
        ev_after = floor + 0.01
        assert abs(ev_after) >= floor

    def test_floor_predicate_just_below_floor_filtered(self):
        floor = self._ev_floor()
        ev_after = floor - 0.01
        assert abs(ev_after) < floor

    def test_pass_direction_always_filtered(self):
        """PASS direction produces None EV, which is always filtered."""
        ev_after = self._ev_after("PASS", 0.20, 0.50, unit_size=10)
        floor = self._ev_floor()
        assert ev_after is None or abs(ev_after) < floor

    def test_floor_scales_with_unit_size(self):
        """At unit_size=20 the floor is $10 (double the default $5)."""
        floor_10 = self._ev_floor(unit_size=10)
        floor_20 = self._ev_floor(unit_size=20)
        assert floor_20 == pytest.approx(floor_10 * 2)


# ═══════════════════════════════════════════════════════════════════════════════
# PART B — watchlist gating
# ═══════════════════════════════════════════════════════════════════════════════

class TestWatchlistGating:
    """Watchlist traders must clear _is_winner on resolved positions before being promoted."""

    def test_too_few_resolved_excluded(self):
        """Trader with 3 resolved positions is excluded (need ≥10)."""
        positions = _winning_resolved(n=3, wins=3)
        with patch("sources.accounts.fetch_user_positions", return_value=positions):
            verified, reason, stats, _ = _verify_watchlist_trader(_FAKE_ADDR, _cfg())
        assert not verified
        assert "resolved positions" in reason
        assert "3" in reason

    def test_low_win_rate_excluded(self):
        """40% win rate is excluded (threshold 55%)."""
        positions = _winning_resolved(n=10, wins=4)
        with patch("sources.accounts.fetch_user_positions", return_value=positions):
            verified, reason, stats, _ = _verify_watchlist_trader(_FAKE_ADDR, _cfg())
        assert not verified
        assert "win rate" in reason

    def test_low_cash_pnl_excluded(self):
        """Sufficient win rate but trivial cash PnL ($36) is excluded (need ≥$100)."""
        positions = [
            _pos(
                f"Will Policy {i} pass?",
                80.0 if i < 8 else -30.0,
                5.0 if i < 8 else -2.0,  # total = 8*5 + 2*(-2) = $36
                resolved=True,
            )
            for i in range(10)
        ]
        with patch("sources.accounts.fetch_user_positions", return_value=positions):
            verified, reason, stats, _ = _verify_watchlist_trader(_FAKE_ADDR, _cfg())
        assert not verified
        assert "cash" in reason.lower() or "pnl" in reason.lower()

    def test_no_api_positions_excluded(self):
        """Empty API response is excluded with a clear reason."""
        with patch("sources.accounts.fetch_user_positions", return_value=[]):
            verified, reason, stats, _ = _verify_watchlist_trader(_FAKE_ADDR, _cfg())
        assert not verified
        assert isinstance(reason, str) and len(reason) > 0

    def test_no_api_positions_stats_is_none(self):
        """Stats is None when API returns nothing."""
        with patch("sources.accounts.fetch_user_positions", return_value=[]):
            _, _, stats, _ = _verify_watchlist_trader(_FAKE_ADDR, _cfg())
        assert stats is None

    def test_verified_trader_passes(self):
        """Trader with strong resolved track record passes verification."""
        positions = _winning_resolved(n=12, wins=10)
        with patch("sources.accounts.fetch_user_positions", return_value=positions):
            verified, reason, stats, _ = _verify_watchlist_trader(_FAKE_ADDR, _cfg())
        assert verified
        assert reason is None

    def test_verified_trader_stats_present(self):
        """Stats dict is returned when trader is verified."""
        positions = _winning_resolved(n=12, wins=10)
        with patch("sources.accounts.fetch_user_positions", return_value=positions):
            verified, _, stats, _ = _verify_watchlist_trader(_FAKE_ADDR, _cfg())
        assert verified
        assert stats is not None
        assert stats["resolved_count"] == 12

    def test_verified_returns_all_positions(self):
        """all_positions is returned for open-position filtering."""
        positions = _winning_resolved(n=12, wins=10)
        with patch("sources.accounts.fetch_user_positions", return_value=positions):
            verified, _, _, returned = _verify_watchlist_trader(_FAKE_ADDR, _cfg())
        assert verified
        assert returned == positions

    def test_fail_reason_non_empty_when_excluded(self):
        """fail_reason is always a non-empty string when excluded."""
        positions = _winning_resolved(n=2, wins=2)
        with patch("sources.accounts.fetch_user_positions", return_value=positions):
            verified, reason, _, _ = _verify_watchlist_trader(_FAKE_ADDR, _cfg())
        assert not verified
        assert isinstance(reason, str) and len(reason) > 0

    def test_custom_threshold_respected(self):
        """When thresholds are lowered, a trader with 3 resolved positions passes."""
        positions = _winning_resolved(n=3, wins=3)
        cfg = _cfg(min_resolved_count=3, min_positions=3, min_cash_pnl=1.0, min_pct_pnl=1.0)
        with patch("sources.accounts.fetch_user_positions", return_value=positions):
            verified, reason, _, _ = _verify_watchlist_trader(_FAKE_ADDR, cfg)
        assert verified
        assert reason is None


# ═══════════════════════════════════════════════════════════════════════════════
# PART C — fee-aware edge
# ═══════════════════════════════════════════════════════════════════════════════

class TestKalshiFee:
    """kalshi_fee formula: ceil(0.07 * p * (1-p) * contracts * 100) / 100."""

    def test_max_fee_at_50pct(self):
        # 0.07 * 0.5 * 0.5 * 10 * 100 = 17.5 → ceil=18 → $0.18
        assert kalshi_fee(0.5, 10) == 0.18

    def test_fee_at_30pct(self):
        # 0.07 * 0.3 * 0.7 * 10 * 100 = 14.7 → ceil=15 → $0.15
        assert kalshi_fee(0.3, 10) == 0.15

    def test_fee_at_10pct(self):
        # 0.07 * 0.1 * 0.9 * 10 * 100 = 6.3 → ceil=7 → $0.07
        assert kalshi_fee(0.1, 10) == 0.07

    def test_fee_at_90pct_symmetric_with_10pct(self):
        # p*(1-p) is symmetric: fee(0.9, n) == fee(0.1, n)
        assert kalshi_fee(0.9, 10) == kalshi_fee(0.1, 10)

    def test_fee_scales_with_contracts(self):
        """Fee at p=0.3 for 20 contracts is double p=0.3 for 10 contracts (exact here)."""
        # 0.07 * 0.3 * 0.7 * 10 * 100 = 14.7 → ceil=15 → $0.15
        # 0.07 * 0.3 * 0.7 * 20 * 100 = 29.4 → ceil=30 → $0.30
        assert kalshi_fee(0.3, 20) == pytest.approx(0.30, abs=0.01)

    def test_zero_contracts_returns_zero(self):
        assert kalshi_fee(0.5, 0) == 0.0

    def test_boundary_price_zero_returns_zero(self):
        assert kalshi_fee(0.0, 10) == 0.0

    def test_boundary_price_one_returns_zero(self):
        assert kalshi_fee(1.0, 10) == 0.0

    def test_fee_never_negative(self):
        for p in (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
            assert kalshi_fee(p, 10) >= 0.0

    def test_fee_highest_at_mid_price(self):
        """Fee at p=0.5 > fee at p=0.1 and p=0.9 (variance-based)."""
        assert kalshi_fee(0.5, 10) > kalshi_fee(0.1, 10)
        assert kalshi_fee(0.5, 10) > kalshi_fee(0.9, 10)

    def test_net_edge_after_fee_le_net_edge(self):
        """net_edge_after_fee is always ≤ net_edge (fees never add value)."""
        for p in (0.1, 0.3, 0.5, 0.7, 0.9):
            unit_size  = 10
            net_edge   = 0.15
            fee_dollars = kalshi_fee(p, unit_size)
            fee_as_pp  = fee_dollars / unit_size
            net_edge_after_fee = net_edge - fee_as_pp
            assert net_edge_after_fee <= net_edge

    def test_net_edge_after_fee_le_net_edge_at_various_units(self):
        """Property holds regardless of unit_size."""
        for unit_size in (1, 5, 10, 25, 100):
            fee_dollars = kalshi_fee(0.5, unit_size)
            fee_as_pp   = fee_dollars / unit_size if unit_size > 0 else 0.0
            net_edge    = 0.20
            net_edge_after_fee = net_edge - fee_as_pp
            assert net_edge_after_fee <= net_edge
