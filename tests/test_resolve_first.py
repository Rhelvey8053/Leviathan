"""
tests/test_resolve_first.py — Offline tests for analysis/resolve_first.py (Goal 2c PART E).

All tests use a temporary SQLite DB and no network calls.
NO existing test file was modified to accommodate this module.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Patch logger.DB_PATH to a tmp file before importing resolve_first or logger
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()

from core import logger as _logger_mod
_orig_db_path = _logger_mod.DB_PATH
_logger_mod.DB_PATH = _tmp_db.name
_logger_mod._init_db()   # initialise the tmp DB

from analysis.resolve_first import (
    is_two_sided,
    mid_price,
    days_to_close,
    price_band_label,
    select_near_dated,
    dedup_already_logged,
    log_selected,
    print_resolution_status,
    PRICE_BANDS,
    NEEDED_FOR_WINRATE,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _close(days_from_now: float) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days_from_now)
    return dt.isoformat()


def _market(ticker: str = "TEST-TICKER", mid: float = 0.30,
            days: float = 7, volume: float = 5000.0,
            one_sided: bool = False, flag_path: str = "HEURISTIC",
            heuristic_direction: str = "YES", base_rate: float | None = 0.40,
            time_horizon: str = "WEEKLY") -> dict:
    """Build a minimal scored market dict."""
    spread = mid * 0.01  # 1% spread
    yb = mid - spread if not one_sided else 0.0
    ya = mid + spread
    ct = _close(days)
    m = {
        "ticker":              ticker,
        "title":               f"Will {ticker} happen?",
        "yes_bid_dollars":     yb,
        "yes_ask_dollars":     ya,
        "close_time":          ct,
        "volume_fp":           volume,
        "flag_path":           flag_path,
        "heuristic_direction": heuristic_direction,
        "base_rate":           base_rate,
        "raw_edge":            abs((base_rate or mid) - mid) if base_rate is not None else None,
        "net_edge":            None,
        "sig_edge":            True,
        "sig_drift":           False,
        "sig_br_none":         False,
        "short_horizon":       False,
        "time_horizon":        time_horizon,
        "heuristic_label":     "test",
        "flag":                True,
    }
    return m


def _clear_signals() -> None:
    """Delete all rows from the tmp DB signals table."""
    with _logger_mod._db() as conn:
        conn.execute("DELETE FROM signals")


# ── PART E tests ──────────────────────────────────────────────────────────────

class TestSelectNearDated(unittest.TestCase):
    """select_near_dated respects the configured close window."""

    def setUp(self):
        _clear_signals()

    def test_only_picks_within_max_days(self):
        """Markets closing after max_days are excluded."""
        now = datetime.now(timezone.utc)
        markets = [
            _market("INSIDE",  days=10, mid=0.30),
            _market("OUTSIDE", days=20, mid=0.30),
        ]
        selected, _ = select_near_dated(markets, max_days=14, now=now)
        tickers = [m["ticker"] for m in selected]
        self.assertIn("INSIDE",  tickers)
        self.assertNotIn("OUTSIDE", tickers)

    def test_excludes_already_closed(self):
        """Markets with negative days-to-close are excluded."""
        now = datetime.now(timezone.utc)
        markets = [
            _market("CLOSED", days=-1, mid=0.30),
            _market("OPEN",   days=5,  mid=0.30),
        ]
        selected, _ = select_near_dated(markets, max_days=14, now=now)
        tickers = [m["ticker"] for m in selected]
        self.assertNotIn("CLOSED", tickers)
        self.assertIn("OPEN", tickers)

    def test_exactly_at_boundary_included(self):
        """Market closing exactly at max_days is included."""
        now = datetime.now(timezone.utc)
        markets = [_market("BOUNDARY", days=14, mid=0.30)]
        selected, _ = select_near_dated(markets, max_days=14, now=now)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["ticker"], "BOUNDARY")

    def test_one_sided_excluded(self):
        """Markets with one-sided book (bid=0) are excluded."""
        now = datetime.now(timezone.utc)
        markets = [
            _market("ONE_SIDE", days=5, mid=0.30, one_sided=True),
            _market("TWO_SIDE", days=5, mid=0.30, one_sided=False),
        ]
        selected, _ = select_near_dated(markets, max_days=14, now=now)
        tickers = [m["ticker"] for m in selected]
        self.assertNotIn("ONE_SIDE", tickers)
        self.assertIn("TWO_SIDE", tickers)

    def test_stale_ask_only_excluded(self):
        """Market with ask=0 (stale) is excluded from two-sided filter."""
        now = datetime.now(timezone.utc)
        m = _market("STALE", days=5, mid=0.30)
        m["yes_bid_dollars"] = 0.25
        m["yes_ask_dollars"] = 0.0  # stale — no ask
        markets = [m]
        selected, _ = select_near_dated(markets, max_days=14, now=now)
        self.assertEqual(len(selected), 0)

    def test_band_counts_accurate(self):
        """band_counts correctly tallies candidates per price band."""
        now = datetime.now(timezone.utc)
        markets = [
            _market("A", days=5, mid=0.08),  # 5-15%
            _market("B", days=5, mid=0.08),  # 5-15%
            _market("C", days=5, mid=0.25),  # 15-40%
        ]
        # Give each different flag_paths so all three are selected
        markets[0]["flag_path"] = "HEURISTIC"
        markets[1]["flag_path"] = "DRIFT"
        markets[2]["flag_path"] = "HEURISTIC"
        _, band_counts = select_near_dated(markets, max_days=14, now=now)
        self.assertEqual(band_counts.get("5-15%"),  2)
        self.assertEqual(band_counts.get("15-40%"), 1)
        self.assertEqual(band_counts.get("40-60%"), 0)

    def test_price_outside_05_95_excluded(self):
        """Markets with mid < 0.05 or mid > 0.95 are excluded."""
        now = datetime.now(timezone.utc)
        low_m = _market("LOW", days=5, mid=0.03)
        low_m["yes_bid_dollars"] = 0.02
        low_m["yes_ask_dollars"] = 0.04
        high_m = _market("HIGH", days=5, mid=0.97)
        high_m["yes_bid_dollars"] = 0.96
        high_m["yes_ask_dollars"] = 0.98
        markets = [low_m, high_m]
        selected, _ = select_near_dated(markets, max_days=14, now=now)
        self.assertEqual(len(selected), 0)


class TestDedupAlreadyLogged(unittest.TestCase):
    """dedup_already_logged removes tickers already in the DB."""

    def setUp(self):
        _clear_signals()

    def test_rerun_does_not_double_log(self):
        """A ticker logged within lookback_days is removed from candidates."""
        _logger_mod.log_signal({
            "ticker": "ALREADY", "direction": "YES", "market_price": 0.30,
            "confidence": "MED",
        })
        markets = [
            _market("ALREADY", days=5, mid=0.30),
            _market("NEW",     days=5, mid=0.30),
        ]
        result = dedup_already_logged(markets, lookback_days=7)
        tickers = [m["ticker"] for m in result]
        self.assertNotIn("ALREADY", tickers)
        self.assertIn("NEW", tickers)

    def test_ticker_not_in_window_is_kept(self):
        """A ticker logged outside the lookback window is not deduped."""
        # Manually insert with old timestamp
        with _logger_mod._db() as conn:
            import uuid
            conn.execute(
                "INSERT INTO signals (call_id,timestamp,ticker,direction,source) VALUES (?,?,?,?,?)",
                (str(uuid.uuid4())[:8], "2020-01-01T00:00:00+00:00", "OLD", "YES", "paper")
            )
        markets = [_market("OLD", days=5, mid=0.30)]
        result = dedup_already_logged(markets, lookback_days=7)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ticker"], "OLD")

    def test_empty_candidates_list_returns_empty(self):
        result = dedup_already_logged([], lookback_days=7)
        self.assertEqual(result, [])


class TestLogSelected(unittest.TestCase):
    """log_selected persists close_time in every logged row."""

    def setUp(self):
        _clear_signals()

    def test_close_time_stored(self):
        """Logged rows have a non-empty close_time so resolution audit works."""
        ct = _close(10)
        m = _market("TRACK-CT", days=10, mid=0.20)
        m["close_time"] = ct
        m["_mid"] = 0.20
        log_selected([m], run_id="test01")

        with _logger_mod._db() as conn:
            row = conn.execute(
                "SELECT close_time FROM signals WHERE ticker = 'TRACK-CT'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row[0])
        self.assertGreater(len(row[0]), 0)

    def test_direction_yes_when_heuristic_direction_yes(self):
        """Direction=YES when heuristic_direction is YES."""
        m = _market("DIR-YES", days=5, mid=0.25, heuristic_direction="YES")
        m["_mid"] = 0.25
        log_selected([m], run_id="test02")
        with _logger_mod._db() as conn:
            row = conn.execute(
                "SELECT direction FROM signals WHERE ticker = 'DIR-YES'"
            ).fetchone()
        self.assertEqual(row[0], "YES")

    def test_direction_pass_when_neutral_heuristic(self):
        """Direction=PASS when heuristic_direction is NEUTRAL."""
        m = _market("DIR-PASS", days=5, mid=0.50)
        m["heuristic_direction"] = "NEUTRAL"
        m["_mid"] = 0.50
        log_selected([m], run_id="test03")
        with _logger_mod._db() as conn:
            row = conn.execute(
                "SELECT direction FROM signals WHERE ticker = 'DIR-PASS'"
            ).fetchone()
        self.assertEqual(row[0], "PASS")

    def test_returns_count_of_logged(self):
        """log_selected returns the count of logged rows."""
        markets = [
            _market("LOG-A", days=5, mid=0.10),
            _market("LOG-B", days=5, mid=0.20),
        ]
        for m in markets:
            m["_mid"] = mid_price(m) or 0.15
        count = log_selected(markets, run_id="test04")
        self.assertEqual(count, 2)


class TestPrintResolutionStatus(unittest.TestCase):
    """print_resolution_status refuses win-rate when resolved < 10."""

    def setUp(self):
        _clear_signals()

    def _capture_status(self) -> str:
        buf = StringIO()
        with patch("sys.stdout", buf):
            print_resolution_status()
        return buf.getvalue()

    def test_not_meaningful_below_10(self):
        """Status report prints NOT YET MEANINGFUL when resolved < 10."""
        output = self._capture_status()
        self.assertIn("NOT YET MEANINGFUL", output)
        self.assertNotIn("WIN RATE:", output.replace("WIN RATE: NOT YET MEANINGFUL", ""))

    def test_win_rate_shown_at_10_resolved(self):
        """Status report shows win rate (as a number) when resolved >= 10."""
        import uuid
        with _logger_mod._db() as conn:
            for i in range(10):
                conn.execute("""
                    INSERT INTO signals
                    (call_id,timestamp,ticker,direction,market_price,our_estimate,
                     outcome,result,pnl_if_traded,source)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    str(uuid.uuid4())[:8],
                    datetime.now(timezone.utc).isoformat(),
                    f"RESOLVED-{i}",
                    "YES", 0.30, 0.50, "YES", "WIN", 0.70, "paper"
                ))
        output = self._capture_status()
        self.assertNotIn("NOT YET MEANINGFUL", output)
        self.assertIn("WIN RATE:", output)

    def test_no_win_rate_percentage_when_n_lt_10(self):
        """No percentage like '50.0%' shown when n < 10."""
        import uuid, re
        with _logger_mod._db() as conn:
            for i in range(5):
                conn.execute("""
                    INSERT INTO signals
                    (call_id,timestamp,ticker,direction,market_price,our_estimate,
                     outcome,result,pnl_if_traded,source)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    str(uuid.uuid4())[:8],
                    datetime.now(timezone.utc).isoformat(),
                    f"PARTIAL-{i}",
                    "YES", 0.30, 0.50, "YES", "WIN", 0.70, "paper"
                ))
        output = self._capture_status()
        # Should NOT have a percentage like "60.0%" — only NOT YET MEANINGFUL
        self.assertIn("NOT YET MEANINGFUL", output)
        self.assertNotRegex(output, r"\d+\.\d+%")


class TestSelectNearDatedNoBidAsk(unittest.TestCase):
    """Edge cases for two-sided book detection."""

    def test_zero_bid_excluded(self):
        """Market with bid=0 but ask>0 excluded (one-sided)."""
        now = datetime.now(timezone.utc)
        m = _market("ZERO-BID", days=5, mid=0.30)
        m["yes_bid_dollars"] = 0.0
        m["yes_ask_dollars"] = 0.32
        selected, _ = select_near_dated([m], max_days=14, now=now)
        self.assertEqual(len(selected), 0)

    def test_zero_ask_excluded(self):
        """Market with ask=0 but bid>0 excluded (one-sided)."""
        now = datetime.now(timezone.utc)
        m = _market("ZERO-ASK", days=5, mid=0.30)
        m["yes_bid_dollars"] = 0.28
        m["yes_ask_dollars"] = 0.0
        selected, _ = select_near_dated([m], max_days=14, now=now)
        self.assertEqual(len(selected), 0)


if __name__ == "__main__":
    unittest.main()
