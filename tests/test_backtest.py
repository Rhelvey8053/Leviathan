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

from backtest import BacktestRunner


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
    """The bundled sample_resolutions.csv must be present in repo root."""
    assert (ROOT / "sample_resolutions.csv").exists()


def test_sample_resolutions_loadable(tmp_path):
    """BacktestRunner can load sample_resolutions.csv without error."""
    r = BacktestRunner()
    r.load_resolutions(str(ROOT / "sample_resolutions.csv"))
    assert len(r.resolutions) >= 5
