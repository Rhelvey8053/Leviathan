"""
tests/test_settled_fetcher.py — Offline tests for backtesting/settled_fetcher.py
(replay-settled-fetcher).

All tests use a throwaway SQLite file — never touches leviathan.db.
Kalshi HTTP calls are mocked — no network calls.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from backtesting import settled_fetcher


@pytest.fixture()
def tmp_db(tmp_path):
    return str(tmp_path / "test_settled.db")


def _event(event_ticker, series_ticker="KXSERIES", category="Politics"):
    return {
        "event_ticker": event_ticker,
        "series_ticker": series_ticker,
        "category": category,
        "title": f"Event {event_ticker}",
    }


def _market(ticker, event_ticker, result="yes", **overrides):
    m = {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "title": f"Market {ticker}",
        "result": result,
        "close_time": "2026-06-01T00:00:00Z",
        "settlement_ts": "2026-06-01T00:05:00Z",
        "volume_fp": "100.0",
        "open_interest_fp": "50.0",
        "last_price_dollars": "0.9500",
    }
    m.update(overrides)
    return m


# ─── table creation / isolation from signals/runs ────────────────────────────

def test_init_table_creates_settled_markets(tmp_db):
    settled_fetcher._init_table(tmp_db)
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "settled_markets" in tables


def test_does_not_create_signals_or_runs_tables(tmp_db):
    """Read-only against existing tables -- this module must never create/touch signals or runs."""
    settled_fetcher._init_table(tmp_db)
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "signals" not in tables
    assert "runs" not in tables


# ─── fetch_and_store_settled_markets ──────────────────────────────────────────

def test_fetches_and_persists_settled_markets(tmp_db, monkeypatch):
    events = [_event("KXFOO-26JUN01")]
    markets = [_market("KXFOO-26JUN01-YES", "KXFOO-26JUN01", result="yes")]

    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_events", lambda config, max_fetch: events)
    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_event_markets", lambda config, et: markets)
    monkeypatch.setattr(settled_fetcher.time, "sleep", lambda s: None)

    result = settled_fetcher.fetch_and_store_settled_markets({}, db_path=tmp_db, max_events=10)

    assert result["events_scanned"] == 1
    assert result["markets_fetched"] == 1
    assert result["inserted"] == 1
    assert settled_fetcher.get_settled_market_count(tmp_db) == 1


def test_persisted_row_carries_series_ticker_and_category_from_event(tmp_db, monkeypatch):
    """series_ticker/category live on the event, not the raw market -- must be carried through."""
    events = [_event("KXFOO-26JUN01", series_ticker="KXFOO", category="Politics")]
    markets = [_market("KXFOO-26JUN01-YES", "KXFOO-26JUN01", result="yes")]

    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_events", lambda config, max_fetch: events)
    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_event_markets", lambda config, et: markets)
    monkeypatch.setattr(settled_fetcher.time, "sleep", lambda s: None)

    settled_fetcher.fetch_and_store_settled_markets({}, db_path=tmp_db, max_events=10)

    import sqlite3
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM settled_markets WHERE ticker='KXFOO-26JUN01-YES'").fetchone()
    assert row["series_ticker"] == "KXFOO"
    assert row["category"] == "Politics"
    assert row["result"] == "YES"


def test_excludes_kxmve_parlay_events(tmp_db, monkeypatch):
    """KXMVE parlay events are filtered out before fetching their markets at all."""
    events = [_event("KXMVESPORTSMULTIGAMEEXTENDED-S1"), _event("KXFOO-26JUN01")]
    call_log = []

    def fake_fetch_markets(config, event_ticker):
        call_log.append(event_ticker)
        return [_market(f"{event_ticker}-YES", event_ticker, result="yes")]

    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_events", lambda config, max_fetch: events)
    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_event_markets", fake_fetch_markets)
    monkeypatch.setattr(settled_fetcher.time, "sleep", lambda s: None)

    result = settled_fetcher.fetch_and_store_settled_markets({}, db_path=tmp_db, max_events=10)

    assert "KXMVESPORTSMULTIGAMEEXTENDED-S1" not in call_log
    assert "KXFOO-26JUN01" in call_log
    assert result["events_scanned"] == 1


def test_skips_unresolved_markets_within_a_settled_event(tmp_db, monkeypatch):
    """A market with no clean YES/NO result is not persisted, even within a settled event."""
    events = [_event("KXFOO-26JUN01")]
    markets = [_market("KXFOO-26JUN01-VOID", "KXFOO-26JUN01", result="")]

    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_events", lambda config, max_fetch: events)
    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_event_markets", lambda config, et: markets)
    monkeypatch.setattr(settled_fetcher.time, "sleep", lambda s: None)

    result = settled_fetcher.fetch_and_store_settled_markets({}, db_path=tmp_db, max_events=10)

    assert result["skipped_unresolved"] == 1
    assert result["inserted"] == 0
    assert settled_fetcher.get_settled_market_count(tmp_db) == 0


def test_idempotent_rerun_does_not_duplicate(tmp_db, monkeypatch):
    """Running the fetch twice against the same market must not duplicate or error."""
    events = [_event("KXFOO-26JUN01")]
    markets = [_market("KXFOO-26JUN01-YES", "KXFOO-26JUN01", result="yes")]

    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_events", lambda config, max_fetch: events)
    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_event_markets", lambda config, et: markets)
    monkeypatch.setattr(settled_fetcher.time, "sleep", lambda s: None)

    first  = settled_fetcher.fetch_and_store_settled_markets({}, db_path=tmp_db, max_events=10)
    second = settled_fetcher.fetch_and_store_settled_markets({}, db_path=tmp_db, max_events=10)

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["already_present"] == 1
    assert settled_fetcher.get_settled_market_count(tmp_db) == 1


def test_missing_event_ticker_is_skipped_gracefully(tmp_db, monkeypatch):
    """An event with no event_ticker/ticker at all is skipped, not a crash."""
    events = [{"series_ticker": "X", "category": "Y"}]  # no event_ticker or ticker key

    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_events", lambda config, max_fetch: events)
    monkeypatch.setattr(settled_fetcher.kalshi, "fetch_settled_event_markets",
                         lambda config, et: pytest.fail("should not be called"))
    monkeypatch.setattr(settled_fetcher.time, "sleep", lambda s: None)

    result = settled_fetcher.fetch_and_store_settled_markets({}, db_path=tmp_db, max_events=10)
    assert result["events_scanned"] == 0


def test_get_settled_market_count_zero_when_table_absent(tmp_db):
    """A fresh DB with no settled_markets table yet returns 0, not an error."""
    assert settled_fetcher.get_settled_market_count(tmp_db) == 0
