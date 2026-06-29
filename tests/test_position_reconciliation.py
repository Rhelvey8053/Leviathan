"""
tests/test_position_reconciliation.py - Offline tests for position reconciliation.

All DB interactions use tmp SQLite files; no Kalshi API calls are made.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts.position_reconciliation import (
    _open_paper_signals,
    _parse_positions,
    format_report,
    reconcile,
    reconcile_data,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_db(tmp_path, rows: list[dict]) -> Path:
    """Create a minimal signals table and insert test rows."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE signals (
            ticker TEXT, direction TEXT, result TEXT,
            source TEXT DEFAULT 'paper', timestamp TEXT DEFAULT '2026-01-01T00:00:00'
        )
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO signals (ticker, direction, result, source, timestamp) VALUES (?,?,?,?,?)",
            (r.get("ticker"), r.get("direction"), r.get("result", ""),
             r.get("source", "paper"), r.get("timestamp", "2026-01-01T00:00:00")),
        )
    conn.commit()
    conn.close()
    return db


# ── _open_paper_signals ───────────────────────────────────────────────────────

def test_open_paper_signals_basic(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "KXABC-26JUL01", "direction": "YES"},
    ])
    result = _open_paper_signals(db)
    assert result == {"KXABC-26JUL01": "YES"}


def test_open_paper_signals_excludes_resolved(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "KXABC-26JUL01", "direction": "YES", "result": "WIN"},
    ])
    assert _open_paper_signals(db) == {}


def test_open_paper_signals_excludes_pass(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "KXABC-26JUL01", "direction": "PASS"},
    ])
    assert _open_paper_signals(db) == {}


def test_open_paper_signals_dedupes_by_ticker_keeps_latest(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "KXABC-26JUL01", "direction": "YES", "timestamp": "2026-01-01T00:00:00"},
        {"ticker": "KXABC-26JUL01", "direction": "NO",  "timestamp": "2026-01-02T00:00:00"},
    ])
    result = _open_paper_signals(db)
    assert result == {"KXABC-26JUL01": "NO"}


def test_open_paper_signals_includes_null_source(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "KXABC-26JUL01", "direction": "YES", "source": None},
    ])
    assert "KXABC-26JUL01" in _open_paper_signals(db)


def test_open_paper_signals_excludes_real_fill(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "KXABC-26JUL01", "direction": "YES", "source": "real_fill"},
    ])
    assert _open_paper_signals(db) == {}


def test_open_paper_signals_empty_db(tmp_path):
    db = _make_db(tmp_path, [])
    assert _open_paper_signals(db) == {}


# ── _parse_positions ──────────────────────────────────────────────────────────

def test_parse_positions_positive_is_yes():
    result = _parse_positions([{"ticker": "KXABC", "position": 5}])
    assert result == {"KXABC": "YES"}


def test_parse_positions_negative_is_no():
    result = _parse_positions([{"ticker": "KXABC", "position": -3}])
    assert result == {"KXABC": "NO"}


def test_parse_positions_zero_is_skipped():
    result = _parse_positions([{"ticker": "KXABC", "position": 0}])
    assert result == {}


def test_parse_positions_empty_list():
    assert _parse_positions([]) == {}


def test_parse_positions_missing_ticker_skipped():
    result = _parse_positions([{"position": 5}])
    assert result == {}


def test_parse_positions_multiple_markets():
    raw = [
        {"ticker": "KXYES", "position": 10},
        {"ticker": "KXNO",  "position": -2},
        {"ticker": "KXFLAT", "position": 0},
    ]
    result = _parse_positions(raw)
    assert result == {"KXYES": "YES", "KXNO": "NO"}


def test_parse_positions_fallback_yes_no_counts():
    raw = [{"ticker": "KXABC", "yes_position": 4, "no_position": 0}]
    result = _parse_positions(raw)
    assert result == {"KXABC": "YES"}


def test_parse_positions_fallback_net_no():
    raw = [{"ticker": "KXABC", "yes_position": 0, "no_position": 3}]
    result = _parse_positions(raw)
    assert result == {"KXABC": "NO"}


def test_parse_positions_market_ticker_field():
    result = _parse_positions([{"market_ticker": "KXABC", "position": 1}])
    assert result == {"KXABC": "YES"}


# ── reconcile_data ────────────────────────────────────────────────────────────

def test_reconcile_data_aligned():
    paper     = {"KXABC": "YES"}
    positions = {"KXABC": "YES"}
    r = reconcile_data(paper, positions)
    assert len(r["aligned"]) == 1
    assert r["aligned"][0] == {"ticker": "KXABC", "direction": "YES"}
    assert r["misaligned"] == []
    assert r["unplaced"]   == []
    assert r["unexpected"] == []


