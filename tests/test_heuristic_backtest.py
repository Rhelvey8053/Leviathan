"""
tests/test_heuristic_backtest.py — Offline tests for
analysis/heuristic_backtest.py.

Uses a throwaway SQLite DB (same tmp_db pattern as test_calibration.py) with
its own settled_markets table (backtesting.settled_fetcher._init_table) --
never touches leviathan.db, no network calls, no Claude.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis import heuristic_backtest as hb
from backtesting.settled_fetcher import _init_table
from core import logger


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(logger, "DB_PATH", db_file)
    monkeypatch.setattr(hb, "DB_PATH", db_file)
    _init_table(db_file)
    return db_file


def _insert(db_path, ticker, title, result, category=""):
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO settled_markets (ticker, title, category, result) VALUES (?,?,?,?)",
            (ticker, title, category, result),
        )
        conn.commit()
    finally:
        conn.close()


# ─── load_settled_markets ──────────────────────────────────────────────────

def test_load_settled_markets_only_binary_results(tmp_db):
    _insert(tmp_db, "K1", "Will X win the election?", "YES")
    _insert(tmp_db, "K2", "Will Y happen?", "NO")
    _insert(tmp_db, "K3", "Voided market", "VOID")
    rows = hb.load_settled_markets(tmp_db)
    tickers = {r["ticker"] for r in rows}
    assert tickers == {"K1", "K2"}


def test_load_settled_markets_empty_table(tmp_db):
    assert hb.load_settled_markets(tmp_db) == []


# ─── run_study ──────────────────────────────────────────────────────────────

def test_run_study_pairs_prediction_with_actual():
    markets = [{"ticker": "K1", "title": "Will X win the election?", "category": "Politics", "result": "YES"}]
    results = hb.run_study(markets)
    assert results[0]["ticker"] == "K1"
    assert results[0]["actual"] == 1.0
    assert results[0]["base_rate"] == pytest.approx(0.52)
    assert results[0]["label"] == "election"


def test_run_study_no_match_gives_none_base_rate():
    markets = [{"ticker": "K1", "title": "Some totally unrelated market title", "category": "", "result": "NO"}]
    results = hb.run_study(markets)
    assert results[0]["base_rate"] is None
    assert results[0]["label"] is None
    assert results[0]["actual"] == 0.0


# ─── summarize ──────────────────────────────────────────────────────────────

def _r(base_rate, actual, label="election"):
    return {"ticker": "T", "category": "", "base_rate": base_rate, "label": label, "actual": actual}


def test_summarize_perfect_calibration_zero_brier():
    results = [_r(1.0, 1.0), _r(0.0, 0.0), _r(1.0, 1.0), _r(0.0, 0.0)]
    summary = hb.summarize(results)
    assert summary["brier"] == pytest.approx(0.0)
    assert summary["coverage"] == 1.0
    assert summary["directional_accuracy"] == pytest.approx(1.0)


def test_summarize_worst_case_calibration_high_brier():
    results = [_r(1.0, 0.0), _r(0.0, 1.0)]
    summary = hb.summarize(results)
    assert summary["brier"] == pytest.approx(1.0)
    assert summary["directional_accuracy"] == pytest.approx(0.0)


def test_summarize_excludes_unmatched_from_coverage_and_brier():
    results = [_r(0.6, 1.0), {"ticker": "T2", "category": "", "base_rate": None, "label": None, "actual": 0.0}]
    summary = hb.summarize(results)
    assert summary["total"] == 2
    assert summary["matched"] == 1
    assert summary["coverage"] == pytest.approx(0.5)
    # naive brier still uses the full population (naive_yes_rate = 0.5 here)
    assert summary["naive_brier"] == pytest.approx(0.25)


def test_summarize_exact_half_excluded_from_directional_accuracy():
    results = [_r(0.5, 1.0), _r(0.5, 0.0), _r(0.8, 1.0)]
    summary = hb.summarize(results)
    assert summary["n_decided"] == 1
    assert summary["directional_accuracy"] == pytest.approx(1.0)


def test_summarize_by_label_breakdown_grouped_correctly():
    results = [
        _r(0.52, 1.0, label="election"),
        _r(0.52, 0.0, label="election"),
        _r(0.20, 0.0, label="treaty withdrawal"),
    ]
    summary = hb.summarize(results)
    by_label = {r["label"]: r for r in summary["by_label"]}
    assert by_label["election"]["n"] == 2
    assert by_label["election"]["avg_predicted"] == pytest.approx(0.52)
    assert by_label["election"]["actual_yes_rate"] == pytest.approx(0.5)
    assert by_label["treaty withdrawal"]["n"] == 1


def test_summarize_empty_input_does_not_crash():
    summary = hb.summarize([])
    assert summary["total"] == 0
    assert summary["matched"] == 0
    assert summary["coverage"] == 0.0
    assert summary["brier"] is None
    assert summary["by_label"] == []


# ─── main() end-to-end ──────────────────────────────────────────────────────

def test_main_runs_end_to_end_and_writes_report(tmp_db, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(hb, "REPORT_PATH", str(tmp_path / "heuristic_backtest.md"))
    _insert(tmp_db, "K1", "Will X win the election?", "YES")
    _insert(tmp_db, "K2", "Will Y win the election?", "NO")
    _insert(tmp_db, "K3", "Unrelated market with no heuristic match", "NO")
    hb.main()
    out = capsys.readouterr().out
    assert "HEURISTIC BACKTEST" in out
    assert "Heuristic coverage" in out
    assert Path(hb.REPORT_PATH).exists()
    report = Path(hb.REPORT_PATH).read_text(encoding="utf-8")
    assert "# Heuristic Backtest" in report
    assert "election" in report


def test_main_handles_empty_corpus_gracefully(tmp_db, capsys):
    hb.main()
    out = capsys.readouterr().out
    assert "No settled markets" in out
