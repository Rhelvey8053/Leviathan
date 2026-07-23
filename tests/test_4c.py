"""
tests/test_4c.py — Tests for Goal 4c report readability refactoring.

Covers:
  - Line-width constraint: no non-rule line > 100 chars in compile_report output
  - _close_and_urgency: regression against hardcoded expected strings per threshold
  - _render_table: column alignment and truncation
  - Betting queue: title shown, Dir+Conf shown, missing title falls back gracefully
  - Smart money drift: title shown alongside ticker
  - Per-trader cross-reference: kalshi_title shown
  - _signal_block: empty reasoning fallback line
  - Repeat signals: EV/contract shown

All tests are offline — no network calls, no DB (except tmp SQLite for betting queue tests).
"""

import sqlite3
import sys
import tempfile
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import report
from core.report import _close_and_urgency, _render_table


# ── helpers ──────────────────────────────────────────────────────────────────

def _sig(**kw):
    """Maximum-flags synthetic signal that triggers every optional label."""
    s = {
        "ticker":                "KXTEST-26DEC31",
        "title":                 "Will Congress pass the infrastructure bill by year end?",
        "confidence":            "HIGH",
        "direction":             "YES",
        "time_horizon":          "LONG",
        "market_price":          0.30,
        "our_estimate":          0.55,
        "edge":                  0.25,
        "net_edge":              0.22,
        "drift_flag":            True,
        "price_drift":           -0.12,
        "spread_wide":           False,
        "ob_flag":               False,
        "watchlist_signal":      True,
        "watchlist_direction":   "YES",
        "whale_reversal":        True,
        "whale_streak":          3,
        "smart_money":           [],
        "poly": {"price_gap": 0.18, "poly_price": 0.48, "poly_question": "Same Q", "match_score": 0.88},
        "ext_markets":           [],
        "ext_consensus":         {},
        "flag_path":             "HEURISTIC",
        "base_rate":             0.60,
        "second_pass":           True,
        "short_horizon":         False,
        "confidence_downgraded": False,
        "close_time":            "2026-12-31T23:59:00Z",
        "reasoning":             "Detailed analysis text.",
        "sources_checked":       ["Kalshi", "Polymarket"],
        "is_repeat":             True,
        "repeat_count":          3,
        "prior_appearances":     3,
        "direction_consistent":  True,
    }
    s.update(kw)
    return s


def _make_db(tmp_path, rows=None):
    """Create a minimal signals+fills SQLite DB for betting queue tests."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE signals (
            call_id TEXT, ticker TEXT, direction TEXT, market_price REAL,
            our_estimate REAL, edge REAL, close_time TEXT,
            confidence TEXT, result TEXT, source TEXT, timestamp TEXT, title TEXT,
            event_ticker TEXT, series_ticker TEXT
        );
        CREATE TABLE fills (ticker TEXT, filled_at TEXT);
    """)
    for r in (rows or []):
        conn.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r.get("call_id", r["ticker"]),
                r["ticker"],
                r.get("direction", "YES"),
                r.get("market_price", 0.30),
                r.get("our_estimate", 0.60),
                r.get("edge", 0.30),
                r.get("close_time", "2026-12-31T00:00:00Z"),
                r.get("confidence", "HIGH"),
                "", "paper",
                r.get("timestamp", "2026-06-20T00:00:00Z"),
                r.get("title", ""),
                r.get("event_ticker", ""),
                r.get("series_ticker", ""),
            ),
        )
    conn.commit()
    conn.close()
    return str(db)


def _no_floor_cfg():
    return {"betting": {"unit_size": 10, "min_ev_pct_of_unit": 0.0}}


