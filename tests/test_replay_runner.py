"""
tests/test_replay_runner.py — Offline tests for backtesting/replay_runner.py
(replay-runner).

All tests use a throwaway SQLite file — never touches leviathan.db.
core.scorer.score_markets is mocked throughout — no real (billed) API calls.
"""

import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from backtesting import replay_runner as rr
from backtesting.settled_fetcher import _init_table as _init_settled_table
from core.llm import LLMCostCeilingExceeded

CONFIG = {"markets": {}, "betting": {"unit_size": 10}, "llm": {"backend": "cli"}}


@pytest.fixture()
def tmp_db(tmp_path):
    return str(tmp_path / "test_replay.db")


def _insert_settled(db_path, ticker, close_time="2026-06-01T00:00:00Z", result="YES", series_ticker="SER"):
    _init_settled_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO settled_markets "
        "(ticker, event_ticker, series_ticker, category, title, result, close_time, "
        " settlement_ts, volume, open_interest, last_price, fetched_at) "
        "VALUES (?, 'EVT-1', ?, 'Politics', ?, ?, ?, ?, 100.0, 50.0, 0.9, '2026-07-25T00:00:00Z')",
        (ticker, series_ticker, f"Market {ticker}", result, close_time, close_time),
    )
    conn.commit()
    conn.close()


def _enriched(ticker, mid_price=0.4, close_time="2026-06-01T00:00:00Z", tier="approximate", as_of="2026-05-01T00:00:00+00:00"):
    return {
        "ticker": ticker, "title": f"Market {ticker}", "close_time": close_time,
        "mid_price": mid_price, "flag_path": "DRIFT", "time_horizon": "MONTHLY",
        "reconstruction_tier": tier, "reconstruction_source": "kalshi_candlestick",
        "reconstruction_as_of": as_of,
    }


# ─── table init / candidate selection ─────────────────────────────────────

def test_init_table_creates_replay_signals(tmp_db):
    rr._init_table(tmp_db)
    conn = sqlite3.connect(tmp_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "replay_signals" in tables


def test_candidate_tickers_excludes_already_replayed(tmp_db):
    _insert_settled(tmp_db, "T1")
    _insert_settled(tmp_db, "T2")
    rr._init_table(tmp_db)
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO replay_signals (ticker, direction) VALUES ('T1', 'YES')"
    )
    conn.commit()
    conn.close()
    candidates = rr._candidate_tickers(tmp_db, 10)
    tickers = {c["ticker"] for c in candidates}
    assert tickers == {"T2"}


def test_candidate_tickers_requires_series_ticker(tmp_db):
    _insert_settled(tmp_db, "T1", series_ticker="")
    _insert_settled(tmp_db, "T2", series_ticker="SER")
    rr._init_table(tmp_db)
    candidates = rr._candidate_tickers(tmp_db, 10)
    tickers = {c["ticker"] for c in candidates}
    assert tickers == {"T2"}


# ─── price-band guard ──────────────────────────────────────────────────────

def test_price_in_band_default_bounds():
    assert rr._price_in_band(0.5, CONFIG) is True
    assert rr._price_in_band(0.02, CONFIG) is False
    assert rr._price_in_band(0.98, CONFIG) is False
    assert rr._price_in_band(None, CONFIG) is False


def test_price_in_band_respects_config_override():
    cfg = {"markets": {"min_market_price": 0.20, "max_market_price": 0.80}}
    assert rr._price_in_band(0.15, cfg) is False
    assert rr._price_in_band(0.50, cfg) is True


# ─── as-of lookback selection ──────────────────────────────────────────────

def test_find_reconstructable_as_of_prefers_furthest_lookback_within_band(monkeypatch):
    calls = []

    def _fake_reconstruct(config, ticker, as_of_dt, db_path):
        calls.append(as_of_dt)
        # Only the 14-day-out lookback lands in-band; nearer ones are already certain.
        days_before_close = (datetime(2026, 6, 1, tzinfo=timezone.utc) - as_of_dt).days
        if days_before_close == 14:
            return _enriched("T1", mid_price=0.4)
        return _enriched("T1", mid_price=0.99)

    monkeypatch.setattr(rr.asof, "reconstruct_market_state", _fake_reconstruct)
    result = rr._find_reconstructable_as_of(CONFIG, "T1", "2026-06-01T00:00:00Z", "unused.db")
    assert result is not None
    assert result["mid_price"] == 0.4
    # Confirms furthest-from-close (30d) was tried before 14d succeeded.
    assert len(calls) == 2


def test_find_reconstructable_as_of_none_when_all_out_of_band(monkeypatch):
    monkeypatch.setattr(rr.asof, "reconstruct_market_state",
                        lambda *a, **k: _enriched("T1", mid_price=0.99))
    result = rr._find_reconstructable_as_of(CONFIG, "T1", "2026-06-01T00:00:00Z", "unused.db")
    assert result is None


