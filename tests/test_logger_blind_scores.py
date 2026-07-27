"""
tests/test_logger_blind_scores.py — Offline tests for core.logger.log_blind_score
and the blind_scores table (backlog: price-blind-arm).

No live DB: core.logger.DB_PATH is monkeypatched to a throwaway sqlite file
per test, same pattern as tests/test_heartbeat_check.py.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import logger


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE blind_scores (
            call_id TEXT PRIMARY KEY, timestamp TEXT, run_id TEXT, ticker TEXT,
            title TEXT, estimate REAL, confidence TEXT, reasoning TEXT,
            sources_checked TEXT, market_price_at_score REAL, cost_usd REAL
        )
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(logger, "DB_PATH", str(db_path))
    return db_path


def _read_rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM blind_scores").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_log_blind_score_persists_all_fields(_isolate_db):
    logger.log_blind_score({
        "run_id": "run1", "ticker": "KXFOO-01", "title": "Will foo?",
        "estimate": 0.42, "confidence": "MED", "reasoning": "test reasoning",
        "sources_checked": ["a.com", "b.com"], "market_price_at_score": 0.55,
        "cost_usd": 0.0123,
    })
    rows = _read_rows(_isolate_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == "run1"
    assert row["ticker"] == "KXFOO-01"
    assert row["estimate"] == pytest.approx(0.42)
    assert row["confidence"] == "MED"
    assert json.loads(row["sources_checked"]) == ["a.com", "b.com"]
    assert row["market_price_at_score"] == pytest.approx(0.55)
    assert row["cost_usd"] == pytest.approx(0.0123)


def test_log_blind_score_never_touches_signals_table():
    """blind_scores is a structurally separate table -- log_blind_score must
    not write anything into signals, which is what feeds signal selection."""
    import inspect
    src = inspect.getsource(logger.log_blind_score)
    assert "INSERT" in src
    assert "INTO blind_scores" in src
    assert "INTO signals" not in src


def test_missing_sources_checked_defaults_to_empty_list(_isolate_db):
    logger.log_blind_score({
        "run_id": "run1", "ticker": "KXFOO-01", "estimate": 0.3,
        "confidence": "LOW", "reasoning": "x",
    })
    rows = _read_rows(_isolate_db)
    assert json.loads(rows[0]["sources_checked"]) == []


def test_log_blind_score_failure_is_non_fatal(monkeypatch):
    """A DB error must be swallowed (printed), never raised -- the blind arm
    is a shadow experiment and must never take down the main run."""
    monkeypatch.setattr(logger, "DB_PATH", "Z:\\definitely\\not\\a\\real\\path.db")
    logger.log_blind_score({"run_id": "run1", "ticker": "KXFOO-01"})  # must not raise