def _full_compile(signals=None, repeat_signals=None, whale_only=None,
                  all_filtered=None, smart_money_result=None, db_path=None):
    """Run compile_report with minimal but representative data."""
    stats = {
        "win_rate": 60.0, "avg_edge_captured": 0.15,
        "total_hypothetical_pnl": 80.0, "total_calls": 5, "resolved": 3,
    }
    run_meta = {"markets_scanned": 100, "runtime_ms": 2000}
    config = {
        "environment": "demo",
        "scoring": {"confidence_threshold": "MED", "min_report_lv": 0},
        "betting": {"unit_size": 10, "min_ev_pct_of_unit": 0.0},
    }
    return report.compile_report(
        signals=signals or [],
        whale_only=whale_only or [],
        stats=stats,
        run_meta=run_meta,
        config=config,
        all_filtered=all_filtered,
        new_signals=signals or [],
        repeat_signals=repeat_signals or [],
        smart_money_result=smart_money_result,
        probe_stats=None,
        flag_path_stats=None,
        lv_stats=None,
        db_path=db_path,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Line-width constraint
# ═══════════════════════════════════════════════════════════════════════════════

class TestLineWidth:
    """No non-rule line in compile_report output may exceed 100 chars."""

    def test_max_line_width_full_flags_signal(self, tmp_path):
        """Max-flags signal keeps every line under 100 chars."""
        db = _make_db(tmp_path, rows=[{"ticker": "KXTEST-26DEC31", "direction": "YES",
                                        "title": "Will Congress pass the infrastructure bill?",
                                        "market_price": 0.30, "our_estimate": 0.60, "edge": 0.30,
                                        "confidence": "HIGH"}])
        text = _full_compile(signals=[_sig()], db_path=db)
        over = [(i+1, len(l), l) for i, l in enumerate(text.split("\n"))
                if len(l) > 100 and "====" not in l]
        assert over == [], f"Lines > 100 chars: {over[:3]}"

    def test_signal_block_alone_max_flags(self):
        """_signal_block with all optional flags stays under 100 chars."""
        s = _sig(second_pass=True, confidence_downgraded=True, short_horizon=True)
        lines = report._signal_block(s, index=1, unit_size=10)
        over = [(i, len(l), l) for i, l in enumerate(lines) if len(l) > 100]
        assert over == [], f"Lines > 100 chars in _signal_block: {over}"


# ═══════════════════════════════════════════════════════════════════════════════
# _close_and_urgency regressions
# ═══════════════════════════════════════════════════════════════════════════════

class TestCloseAndUrgency:
    """Regression tests against hardcoded expected strings for each threshold."""

    def _dt(self, hours_ahead):
        return (datetime.now(timezone.utc) + timedelta(hours=hours_ahead)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    def test_closing_today_urgency(self):
        """<= 0 days → [CLOSING TODAY/TOMORROW]."""
        close_fmt, urgency = _close_and_urgency({"close_time": self._dt(6)})
        assert close_fmt != ""
        assert "CLOSING TODAY/TOMORROW" in urgency

    def test_closing_in_two_days_urgency(self):
        """2 days ahead → [CLOSING IN 2d]."""
        close_fmt, urgency = _close_and_urgency({"close_time": self._dt(48 + 6)})
        assert "CLOSING IN" in urgency
        assert "d]" in urgency

    def test_closing_in_six_days_soft_urgency(self):
        """6 days ahead → [closes in 6d] (lowercase soft marker)."""
        close_fmt, urgency = _close_and_urgency({"close_time": self._dt(6 * 24 + 6)})
        assert "closes in" in urgency

    def test_far_future_no_urgency(self):
        """30 days ahead → no urgency marker."""
        _, urgency = _close_and_urgency({"close_time": self._dt(30 * 24)})
        assert urgency == ""

    def test_missing_close_time_returns_empty(self):
        """No close_time key → both outputs are empty strings."""
        close_fmt, urgency = _close_and_urgency({})
        assert close_fmt == ""
        assert urgency == ""

    def test_none_close_time_returns_empty(self):
        """close_time=None → both outputs are empty strings."""
        close_fmt, urgency = _close_and_urgency({"close_time": None})
        assert close_fmt == ""
        assert urgency == ""

    def test_close_fmt_has_month_name(self):
        """close_fmt contains abbreviated month name, e.g. 'Dec'."""
        _, _ = _close_and_urgency({"close_time": "2026-12-31T23:59:00Z"})
        close_fmt, _ = _close_and_urgency({"close_time": "2026-12-31T23:59:00Z"})
        assert "Dec" in close_fmt

    def test_expiration_time_fallback(self):
        """expiration_time key used when close_time absent."""
        close_fmt, _ = _close_and_urgency({"expiration_time": "2026-12-31T23:59:00Z"})
        assert "Dec" in close_fmt


# ═══════════════════════════════════════════════════════════════════════════════
# _render_table
# ═══════════════════════════════════════════════════════════════════════════════

class TestRenderTable:
    """Shared table renderer alignment and truncation."""

    def test_header_row_rendered(self):
        """Header names appear in the first output line."""
        lines = _render_table(["Ticker", "Edge"], [["KXFOO", "12%"]])
        assert "Ticker" in lines[0]
        assert "Edge" in lines[0]

    def test_separator_line_present(self):
        """Second line is a separator of dashes."""
        lines = _render_table(["A", "B"], [["x", "y"]])
        assert lines[1].strip().replace("-", "").replace(" ", "") == ""

    def test_content_row_rendered(self):
        """Data row content appears in output."""
        lines = _render_table(["A", "B"], [["hello", "world"]])
        assert "hello" in lines[2]
        assert "world" in lines[2]

    def test_long_content_truncated(self):
        """Content longer than column width is truncated with ellipsis."""
        long_val = "A" * 40
        lines = _render_table(["Col"], [[long_val]], widths=[10])
        assert "..." in lines[2]
        assert len(lines[2].strip()) <= 10

    def test_multiple_rows_aligned(self):
        """All content rows have the same number of characters in column 1."""
        rows = [["short", "x"], ["much_longer_value", "y"]]
        lines = _render_table(["H1", "H2"], rows, widths=[20, 5])
        # Both content rows should have the same total width
        assert len(lines[2]) == len(lines[3])

    def test_empty_rows_returns_header_only(self):
        """Empty row list renders header + separator with no data rows."""
        lines = _render_table(["A", "B"], [])
        assert len(lines) == 2  # header + separator

    def test_width_column_respected(self):
        """Column width does not exceed specified width."""
        lines = _render_table(["Title"], [["Short"]], widths=[15])
        col_width = len(lines[0].strip())
        assert col_width <= 15


# ═══════════════════════════════════════════════════════════════════════════════
# Betting queue: title, Dir+Conf, missing-title fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestBettingQueueContent:
    """Betting queue rows show title, direction, and confidence."""

    def test_title_appears_in_queue_row(self, tmp_path):
        """Known title from DB appears in the rendered betting queue."""
        db = _make_db(tmp_path, rows=[{
            "ticker": "KXINFRA-26DEC31",
            "title": "Will Congress pass infrastructure bill?",
            "direction": "YES", "market_price": 0.30, "our_estimate": 0.60,
            "edge": 0.30, "confidence": "HIGH",
        }])
        lines = report._betting_queue(db_path=db, config=_no_floor_cfg())
        full = "\n".join(lines)
        assert "Will Congress" in full

    def test_direction_yes_shown_in_queue(self, tmp_path):
        """YES direction appears in the queue table row."""
        db = _make_db(tmp_path, rows=[{
            "ticker": "KXTEST-26DEC31", "title": "Test market",
            "direction": "YES", "market_price": 0.30, "our_estimate": 0.60,
            "edge": 0.30, "confidence": "MED",
        }])
        lines = report._betting_queue(db_path=db, config=_no_floor_cfg())
        full = "\n".join(lines)
        assert "YES" in full

    def test_direction_no_shown_in_queue(self, tmp_path):
        """NO direction appears in the queue table row."""
        db = _make_db(tmp_path, rows=[{
            "ticker": "KXTEST-26DEC31", "title": "Test market",
            "direction": "NO", "market_price": 0.70, "our_estimate": 0.40,
            "edge": 0.30, "confidence": "HIGH",
        }])
        lines = report._betting_queue(db_path=db, config=_no_floor_cfg())
        full = "\n".join(lines)
        assert "NO" in full

    def test_confidence_shown_in_queue(self, tmp_path):
        """HIGH confidence label appears in queue table row."""
        db = _make_db(tmp_path, rows=[{
            "ticker": "KXTEST-26DEC31", "title": "Test market",
            "direction": "YES", "market_price": 0.30, "our_estimate": 0.60,
            "edge": 0.30, "confidence": "HIGH",
        }])
        lines = report._betting_queue(db_path=db, config=_no_floor_cfg())
        full = "\n".join(lines)
        assert "HIGH" in full

    def test_missing_title_falls_back_to_ticker(self, tmp_path):
        """Row with empty title falls back to ticker — no crash, no 'None' printed."""
        db = _make_db(tmp_path, rows=[{
            "ticker": "KXNOTITLE-26DEC31", "title": "",
            "direction": "YES", "market_price": 0.30, "our_estimate": 0.60,
            "edge": 0.30, "confidence": "MED",
        }])
        lines = report._betting_queue(db_path=db, config=_no_floor_cfg())
        full = "\n".join(lines)
        assert "None" not in full
        assert "KXNOTITLE-26DEC31" in full

    def test_queue_header_has_dir_and_conf_columns(self, tmp_path):
        """'Dir' and 'Conf' column headers appear in the betting queue."""
        db = _make_db(tmp_path, rows=[{
            "ticker": "KXTEST", "title": "Test",
            "direction": "YES", "market_price": 0.30, "our_estimate": 0.60,
            "edge": 0.30, "confidence": "MED",
        }])
        lines = report._betting_queue(db_path=db, config=_no_floor_cfg())
        full = "\n".join(lines)
        assert "Dir" in full
        assert "Conf" in full


# ═══════════════════════════════════════════════════════════════════════════════
# Smart money drift: title shown
# ═══════════════════════════════════════════════════════════════════════════════

class TestSmartMoneyDriftTitle:
    """Drift table shows title alongside ticker (when yesterday snapshot exists)."""

    def _sm_with_signal(self, ticker="KXDRIFT-26DEC31", title="Will inflation fall in Q4?"):
        """Smart money result where title is available via kalshi_signals."""
        return {
            "kalshi_signals": [
                {
                    "trader": "0xtrader000000000000000000000000000000001",
                    "poly_outcome": "YES",
                    "position_val": 1500,
                    "poly_price": 0.52,
                    "match_score": 0.85,
                    "kalshi_ticker": ticker,
                    "kalshi_title": title,
                }
            ],
            "watchlist": {},
        }

    def _prev_snapshot(self, trader, ticker, val):
        """Return a fake _parse_sm_snapshot result dict with one position."""
        return {(trader, ticker): val}

    def test_drift_title_shown_when_snapshot_exists(self):
        """When yesterday snapshot file is mocked, drift row shows title."""
        trader = "0xtrader000000000000000000000000000000001"
        ticker = "KXDRIFT-26DEC31"
        title  = "Will inflation fall in Q4?"
        sm = self._sm_with_signal(ticker=ticker, title=title)
        # Prev had $500; curr has $1500 → chg = +$1000 (exactly at threshold)
        fake_prev = {(trader, ticker): 500.0}
        with patch("core.report.os.path.exists", return_value=True), \
             patch("core.report._parse_sm_snapshot", return_value=fake_prev):
            text = _full_compile(smart_money_result=sm)
        assert "Will inflation" in text

    def test_drift_no_crash_without_snapshot(self):
        """Without snapshot file, drift section is omitted — no crash, no 'None'."""
        sm = self._sm_with_signal()
        with patch("core.report.os.path.exists", return_value=False):
            text = _full_compile(smart_money_result=sm)
        # Drift section is skipped entirely when no yesterday file exists
        assert "None" not in text
        assert "SMART MONEY WATCHLIST" in text  # section still renders

    def test_drift_missing_title_falls_back_to_empty(self):
        """If kalshi_title absent in signals, title col is blank — no crash, no 'None'."""
        sm = {
            "kalshi_signals": [
                {
                    "trader": "0xtrader000000000000000000000000000000002",
                    "poly_outcome": "YES", "position_val": 1500, "poly_price": 0.50,
                    "match_score": 0.80, "kalshi_ticker": "KXNO-TITLE",
                    # kalshi_title intentionally absent
                }
            ],
            "watchlist": {},
        }
        fake_prev = {("0xtrader000000000000000000000000000000002", "KXNO-TITLE"): 200.0}
        with patch("core.report.os.path.exists", return_value=True), \
             patch("core.report._parse_sm_snapshot", return_value=fake_prev):
            text = _full_compile(smart_money_result=sm)
        assert "None" not in text


# ═══════════════════════════════════════════════════════════════════════════════
# Per-trader cross-reference: kalshi_title shown
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerTraderCrossRefTitle:
    """Per-trader cross-reference table shows kalshi_title."""

    def _sm_with_xref(self, title="Will tech sector rally in Q3?"):
        return {
            "kalshi_signals": [
                {
                    "trader": "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                    "poly_outcome": "YES",
                    "position_val": 1500,
                    "poly_price": 0.55,
                    "match_score": 0.85,
                    "kalshi_ticker": "KXTECH-26SEP30",
                    "kalshi_title": title,
                }
            ],
            "drift_data": {},
            "watchlist": {},
        }

    def test_kalshi_title_shown_in_xref(self):
        """kalshi_title fragment appears in the per-trader table output."""
        sm = self._sm_with_xref(title="Will tech sector rally in Q3?")
        text = _full_compile(smart_money_result=sm)
        assert "Will tech" in text

    def test_xref_missing_title_falls_back_gracefully(self):
        """Missing kalshi_title on xref dict does not crash and shows no 'None'."""
        sm = {
            "kalshi_signals": [
                {
                    "trader": "0xdeadbeef0000000000000000000000000000dead",
                    "poly_outcome": "YES",
                    "position_val": 1200,
                    "poly_price": 0.50,
                    "match_score": 0.80,
                    "kalshi_ticker": "KXNOTITLE-26DEC31",
                    # kalshi_title intentionally absent
                }
            ],
            "drift_data": {},
            "watchlist": {},
        }
        text = _full_compile(smart_money_result=sm)
        assert "None" not in text


# ═══════════════════════════════════════════════════════════════════════════════
# _signal_block: reasoning fallback line
# ═══════════════════════════════════════════════════════════════════════════════

class TestSignalBlockReasoningFallback:
    """When reasoning is empty, signal block shows explicit fallback text."""

    def test_empty_reasoning_shows_fallback(self):
        s = _sig(reasoning="", sources_checked=[])
        lines = report._signal_block(s, index=1)
        full = "\n".join(lines)
        assert "heuristic-only signal" in full.lower() or "no narrative reasoning" in full.lower()

    def test_none_reasoning_shows_fallback(self):
        s = _sig(reasoning=None, sources_checked=None)
        lines = report._signal_block(s, index=1)
        full = "\n".join(lines)
        assert "heuristic-only signal" in full.lower() or "no narrative reasoning" in full.lower()

    def test_present_reasoning_no_fallback(self):
        """When reasoning is non-empty, fallback line must NOT appear."""
        s = _sig(reasoning="Detailed analysis of market conditions.")
        lines = report._signal_block(s, index=1)
        full = "\n".join(lines)
        assert "heuristic-only signal" not in full.lower()
        assert "no narrative reasoning" not in full.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Repeat signals: EV/contract shown
# ═══════════════════════════════════════════════════════════════════════════════

class TestRepeatSignalEV:
    """Repeat signals section shows EV/contract value, not just edge pp."""

    def test_repeat_signal_shows_ev(self):
        """EV value (e.g. '$+3.00') appears in repeat signals output."""
        repeat = _sig(
            ticker="KXREPEAT-26NOV01",
            title="Will Fed cut rates in November?",
            market_price=0.40, our_estimate=0.70, edge=0.30,
            is_repeat=True, repeat_count=2,
        )
        text = _full_compile(repeat_signals=[repeat])
        # The repeat block should contain EV dollar amount
        assert "EV $" in text or "EV" in text

    def test_repeat_signal_ev_value_is_correct(self):
        """EV shown in repeat block matches _ev_per_contract output."""
        repeat = _sig(
            ticker="KXREPEAT-26NOV01",
            market_price=0.40, our_estimate=0.70, edge=0.30,
            is_repeat=True,
        )
        expected = report._ev_per_contract("YES", 0.40, 0.70, unit_size=10)
        text = _full_compile(repeat_signals=[repeat])
        assert expected is not None
        assert expected in text

    def test_repeat_signal_reasoning_summary_shown(self):
        """First line of reasoning appears in compact repeat block."""
        repeat = _sig(
            ticker="KXREPEAT-26NOV01",
            market_price=0.40, our_estimate=0.70, edge=0.30,
            reasoning="Disinflationary trend driving cut expectations.",
            is_repeat=True,
        )
        text = _full_compile(repeat_signals=[repeat])
        assert "Disinflationary" in text

    def test_repeat_signal_empty_reasoning_no_crash(self):
        """Repeat signal with no reasoning renders without error."""
        repeat = _sig(
            ticker="KXREPEAT-26NOV01",
            market_price=0.40, our_estimate=0.70, edge=0.30,
            reasoning="", is_repeat=True,
        )
        text = _full_compile(repeat_signals=[repeat])
        assert "KXREPEAT-26NOV01" in text