def test_find_reconstructable_as_of_none_when_no_data(monkeypatch):
    monkeypatch.setattr(rr.asof, "reconstruct_market_state", lambda *a, **k: None)
    result = rr._find_reconstructable_as_of(CONFIG, "T1", "2026-06-01T00:00:00Z", "unused.db")
    assert result is None


def test_find_reconstructable_as_of_none_on_bad_close_time():
    result = rr._find_reconstructable_as_of(CONFIG, "T1", "not-a-date", "unused.db")
    assert result is None


# ─── row construction / hit grading ────────────────────────────────────────

def test_row_from_scored_hit_on_correct_yes_call():
    enriched = _enriched("T1")
    cs = {"direction": "yes", "confidence": "high", "edge": 0.2, "reasoning": "r"}
    row = rr._row_from_scored(enriched, cs, "YES", datetime(2026, 5, 1, tzinfo=timezone.utc))
    assert row["direction"] == "YES"
    assert row["resolved_yes"] == 1
    assert row["hit"] == 1


def test_row_from_scored_miss_on_wrong_no_call():
    enriched = _enriched("T1")
    cs = {"direction": "no", "confidence": "med", "edge": 0.1, "reasoning": "r"}
    row = rr._row_from_scored(enriched, cs, "YES", datetime(2026, 5, 1, tzinfo=timezone.utc))
    assert row["direction"] == "NO"
    assert row["resolved_yes"] == 1
    assert row["hit"] == 0


def test_row_from_scored_pass_direction_has_no_hit():
    enriched = _enriched("T1")
    cs = {"direction": "pass", "confidence": "low", "edge": 0.01, "reasoning": "r"}
    row = rr._row_from_scored(enriched, cs, "NO", datetime(2026, 5, 1, tzinfo=timezone.utc))
    assert row["direction"] == "PASS"
    assert row["hit"] is None


# ─── run_replay integration ─────────────────────────────────────────────────

def test_run_replay_forces_cli_backend(tmp_db, monkeypatch):
    """
    2026-08-26: was test_run_replay_forces_api_backend, asserting "api".
    Switched to "cli" (Claude Pro subscription, no metered spend) at the
    user's explicit request -- the API requirement was only ever a side
    effect of core.llm's $-based cost ceiling, which the validation task
    itself never needed; see replay_runner.py's module docstring.
    """
    _insert_settled(tmp_db, "T1")
    monkeypatch.setattr(rr, "_find_reconstructable_as_of",
                        lambda *a, **k: _enriched("T1"))

    seen_config = {}

    def _fake_score_markets(markets, config, now=None):
        seen_config["backend"] = config.get("llm", {}).get("backend")
        return [{"ticker": "T1", "direction": "YES", "confidence": "HIGH", "edge": 0.2, "reasoning": "r"}], {}

    monkeypatch.setattr(rr.scorer, "score_markets", _fake_score_markets)
    rr.run_replay(CONFIG, max_markets=5, db_path=tmp_db)
    assert seen_config["backend"] == "cli"


def test_run_replay_persists_scored_rows(tmp_db, monkeypatch):
    _insert_settled(tmp_db, "T1", result="YES")
    monkeypatch.setattr(rr, "_find_reconstructable_as_of",
                        lambda *a, **k: _enriched("T1"))
    monkeypatch.setattr(rr.scorer, "score_markets",
                        lambda markets, config, now=None: (
                            [{"ticker": "T1", "direction": "YES", "confidence": "HIGH", "edge": 0.2, "reasoning": "r"}], {}
                        ))
    summary = rr.run_replay(CONFIG, max_markets=5, db_path=tmp_db)
    assert summary["scored"] == 1
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM replay_signals WHERE ticker='T1'").fetchone()
    assert row is not None
    assert row["direction"] == "YES"
    assert row["hit"] == 1


def test_run_replay_stops_on_cost_ceiling(tmp_db, monkeypatch):
    _insert_settled(tmp_db, "T1")
    _insert_settled(tmp_db, "T2")
    monkeypatch.setattr(rr, "_find_reconstructable_as_of",
                        lambda config, ticker, close_time, db_path: _enriched(ticker))

    def _raise_ceiling(markets, config, now=None):
        raise LLMCostCeilingExceeded("ceiling hit")

    monkeypatch.setattr(rr.scorer, "score_markets", _raise_ceiling)
    summary = rr.run_replay(CONFIG, max_markets=5, db_path=tmp_db)
    assert summary["ceiling_stopped"] is True
    assert summary["scored"] == 0


