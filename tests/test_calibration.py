"""
tests/test_calibration.py — Offline tests for analysis/calibration.py's
market-price baseline Brier reporting (market-baseline-brier).

Uses a throwaway SQLite DB via the same tmp_db pattern as test_logger.py —
never touches leviathan.db, no network calls.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis import calibration
from core import logger


@pytest.fixture(autouse=False)
def tmp_db(tmp_path, monkeypatch):
    """Fresh throwaway DB for each test — never touches leviathan.db."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(logger, "DB_PATH", db_file)
    logger._init_db()
    return db_file


def _insert(call_id, ticker, direction, market_price, our_estimate, result, outcome):
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, title, market_price, our_estimate,
             edge, direction, confidence, whale_detected, whale_direction,
             outcome, result, pnl_if_traded, run_id, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id, datetime.now(timezone.utc).isoformat(), ticker, "Test market",
            market_price, our_estimate, 0.10, direction, "MED", 0, "",
            outcome, result, None, "run-test", "paper",
        ))


def test_market_baseline_brier_line_present(tmp_db, capsys):
    """main() prints a Market Baseline Brier line when resolved data exists."""
    _insert("c1", "KX1", "YES", 0.40, 0.90, "WIN", "YES")
    calibration.main()
    out = capsys.readouterr().out
    assert "Market Baseline Brier:" in out


def test_market_baseline_brier_pending_when_no_resolved(tmp_db, capsys):
    """PENDING label shown when there are no resolved signals at all."""
    calibration.main()
    out = capsys.readouterr().out
    assert "Market Baseline Brier: PENDING" in out


def test_market_baseline_brier_pending_when_price_missing(tmp_db, capsys):
    """PENDING when the only resolved row has no market_price (never coerced to 0.5)."""
    _insert("c1", "KX1", "YES", None, 0.90, "WIN", "YES")
    calibration.main()
    out = capsys.readouterr().out
    assert "Market Baseline Brier: PENDING" in out


def test_scorer_worse_than_baseline_verdict(tmp_db, capsys):
    """
    Scorer far off (est=0.90, resolves NO) vs market price close to correct
    (0.05) must surface the anchoring-risk verdict.
    """
    _insert("c1", "KX1", "YES", 0.05, 0.90, "LOSS", "NO")
    calibration.main()
    out = capsys.readouterr().out
    assert "scorer WORSE than market-price baseline" in out


def test_scorer_beats_baseline_verdict(tmp_db, capsys):
    """Scorer close to correct vs market price far off must print the 'beats' verdict."""
    _insert("c1", "KX1", "YES", 0.90, 0.05, "LOSS", "NO")
    calibration.main()
    out = capsys.readouterr().out
    assert "scorer beats market-price baseline" in out


def test_both_scores_shown_side_by_side(tmp_db, capsys):
    """Scorer Brier and Market Baseline Brier both appear so they can be compared directly."""
    _insert("c1", "KX1", "YES", 0.20, 0.80, "WIN", "YES")
    calibration.main()
    out = capsys.readouterr().out
    assert "Brier Score:" in out
    assert "Market Baseline Brier:" in out
    assert "Scorer vs Baseline:" in out
