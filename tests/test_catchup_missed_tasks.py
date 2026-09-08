"""
tests/test_catchup_missed_tasks.py — Offline tests for
scripts/catchup_missed_tasks.py (unattended-ops: wake-triggered catch-up
for missed scheduled runs).

Covers only this script's own decision logic (which tasks it judges
stale) and its handling of Start-ScheduledTask success/failure -- not
whether Windows actually delivers a real launch, which this module's own
docstring is explicit about being unable to verify from an automated
session. No live Task Scheduler, no live DB (core.logger.DB_PATH is
monkeypatched per test), no live subprocess calls (patched throughout).
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import logger
import scripts.catchup_missed_tasks as cm

NOW = datetime(2026, 8, 24, 15, 0, 0, tzinfo=timezone.utc)


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


def _insert_run(db_path, run_id, hours_ago, now=NOW):
    ts = (now - timedelta(hours=hours_ago)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO runs (run_id, timestamp, markets_scanned, signals_generated, "
        "whale_flags, model_used, tokens_used, cost_usd, runtime_ms) "
        "VALUES (?,?,0,0,0,'',0,0,0)", (run_id, ts),
    )
    conn.commit()
    conn.close()


def _healthy_task_results():
    return [{"task": n, "problem": None, "hours_since_run": 1.0, "last_result": 0}
            for n in cm._ahc.TASK_CADENCE_HOURS]


# ─── find_stale_tasks ───────────────────────────────────────────────────

def test_find_stale_tasks_nothing_stale(_isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=1)
    with patch.object(cm._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()):
        assert cm.find_stale_tasks(now=NOW) == []


def test_find_stale_tasks_flags_missed_daily_run(_isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=40)  # past DEFAULT_MAX_SILENCE_HOURS
    with patch.object(cm._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()):
        stale = cm.find_stale_tasks(now=NOW)
    assert cm.DAILY_RUN_TASK in stale


def test_find_stale_tasks_no_runs_at_all_flags_daily_run(_isolate_db):
    with patch.object(cm._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()):
        stale = cm.find_stale_tasks(now=NOW)
    assert cm.DAILY_RUN_TASK in stale


def test_find_stale_tasks_flags_other_stale_task(_isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=1)
    results = _healthy_task_results()
    results[0]["problem"] = "no run in 40.0h (threshold 30h)"
    with patch.object(cm._ahc, "check_scheduled_tasks", return_value=results):
        stale = cm.find_stale_tasks(now=NOW)
    assert results[0]["task"] in stale
    assert cm.DAILY_RUN_TASK not in stale


def test_find_stale_tasks_ignores_bad_result_code_only(_isolate_db):
    """A non-zero result code with no staleness isn't this script's job to relaunch."""
    _insert_run(_isolate_db, "run-1", hours_ago=1)
    results = _healthy_task_results()
    results[0]["problem"] = "last run returned result code 1 (non-zero, not in known-benign list)"
    with patch.object(cm._ahc, "check_scheduled_tasks", return_value=results):
        stale = cm.find_stale_tasks(now=NOW)
    assert stale == []


def test_find_stale_tasks_flags_never_run(_isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=1)
    results = _healthy_task_results()
    results[0]["problem"] = "has never run"
    with patch.object(cm._ahc, "check_scheduled_tasks", return_value=results):
        stale = cm.find_stale_tasks(now=NOW)
    assert results[0]["task"] in stale


# ─── launch_task ─────────────────────────────────────────────────────────

def test_launch_task_success():
    with patch.object(cm.subprocess, "run", return_value=MagicMock(returncode=0, stderr="")) as mock_run:
        ok, msg = cm.launch_task("Leviathan-GateNotifier")
    assert ok is True
    mock_run.assert_called_once()


def test_launch_task_failure():
    with patch.object(cm.subprocess, "run", return_value=MagicMock(returncode=1, stderr="access denied")):
        ok, msg = cm.launch_task("Leviathan-GateNotifier")
    assert ok is False
    assert "access denied" in msg


# ─── run() ──────────────────────────────────────────────────────────────

def test_run_nothing_stale_does_not_call_subprocess(_isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=1)
    with patch.object(cm._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()), \
         patch.object(cm.subprocess, "run") as mock_run:
        result = cm.run(now=NOW)
    assert result == {"stale": [], "launched": [], "failed": {}}
    mock_run.assert_not_called()


def test_run_launches_stale_tasks(_isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=40)
    with patch.object(cm._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()), \
         patch.object(cm.subprocess, "run", return_value=MagicMock(returncode=0, stderr="")) as mock_run:
        result = cm.run(now=NOW)
    assert result["stale"] == [cm.DAILY_RUN_TASK]
    assert result["launched"] == [cm.DAILY_RUN_TASK]
    assert result["failed"] == {}
    mock_run.assert_called_once()


def test_run_dry_run_launches_nothing(_isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=40)
    with patch.object(cm._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()), \
         patch.object(cm.subprocess, "run") as mock_run:
        result = cm.run(dry_run=True, now=NOW)
    assert result["stale"] == [cm.DAILY_RUN_TASK]
    assert result["launched"] == []
    mock_run.assert_not_called()


def test_run_records_launch_failures(_isolate_db):
    _insert_run(_isolate_db, "run-1", hours_ago=40)
    with patch.object(cm._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()), \
         patch.object(cm.subprocess, "run", return_value=MagicMock(returncode=1, stderr="boom")):
        result = cm.run(now=NOW)
    assert result["failed"] == {cm.DAILY_RUN_TASK: "boom"}
    assert result["launched"] == []