def test_run_replay_preserves_already_scored_rows_when_ceiling_hits_later(tmp_db, monkeypatch):
    """
    The module docstring promises a ceiling-triggered stop is a pause, not
    lost work: rows successfully scored before the ceiling hit must remain
    durably persisted even though the run as a whole stops early.
    """
    _insert_settled(tmp_db, "T1")
    _insert_settled(tmp_db, "T2")
    # _candidate_tickers orders by RANDOM() -- pin a deterministic order so
    # this test isn't flaky about which ticker gets scored before the
    # ceiling hits.
    monkeypatch.setattr(rr, "_candidate_tickers",
                        lambda db_path, limit: [
                            {"ticker": "T1", "series_ticker": "SER", "close_time": "2026-06-01T00:00:00Z", "result": "YES"},
                            {"ticker": "T2", "series_ticker": "SER", "close_time": "2026-06-01T00:00:00Z", "result": "YES"},
                        ])
    monkeypatch.setattr(rr, "_find_reconstructable_as_of",
                        lambda config, ticker, close_time, db_path: _enriched(ticker))

    def _score_then_raise(markets, config, now=None):
        ticker = markets[0]["ticker"]
        if ticker == "T1":
            return [{"ticker": "T1", "direction": "YES", "confidence": "HIGH", "edge": 0.2, "reasoning": "r"}], {}
        raise LLMCostCeilingExceeded("ceiling hit")

    monkeypatch.setattr(rr.scorer, "score_markets", _score_then_raise)
    summary = rr.run_replay(CONFIG, max_markets=5, db_path=tmp_db)
    assert summary["ceiling_stopped"] is True
    assert summary["scored"] == 1

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM replay_signals WHERE ticker='T1'").fetchone()
    assert row is not None
    assert row["direction"] == "YES"
    t2_row = conn.execute("SELECT * FROM replay_signals WHERE ticker='T2'").fetchone()
    assert t2_row is None


def test_run_replay_skips_when_no_reconstructable_state(tmp_db, monkeypatch):
    _insert_settled(tmp_db, "T1")
    monkeypatch.setattr(rr, "_find_reconstructable_as_of", lambda *a, **k: None)
    scorer_called = []
    monkeypatch.setattr(rr.scorer, "score_markets",
                        lambda *a, **k: scorer_called.append(1) or ([], {}))
    summary = rr.run_replay(CONFIG, max_markets=5, db_path=tmp_db)
    assert summary["skipped_no_data"] == 1
    assert summary["scored"] == 0
    assert scorer_called == []  # never reached the (billed) scoring call


def test_run_replay_is_idempotent_across_calls(tmp_db, monkeypatch):
    _insert_settled(tmp_db, "T1")
    monkeypatch.setattr(rr, "_find_reconstructable_as_of",
                        lambda *a, **k: _enriched("T1"))
    call_count = []

    def _fake_score(markets, config, now=None):
        call_count.append(1)
        return [{"ticker": "T1", "direction": "YES", "confidence": "HIGH", "edge": 0.2, "reasoning": "r"}], {}

    monkeypatch.setattr(rr.scorer, "score_markets", _fake_score)
    rr.run_replay(CONFIG, max_markets=5, db_path=tmp_db)
    rr.run_replay(CONFIG, max_markets=5, db_path=tmp_db)  # second call: T1 already replayed
    assert len(call_count) == 1


def test_run_replay_respects_max_markets(tmp_db, monkeypatch):
    for i in range(5):
        _insert_settled(tmp_db, f"T{i}")
    monkeypatch.setattr(rr, "_find_reconstructable_as_of",
                        lambda config, ticker, close_time, db_path: _enriched(ticker))
    monkeypatch.setattr(rr.scorer, "score_markets",
                        lambda markets, config, now=None: (
                            [{"ticker": markets[0]["ticker"], "direction": "YES",
                              "confidence": "HIGH", "edge": 0.2, "reasoning": "r"}], {}
                        ))
    summary = rr.run_replay(CONFIG, max_markets=2, db_path=tmp_db)
    assert summary["scored"] == 2


# ─── export_and_report ──────────────────────────────────────────────────────

def test_export_and_report_writes_harness_compatible_csvs(tmp_db, tmp_path, monkeypatch):
    _insert_settled(tmp_db, "T1", close_time="2026-06-01T00:00:00Z", result="YES")
    rr._init_table(tmp_db)
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        "INSERT INTO replay_signals "
        "(ticker, as_of_date, close_time, reconstruction_tier, direction, confidence, "
        " edge, reasoning, flag_path, time_horizon, resolved_yes, hit, scored_at) "
        "VALUES ('T1', '2026-05-01', '2026-06-01T00:00:00Z', 'approximate', 'YES', 'HIGH', "
        " 0.2, 'r', 'DRIFT', 'MONTHLY', 1, 1, '2026-07-25T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(rr, "_ROOT", str(tmp_path))
    report_path = rr.export_and_report(db_path=tmp_db)

    signals_csv = tmp_path / "data" / "replay_export" / "replay_signals.csv"
    resolutions_csv = tmp_path / "data" / "replay_export" / "replay_resolutions.csv"
    assert signals_csv.exists()
    assert resolutions_csv.exists()

    with open(signals_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["ticker"] == "T1"
    assert rows[0]["direction"] == "YES"

    with open(resolutions_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["resolved_yes"] == "true"
    assert rows[0]["close_date"] == "2026-06-01"

    assert Path(report_path).exists()
