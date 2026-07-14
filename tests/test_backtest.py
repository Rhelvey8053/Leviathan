"""
tests/test_backtest.py — Tests for the backtest harness (Section 6 — backtest-harness).

Uses in-memory CSV data — no network, no real DB.
"""

import csv
import io
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from backtesting.harness import BacktestRunner


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _write_csv(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


SIGNAL_FIELDS = ["call_id", "ticker", "direction", "confidence", "flag_path",
                 "time_horizon", "edge", "result"]

RESOLUTION_FIELDS = ["ticker", "resolved_yes", "close_date"]

SAMPLE_SIGNALS = [
    {"call_id": "s1", "ticker": "KXFOO", "direction": "YES", "confidence": "HIGH",
     "flag_path": "EDGE", "time_horizon": "WEEKLY", "edge": "0.12", "result": ""},
    {"call_id": "s2", "ticker": "KXBAR", "direction": "NO",  "confidence": "MED",
     "flag_path": "DRIFT", "time_horizon": "INTRADAY", "edge": "0.08", "result": ""},
    {"call_id": "s3", "ticker": "KXBAZ", "direction": "YES", "confidence": "LOW",
     "flag_path": "EDGE", "time_horizon": "LONG", "edge": "0.05", "result": ""},
    {"call_id": "s4", "ticker": "KXQUX", "direction": "YES", "confidence": "HIGH",
     "flag_path": "HEURISTIC", "time_horizon": "WEEKLY", "edge": "0.15", "result": ""},
    # No resolution for s5 — should not appear in matched
    {"call_id": "s5", "ticker": "KXMISS", "direction": "YES", "confidence": "MED",
     "flag_path": "EDGE", "time_horizon": "WEEKLY", "edge": "0.09", "result": ""},
]

SAMPLE_RESOLUTIONS = [
    {"ticker": "KXFOO", "resolved_yes": "true",  "close_date": "2026-05-01"},  # s1 YES + res YES → HIT
    {"ticker": "KXBAR", "resolved_yes": "true",  "close_date": "2026-05-02"},  # s2 NO  + res YES → MISS
    {"ticker": "KXBAZ", "resolved_yes": "false", "close_date": "2026-05-03"},  # s3 YES + res NO  → MISS
    {"ticker": "KXQUX", "resolved_yes": "false", "close_date": "2026-05-04"},  # s4 YES + res NO  → MISS
]


@pytest.fixture()
def runner_with_data(tmp_path):
    sig_path = str(tmp_path / "signals.csv")
    res_path = str(tmp_path / "resolutions.csv")
    _write_csv(sig_path, SAMPLE_SIGNALS, SIGNAL_FIELDS)
    _write_csv(res_path, SAMPLE_RESOLUTIONS, RESOLUTION_FIELDS)
    r = BacktestRunner()
    r.load_signals(sig_path)
    r.load_resolutions(res_path)
    r.match_signals_to_resolutions()
    return r


# ── load_signals ─────────────────────────────────────────────────────────────

def test_load_signals_count(tmp_path):
    sig_path = str(tmp_path / "signals.csv")
    _write_csv(sig_path, SAMPLE_SIGNALS, SIGNAL_FIELDS)
    r = BacktestRunner()
    r.load_signals(sig_path)
    assert len(r.signals) == len(SAMPLE_SIGNALS)


# ── load_resolutions ──────────────────────────────────────────────────────────

def test_load_resolutions_count(tmp_path):
    res_path = str(tmp_path / "resolutions.csv")
    _write_csv(res_path, SAMPLE_RESOLUTIONS, RESOLUTION_FIELDS)
    r = BacktestRunner()
    r.load_resolutions(res_path)
    assert len(r.resolutions) == len(SAMPLE_RESOLUTIONS)


def test_load_resolutions_bool_conversion(tmp_path):
    res_path = str(tmp_path / "resolutions.csv")
    _write_csv(res_path, [{"ticker": "X", "resolved_yes": "true", "close_date": "2026-01-01"}],
               RESOLUTION_FIELDS)
    r = BacktestRunner()
    r.load_resolutions(res_path)
    assert r.resolutions[0]["resolved_yes"] is True


# ── match_signals_to_resolutions ─────────────────────────────────────────────

def test_match_count(runner_with_data):
    """5 signals, 4 resolutions — KXMISS has no resolution → 4 matched."""
    assert len(runner_with_data.matches) == 4


def test_hit_flag_yes_resolves_yes(runner_with_data):
    """KXFOO: direction=YES, resolved_yes=True → hit=True."""
    m = {r["ticker"]: r for r in runner_with_data.matches}
    assert m["KXFOO"]["hit"] is True


def test_hit_flag_no_resolves_yes(runner_with_data):
    """KXBAR: direction=NO, resolved_yes=True → hit=False."""
    m = {r["ticker"]: r for r in runner_with_data.matches}
    assert m["KXBAR"]["hit"] is False


# ── compute_stats ─────────────────────────────────────────────────────────────

def test_compute_stats_total(runner_with_data):
    stats = runner_with_data.compute_stats()
    assert stats["total"] == 5  # all signals loaded
    assert stats["matched"] == 4


def test_compute_stats_hit_rate(runner_with_data):
    """1 hit out of 4 matched → 0.25."""
    stats = runner_with_data.compute_stats()
    assert abs(stats["hit_rate"] - 0.25) < 0.001


def test_compute_stats_by_confidence(runner_with_data):
    stats = runner_with_data.compute_stats()
    bc = stats["by_confidence"]
    assert "HIGH" in bc
    assert "MED" in bc
    assert "LOW" in bc
    # s1 (HIGH, EDGE, WEEKLY, hit) + s4 (HIGH, HEURISTIC, WEEKLY, miss)
    assert bc["HIGH"]["n"] == 2
    assert bc["HIGH"]["hits"] == 1


def test_compute_stats_by_flag_path(runner_with_data):
    stats = runner_with_data.compute_stats()
    fp = stats["by_flag_path"]
    assert "EDGE" in fp
    # KXFOO (EDGE, hit) + KXBAZ (EDGE, miss) = 2 EDGE signals
    assert fp["EDGE"]["n"] == 2
    assert fp["EDGE"]["hits"] == 1


def test_compute_stats_by_horizon(runner_with_data):
    stats = runner_with_data.compute_stats()
    bh = stats["by_horizon"]
    assert "Weekly" in bh
    assert bh["Weekly"]["n"] >= 1


# ── report ────────────────────────────────────────────────────────────────────

def test_report_writes_file(runner_with_data, tmp_path):
    out = str(tmp_path / "report.txt")
    runner_with_data.report(out)
    assert os.path.exists(out)
    with open(out, encoding="utf-8") as f:
        content = f.read()
    assert "LEVIATHAN BACKTEST REPORT" in content
    assert "Hit rate:" in content


def test_report_contains_stats_sections(runner_with_data, tmp_path):
    out = str(tmp_path / "report.txt")
    runner_with_data.report(out)
    with open(out, encoding="utf-8") as f:
        content = f.read()
    assert "By Confidence:" in content
    assert "By Signal Path:" in content
    assert "By Horizon:" in content


# ── sample_resolutions.csv ────────────────────────────────────────────────────

def test_sample_resolutions_exists():
    """The bundled sample_resolutions.csv must be present in backtesting/."""
    assert (ROOT / "backtesting" / "sample_resolutions.csv").exists()


def test_sample_resolutions_loadable(tmp_path):
    """BacktestRunner can load sample_resolutions.csv without error."""
    r = BacktestRunner()
    r.load_resolutions(str(ROOT / "backtesting" / "sample_resolutions.csv"))
    assert len(r.resolutions) >= 5


# ── walk_forward ─────────────────────────────────────────────────────────────

WF_SIGNAL_FIELDS = ["call_id", "ticker", "direction", "confidence", "flag_path",
                    "time_horizon", "edge", "result"]
WF_RESOLUTION_FIELDS = ["ticker", "resolved_yes", "close_date"]

# 6 signals, chronologically ordered by close_date, alternating hit/miss:
# HIT, HIT, miss, HIT, miss, HIT
WF_SIGNALS = [
    {"call_id": f"w{i}", "ticker": f"KXW{i}", "direction": "YES", "confidence": "MED",
     "flag_path": "EDGE", "time_horizon": "WEEKLY", "edge": "0.10", "result": ""}
    for i in range(1, 7)
]
WF_RESOLUTIONS = [
    {"ticker": "KXW1", "resolved_yes": "true",  "close_date": "2026-01-01"},  # HIT
    {"ticker": "KXW2", "resolved_yes": "true",  "close_date": "2026-01-02"},  # HIT
    {"ticker": "KXW3", "resolved_yes": "false", "close_date": "2026-01-03"},  # miss
    {"ticker": "KXW4", "resolved_yes": "true",  "close_date": "2026-01-04"},  # HIT
    {"ticker": "KXW5", "resolved_yes": "false", "close_date": "2026-01-05"},  # miss
    {"ticker": "KXW6", "resolved_yes": "true",  "close_date": "2026-01-06"},  # HIT
]


@pytest.fixture()
def wf_runner(tmp_path):
    sig_path = str(tmp_path / "wf_signals.csv")
    res_path = str(tmp_path / "wf_resolutions.csv")
    _write_csv(sig_path, WF_SIGNALS, WF_SIGNAL_FIELDS)
    _write_csv(res_path, WF_RESOLUTIONS, WF_RESOLUTION_FIELDS)
    r = BacktestRunner()
    r.load_signals(sig_path)
    r.load_resolutions(res_path)
    r.match_signals_to_resolutions()
    return r


def test_match_includes_close_date(wf_runner):
    m = {r["ticker"]: r for r in wf_runner.matches}
    assert m["KXW1"]["close_date"] == "2026-01-01"


def test_walk_forward_insufficient_data_returns_empty(wf_runner):
    """min_train >= number of dateable matches → no folds possible."""
    folds = wf_runner.walk_forward(min_train=6)
    assert folds == []


def test_walk_forward_fold_count(wf_runner):
    """6 matches, min_train=3 → folds for signals at index 3,4,5 = 3 folds."""
    folds = wf_runner.walk_forward(min_train=3)
    assert len(folds) == 3


def test_walk_forward_chronological_order(wf_runner):
    folds = wf_runner.walk_forward(min_train=3)
    dates = [f["close_date"] for f in folds]
    assert dates == sorted(dates)


def test_walk_forward_expanding_train_grows(wf_runner):
    """Expanding window (window=None): each fold's train set grows by one."""
    folds = wf_runner.walk_forward(min_train=3)
    assert [f["train_n"] for f in folds] == [3, 4, 5]


def test_walk_forward_rolling_window_fixed_size(wf_runner):
    """Rolling window=2: train set size is capped at 2 regardless of position."""
    folds = wf_runner.walk_forward(min_train=3, window=2)
    assert all(f["train_n"] == 2 for f in folds)


def test_walk_forward_test_hit_matches_resolution(wf_runner):
    """Fold testing KXW4 (index 3, 0-based) should record test_hit=True."""
    folds = wf_runner.walk_forward(min_train=3)
    assert folds[0]["test_ticker"] == "KXW4"
    assert folds[0]["test_hit"] is True


def test_walk_forward_cumulative_oos_hit_rate(wf_runner):
    """Test points in order are KXW4(HIT), KXW5(miss), KXW6(HIT) → 2/3 cumulative."""
    folds = wf_runner.walk_forward(min_train=3)
    assert folds[-1]["cumulative_oos_n"] == 3
    assert folds[-1]["cumulative_oos_hits"] == 2
    assert abs(folds[-1]["cumulative_oos_hit_rate"] - (2 / 3)) < 0.001


def test_walk_forward_excludes_matches_without_close_date(tmp_path):
    sigs = WF_SIGNALS + [{"call_id": "w7", "ticker": "KXW7", "direction": "YES",
                          "confidence": "MED", "flag_path": "EDGE",
                          "time_horizon": "WEEKLY", "edge": "0.10", "result": ""}]
    res = WF_RESOLUTIONS + [{"ticker": "KXW7", "resolved_yes": "true", "close_date": ""}]
    sig_path = str(tmp_path / "sig.csv")
    res_path = str(tmp_path / "res.csv")
    _write_csv(sig_path, sigs, WF_SIGNAL_FIELDS)
    _write_csv(res_path, res, WF_RESOLUTION_FIELDS)
    r = BacktestRunner()
    r.load_signals(sig_path)
    r.load_resolutions(res_path)
    r.match_signals_to_resolutions()
    folds = r.walk_forward(min_train=3)
    tickers_tested = {f["test_ticker"] for f in folds}
    assert "KXW7" not in tickers_tested


# ── walk_forward_summary ──────────────────────────────────────────────────────

def test_walk_forward_summary_insufficient_data():
    r = BacktestRunner()
    summary = r.walk_forward_summary([])
    assert summary["verdict"] == "INSUFFICIENT_DATA"
    assert summary["oos_hit_rate"] is None


def test_walk_forward_summary_too_few_folds(wf_runner):
    """3 folds < 5 → can't draw a stability conclusion yet."""
    folds = wf_runner.walk_forward(min_train=3)
    summary = wf_runner.walk_forward_summary(folds)
    assert summary["verdict"] == "TOO_FEW_FOLDS_TO_JUDGE"
    assert summary["oos_n"] == 3


def test_walk_forward_summary_in_sample_rate(wf_runner):
    """In-sample rate over all 6 dateable matches: 4 hits / 6 = 0.667."""
    folds = wf_runner.walk_forward(min_train=3)
    summary = wf_runner.walk_forward_summary(folds)
    assert abs(summary["in_sample_hit_rate"] - (4 / 6)) < 0.001


def test_walk_forward_summary_stable_verdict():
    """Enough folds (>=5), OOS rate close to in-sample rate → STABLE."""
    r = BacktestRunner()
    # 7 hits / 8 = 0.875 in-sample; OOS 0.8 is within the +/-0.15 band.
    r.matches = [{"hit": i != 8, "close_date": f"2026-01-{i:02d}"} for i in range(1, 9)]
    folds = [
        {"cumulative_oos_n": 5, "cumulative_oos_hit_rate": 0.8},
    ]
    summary = r.walk_forward_summary(folds)
    assert summary["verdict"] == "STABLE"


def test_walk_forward_summary_degrades_verdict():
    r = BacktestRunner()
    r.matches = [{"hit": True, "close_date": f"2026-01-{i:02d}"} for i in range(1, 9)]
    folds = [
        {"cumulative_oos_n": 5, "cumulative_oos_hit_rate": 0.6},  # in-sample is 1.0 → delta -0.4
    ]
    summary = r.walk_forward_summary(folds)
    assert summary["verdict"] == "DEGRADES_OUT_OF_SAMPLE"


# ── report() with walk-forward section ────────────────────────────────────────

def test_report_walk_forward_section(wf_runner, tmp_path):
    out = str(tmp_path / "report.txt")
    wf_runner.report(out, walk_forward=True, min_train=3)
    with open(out, encoding="utf-8") as f:
        content = f.read()
    assert "WALK-FORWARD VALIDATION" in content
    assert "Verdict:" in content


def test_report_walk_forward_omitted_by_default(runner_with_data, tmp_path):
    out = str(tmp_path / "report.txt")
    runner_with_data.report(out)
    with open(out, encoding="utf-8") as f:
        content = f.read()
    assert "WALK-FORWARD VALIDATION" not in content
