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


# ─── _ece ───────────────────────────────────────────────────────────────────

def test_ece_perfect_calibration_is_zero():
    # predicted 0.0 matching actual 0.0, and predicted 1.0 matching actual
    # 1.0 -- the only base_rate values where a bin's empirical outcome
    # frequency can exactly equal the prediction with a small sample.
    rows = [_r(0.0, 0.0), _r(0.0, 0.0), _r(1.0, 1.0), _r(1.0, 1.0)]
    ece, bins = hb._ece(rows)
    assert ece == pytest.approx(0.0)
    assert len(bins) == 2


def test_ece_bins_across_labels_not_within_one():
    """
    The whole point of binning at the table level: two different labels
    with different flat rates landing in the same 10% band get pooled into
    one bin, not kept separate (a per-label version would be degenerate --
    every row in a single label shares one base_rate, so there's nothing to
    bin within it).
    """
    rows = [
        _r(0.20, 1.0, label="label_a"),
        _r(0.22, 0.0, label="label_b"),
    ]
    ece, bins = hb._ece(rows)
    assert len(bins) == 1
    assert bins[0]["n"] == 2
    assert bins[0]["avg_predicted"] == pytest.approx(0.21)
    assert bins[0]["actual_yes_rate"] == pytest.approx(0.5)


def test_ece_weights_bins_by_size():
    # 9 rows at 0.0 (all actual 0.0, gap=0) + 1 row at 1.0 predicting YES but
    # actual is NO (gap=1.0) -> ece = (9/10)*0 + (1/10)*1.0 = 0.1
    rows = [_r(0.0, 0.0) for _ in range(9)] + [_r(1.0, 0.0)]
    ece, bins = hb._ece(rows)
    assert ece == pytest.approx(0.1)


def test_ece_base_rate_of_exactly_one_lands_in_last_bin():
    """base_rate=1.0 must not overflow past the last bin (idx would be
    n_bins, one past the valid 0..n_bins-1 range, without the min() clamp)."""
    ece, bins = hb._ece([_r(1.0, 1.0)])
    assert len(bins) == 1
    assert bins[0]["lo"] == pytest.approx(0.9)
    assert bins[0]["hi"] == pytest.approx(1.0)


def test_ece_empty_input_does_not_crash():
    ece, bins = hb._ece([])
    assert ece is None
    assert bins == []


def test_summarize_includes_ece():
    results = [_r(0.0, 0.0), _r(1.0, 1.0)]
    summary = hb.summarize(results)
    assert summary["ece"] == pytest.approx(0.0)
    assert len(summary["ece_bins"]) == 2


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