def test_reconcile_data_misaligned():
    r = reconcile_data({"KXABC": "YES"}, {"KXABC": "NO"})
    assert len(r["misaligned"]) == 1
    assert r["misaligned"][0]["signal"]   == "YES"
    assert r["misaligned"][0]["position"] == "NO"
    assert r["aligned"] == []


def test_reconcile_data_unplaced():
    r = reconcile_data({"KXABC": "YES"}, {})
    assert r["unplaced"] == [{"ticker": "KXABC", "direction": "YES"}]
    assert r["aligned"] == r["misaligned"] == r["unexpected"] == []


def test_reconcile_data_unexpected():
    r = reconcile_data({}, {"KXABC": "NO"})
    assert r["unexpected"] == [{"ticker": "KXABC", "direction": "NO"}]
    assert r["aligned"] == r["misaligned"] == r["unplaced"] == []


def test_reconcile_data_empty_both():
    r = reconcile_data({}, {})
    assert all(v == [] for v in r.values())


def test_reconcile_data_mixed():
    paper = {
        "KXALIGNED": "YES",
        "KXMIS":     "YES",
        "KXUNPLACED": "NO",
    }
    positions = {
        "KXALIGNED":   "YES",
        "KXMIS":       "NO",
        "KXUNEXPECTED": "YES",
    }
    r = reconcile_data(paper, positions)
    assert [x["ticker"] for x in r["aligned"]]    == ["KXALIGNED"]
    assert [x["ticker"] for x in r["misaligned"]] == ["KXMIS"]
    assert [x["ticker"] for x in r["unplaced"]]   == ["KXUNPLACED"]
    assert [x["ticker"] for x in r["unexpected"]] == ["KXUNEXPECTED"]


def test_reconcile_data_tickers_sorted():
    paper = {"KXZZZ": "YES", "KXAAA": "YES"}
    positions = {}
    r = reconcile_data(paper, positions)
    tickers = [x["ticker"] for x in r["unplaced"]]
    assert tickers == sorted(tickers)


# ── reconcile (with mocked fetch) ────────────────────────────────────────────

def test_reconcile_with_mock_fetch(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "KXABC-26JUL01", "direction": "YES"},
    ])
    config = {}

    def mock_fetch(cfg):
        return [{"ticker": "KXABC-26JUL01", "position": 3}]

    result = reconcile(config, db, _fetch_fn=mock_fetch)
    assert result["paper_open"]        == 1
    assert result["positions_fetched"] == 1
    assert len(result["aligned"])      == 1
    assert "error" not in result


def test_reconcile_api_error_returns_error_key(tmp_path):
    db = _make_db(tmp_path, [])

    def bad_fetch(cfg):
        raise RuntimeError("API unreachable")

    result = reconcile({}, db, _fetch_fn=bad_fetch)
    assert "error" in result
    assert result["positions_fetched"] == 0
    assert result["aligned"] == []


# ── format_report ─────────────────────────────────────────────────────────────

def _make_result(**overrides) -> dict:
    base = {
        "run_at": "2026-06-28T12:00:00Z",
        "paper_open": 5,
        "positions_fetched": 3,
        "aligned":    [],
        "misaligned": [],
        "unplaced":   [],
        "unexpected": [],
    }
    base.update(overrides)
    return base


def test_format_report_contains_header():
    out = format_report(_make_result())
    assert "POSITION RECONCILIATION" in out


def test_format_report_shows_aligned():
    result = _make_result(aligned=[{"ticker": "KXABC", "direction": "YES"}])
    assert "OK" in format_report(result)
    assert "KXABC" in format_report(result)


def test_format_report_shows_misaligned_flag():
    result = _make_result(misaligned=[{"ticker": "KXABC", "signal": "YES", "position": "NO"}])
    out = format_report(result)
    assert "MISALIGNED" in out
    assert "!!" in out


def test_format_report_shows_unplaced():
    result = _make_result(unplaced=[{"ticker": "KXABC", "direction": "NO"}])
    out = format_report(result)
    assert "Unplaced" in out
    assert "--" in out


def test_format_report_shows_unexpected():
    result = _make_result(unexpected=[{"ticker": "KXABC", "direction": "YES"}])
    out = format_report(result)
    assert "Unexpected" in out
    assert "??" in out


def test_format_report_error_case():
    result = _make_result(error="connection refused")
    out = format_report(result)
    assert "ERROR" in out
    assert "connection refused" in out


def test_format_report_empty_shows_no_activity():
    out = format_report(_make_result())
    assert "No open signals" in out
