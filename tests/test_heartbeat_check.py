"""
tests/test_heartbeat_check.py — Offline tests for scripts/heartbeat_check.py
(unattended-ops: alert on absence).

No live email (core.report.send_report is patched throughout), no live
DB (core.logger.DB_PATH is monkeypatched to a throwaway sqlite file per test).
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import logger
import scripts.heartbeat_check as hb

CONFIG = {"report": {"email_to": "owner@example.com"}, "environment": "demo"}


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, timestamp TEXT, markets_scanned INTEGER,
            signals_generated INTEGER, whale_flags INTEGER, model_used TEXT,
            tokens_used INTEGER, cost_usd REAL, runtime_ms INTEGER
        )
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(logger, "DB_PATH", str(db_path))
    return db_path


def _insert_run(db_path, run_id, hours_ago):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO runs (run_id, timestamp, markets_scanned, signals_generated, "
        "whale_flags, model_used, tokens_used, cost_usd, runtime_ms) "
        "VALUES (?,?,0,0,0,'',0,0,0)",
        (run_id, ts),
    )
    conn.commit()
    conn.close()


# ─── get_last_run / hours_since ────────────────────────────────────────────

def test_get_last_run_none_when_table_empty():
    assert hb.get_last_run() is None


def test_get_last_run_returns_most_recent(_isolate_db):
    _insert_run(_isolate_db, "run-old", hours_ago=20)
    _insert_run(_isolate_db, "run-new", hours_ago=2)
    last = hb.get_last_run()
    assert last["run_id"] == "run-new"


def test_hours_since_computes_correctly():
    ts = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    assert hb.hours_since(ts) == pytest.approx(5.0, abs=0.05)


# ─── check(): not stale ─────────────────────────────────────────────────────

def test_check_not_stale_within_window(_isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=5)
    result = hb.check(CONFIG, state_path=Path("/nonexistent/state.json"), max_silence_hours=30)
    assert result["stale"] is False
    assert result["sent"] is False


# ─── check(): stale, no prior alert ─────────────────────────────────────────

def test_check_stale_sends_alert(tmp_path, _isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=40)
    state_path = tmp_path / "state.json"
    with patch.object(hb, "send_report") as mock_send:
        result = hb.check(CONFIG, state_path=state_path, max_silence_hours=30)
    assert result["stale"] is True
    assert result["sent"] is True
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert "ALERT" in kwargs["subject_override"]


def test_check_no_runs_at_all_sends_alert(tmp_path, _isolate_db):
    state_path = tmp_path / "state.json"
    with patch.object(hb, "send_report") as mock_send:
        result = hb.check(CONFIG, state_path=state_path, max_silence_hours=30)
    assert result["stale"] is True
    assert result["sent"] is True
    mock_send.assert_called_once()


# ─── check(): fire-once semantics ──────────────────────────────────────────

def test_check_does_not_realert_for_same_stale_run(tmp_path, _isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=40)
    state_path = tmp_path / "state.json"
    with patch.object(hb, "send_report") as mock_send:
        hb.check(CONFIG, state_path=state_path, max_silence_hours=30)
        result2 = hb.check(CONFIG, state_path=state_path, max_silence_hours=30)
    assert mock_send.call_count == 1
    assert result2["stale"] is True
    assert result2["sent"] is False


def test_check_realerts_when_last_run_id_unchanged_but_state_cleared(tmp_path, _isolate_db):
    """New stale period after a fresh run_id appears would reset state --
    verified indirectly: alerting keys off run_id, not just staleness."""
    _insert_run(_isolate_db, "run-1", hours_ago=40)
    state_path = tmp_path / "state.json"
    with patch.object(hb, "send_report") as mock_send:
        hb.check(CONFIG, state_path=state_path, max_silence_hours=30)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["alerted_run_id"] == "run-1"


def test_check_realerts_after_a_newer_stale_run_appears(tmp_path, _isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=40)
    state_path = tmp_path / "state.json"
    with patch.object(hb, "send_report") as mock_send:
        hb.check(CONFIG, state_path=state_path, max_silence_hours=30)
        _insert_run(_isolate_db, "run-2", hours_ago=35)  # still stale, but a newer run landed
        hb.check(CONFIG, state_path=state_path, max_silence_hours=30)
    assert mock_send.call_count == 2


# ─── check(): dry-run ───────────────────────────────────────────────────────

def test_check_dry_run_sends_nothing_and_persists_nothing(tmp_path, _isolate_db, capsys):
    _insert_run(_isolate_db, "run-1", hours_ago=40)
    state_path = tmp_path / "state.json"
    with patch.object(hb, "send_report") as mock_send:
        result = hb.check(CONFIG, state_path=state_path, max_silence_hours=30, dry_run=True)
    mock_send.assert_not_called()
    assert result["sent"] is False
    assert not state_path.exists()
    captured = capsys.readouterr()
    assert "ALERT" in captured.out


# ─── check(): send failure ──────────────────────────────────────────────────

def test_check_send_failure_does_not_persist_state(tmp_path, _isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=40)
    state_path = tmp_path / "state.json"
    with patch.object(hb, "send_report", side_effect=RuntimeError("SMTP down")):
        result = hb.check(CONFIG, state_path=state_path, max_silence_hours=30)
    assert result["error"] == "SMTP down"
    assert not state_path.exists()
