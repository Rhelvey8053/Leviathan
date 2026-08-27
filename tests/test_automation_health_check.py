"""
tests/test_automation_health_check.py — Offline tests for
scripts/automation_health_check.py (unattended-ops: scheduled-task drift +
Litestream replica lag).

No live Task Scheduler (get_raw_task_info is patched throughout), no live
email (core.report.send_report is patched throughout), no live filesystem
beyond tmp_path fixtures.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import scripts.automation_health_check as ahc

CONFIG = {"report": {"email_to": "owner@example.com"}, "environment": "demo"}


def _dotnet_date(dt: datetime) -> str:
    return f"/Date({int(dt.timestamp() * 1000)})/"


def _task_entry(name, hours_ago=1, result=0, now=None):
    now = now or datetime.now(timezone.utc)
    last_run = now - timedelta(hours=hours_ago)
    return {"TaskName": name, "LastRunTime": _dotnet_date(last_run), "LastTaskResult": result}


def _all_healthy_tasks(now=None):
    return [_task_entry(name, hours_ago=1, result=0, now=now) for name in ahc.TASK_CADENCE_HOURS]


# ─── _parse_dotnet_date ─────────────────────────────────────────────────────

def test_parse_dotnet_date_roundtrips():
    now = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
    parsed = ahc._parse_dotnet_date(_dotnet_date(now))
    assert parsed == now


def test_parse_dotnet_date_handles_missing():
    assert ahc._parse_dotnet_date(None) is None
    assert ahc._parse_dotnet_date("") is None


# ─── check_scheduled_tasks ──────────────────────────────────────────────────

def test_check_scheduled_tasks_all_healthy():
    with patch.object(ahc, "get_raw_task_info", return_value=_all_healthy_tasks()):
        results = ahc.check_scheduled_tasks()
    assert all(r["problem"] is None for r in results)
    assert len(results) == len(ahc.TASK_CADENCE_HOURS)


def test_check_scheduled_tasks_flags_stale():
    now = datetime.now(timezone.utc)
    tasks = _all_healthy_tasks(now=now)
    for t in tasks:
        if t["TaskName"] == "Leviathan-GateNotifier":
            t["LastRunTime"] = _dotnet_date(now - timedelta(hours=40))
    with patch.object(ahc, "get_raw_task_info", return_value=tasks):
        results = ahc.check_scheduled_tasks(now=now)
    problem = next(r for r in results if r["task"] == "Leviathan-GateNotifier")
    assert problem["problem"] is not None
    assert "40" in problem["problem"] or "no run in" in problem["problem"]


def test_check_scheduled_tasks_flags_missing_task():
    tasks = [t for t in _all_healthy_tasks() if t["TaskName"] != "Leviathan-WeeklyAudit"]
    with patch.object(ahc, "get_raw_task_info", return_value=tasks):
        results = ahc.check_scheduled_tasks()
    problem = next(r for r in results if r["task"] == "Leviathan-WeeklyAudit")
    assert "not found" in problem["problem"]


def test_check_scheduled_tasks_flags_nonbenign_result_code():
    tasks = _all_healthy_tasks()
    for t in tasks:
        if t["TaskName"] == "Leviathan-ResolveFirst":
            t["LastTaskResult"] = 1  # generic failure code
    with patch.object(ahc, "get_raw_task_info", return_value=tasks):
        results = ahc.check_scheduled_tasks()
    problem = next(r for r in results if r["task"] == "Leviathan-ResolveFirst")
    assert "result code 1" in problem["problem"]


def test_check_scheduled_tasks_allows_benign_result_code():
    tasks = _all_healthy_tasks()
    for t in tasks:
        if t["TaskName"] == "Leviathan-CodeAudit":
            t["LastTaskResult"] = 267014  # SCHED_S_TASK_TERMINATED, allowlisted
    with patch.object(ahc, "get_raw_task_info", return_value=tasks):
        results = ahc.check_scheduled_tasks()
    problem = next(r for r in results if r["task"] == "Leviathan-CodeAudit")
    assert problem["problem"] is None


def test_check_scheduled_tasks_reports_both_stale_and_bad_result_code():
    now = datetime.now(timezone.utc)
    tasks = _all_healthy_tasks(now=now)
    for t in tasks:
        if t["TaskName"] == "Leviathan-ResolveFirst":
            t["LastRunTime"] = _dotnet_date(now - timedelta(hours=40))
            t["LastTaskResult"] = 1
    with patch.object(ahc, "get_raw_task_info", return_value=tasks):
        results = ahc.check_scheduled_tasks(now=now)
    problem = next(r for r in results if r["task"] == "Leviathan-ResolveFirst")["problem"]
    assert "no run in" in problem
    assert "result code 1" in problem


def test_check_scheduled_tasks_query_failure_flags_every_task():
    with patch.object(ahc, "get_raw_task_info", side_effect=RuntimeError("powershell not found")):
        results = ahc.check_scheduled_tasks()
    assert len(results) == len(ahc.TASK_CADENCE_HOURS)
    assert all("could not query Task Scheduler" in r["problem"] for r in results)


def test_check_scheduled_tasks_never_run_but_next_run_pending_is_not_a_problem():
    """
    Real bug found 2026-08-27: Leviathan-CodeAudit's weekly Sunday 11am
    trigger was correctly registered but flagged "[!] has never run" every
    day between registration and its first Sunday, because this function
    never consulted NextRunTime at all -- a brand-new weekly task hasn't
    missed anything just because it hasn't reached its first scheduled
    occurrence yet.
    """
    now = datetime.now(timezone.utc)
    tasks = _all_healthy_tasks(now=now)
    for t in tasks:
        if t["TaskName"] == "Leviathan-CodeAudit":
            t["LastRunTime"] = None
            t["NextRunTime"] = _dotnet_date(now + timedelta(days=2))  # first occurrence still ahead
    with patch.object(ahc, "get_raw_task_info", return_value=tasks):
        results = ahc.check_scheduled_tasks(now=now)
    problem = next(r for r in results if r["task"] == "Leviathan-CodeAudit")
    assert problem["problem"] is None


def test_check_scheduled_tasks_never_run_and_next_run_overdue_is_flagged():
    """The other half: if a task has never run AND its own next-scheduled
    occurrence has already passed, that's a real missed fire, not a
    pending-first-run task -- must still flag."""
    now = datetime.now(timezone.utc)
    tasks = _all_healthy_tasks(now=now)
    for t in tasks:
        if t["TaskName"] == "Leviathan-CodeAudit":
            t["LastRunTime"] = None
            t["NextRunTime"] = _dotnet_date(now - timedelta(hours=1))  # already overdue
    with patch.object(ahc, "get_raw_task_info", return_value=tasks):
        results = ahc.check_scheduled_tasks(now=now)
    problem = next(r for r in results if r["task"] == "Leviathan-CodeAudit")
    assert problem["problem"] == "has never run"


def test_check_scheduled_tasks_never_run_and_no_next_run_is_flagged():
    """Ambiguous case (NextRunTime itself missing/unparseable) fails
    toward flagging, not silence -- same conservative default as before
    this fix existed."""
    now = datetime.now(timezone.utc)
    tasks = _all_healthy_tasks(now=now)
    for t in tasks:
        if t["TaskName"] == "Leviathan-CodeAudit":
            t["LastRunTime"] = None
            t["NextRunTime"] = None
    with patch.object(ahc, "get_raw_task_info", return_value=tasks):
        results = ahc.check_scheduled_tasks(now=now)
    problem = next(r for r in results if r["task"] == "Leviathan-CodeAudit")
    assert problem["problem"] == "has never run"


# ─── check_litestream_replica ───────────────────────────────────────────────

def _touch(path: Path, mtime: datetime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    ts = mtime.timestamp()
    import os as _os
    _os.utime(path, (ts, ts))


def test_litestream_replica_fresh_is_healthy(tmp_path):
    now = datetime.now(timezone.utc)
    db_path = tmp_path / "leviathan.db"
    replica_dir = tmp_path / "replica"
    _touch(db_path, now)
    _touch(replica_dir / "gen" / "00000001.ltx", now - timedelta(minutes=5))
    result = ahc.check_litestream_replica(replica_dir=replica_dir, live_db_path=db_path)
    assert result["problem"] is None


def test_litestream_replica_lagging_is_flagged(tmp_path):
    now = datetime.now(timezone.utc)
    db_path = tmp_path / "leviathan.db"
    replica_dir = tmp_path / "replica"
    _touch(db_path, now)
    _touch(replica_dir / "gen" / "00000001.ltx", now - timedelta(hours=6))
    result = ahc.check_litestream_replica(replica_dir=replica_dir, live_db_path=db_path)
    assert result["problem"] is not None
    assert "behind" in result["problem"]


def test_litestream_replica_missing_dir_is_flagged(tmp_path):
    db_path = tmp_path / "leviathan.db"
    _touch(db_path, datetime.now(timezone.utc))
    result = ahc.check_litestream_replica(
        replica_dir=tmp_path / "does_not_exist", live_db_path=db_path)
    assert "no replica files found" in result["problem"]


def test_litestream_missing_db_is_flagged(tmp_path):
    result = ahc.check_litestream_replica(
        replica_dir=tmp_path / "replica", live_db_path=tmp_path / "nope.db")
    assert "live DB not found" in result["problem"]


# ─── check(): end-to-end ────────────────────────────────────────────────────

def test_check_healthy_sends_nothing(tmp_path):
    now = datetime.now(timezone.utc)
    db_path = tmp_path / "leviathan.db"
    replica_dir = tmp_path / "replica"
    _touch(db_path, now)
    _touch(replica_dir / "00000001.ltx", now)
    with patch.object(ahc, "get_raw_task_info", return_value=_all_healthy_tasks()), \
         patch.object(ahc, "LITESTREAM_REPLICA_DIR", replica_dir), \
         patch.object(ahc, "LIVE_DB_PATH", db_path), \
         patch.object(ahc, "send_report") as mock_send:
        result = ahc.check(CONFIG, state_path=tmp_path / "state.json")
    assert result["healthy"] is True
    mock_send.assert_not_called()


def test_check_unhealthy_sends_consolidated_alert(tmp_path):
    now = datetime.now(timezone.utc)
    tasks = _all_healthy_tasks(now=now)
    for t in tasks:
        if t["TaskName"] == "Leviathan-GateNotifier":
            t["LastRunTime"] = _dotnet_date(now - timedelta(hours=40))
    db_path = tmp_path / "leviathan.db"
    replica_dir = tmp_path / "replica"
    _touch(db_path, now)
    _touch(replica_dir / "00000001.ltx", now - timedelta(hours=6))  # also lagging
    state_path = tmp_path / "state.json"
    with patch.object(ahc, "get_raw_task_info", return_value=tasks), \
         patch.object(ahc, "LITESTREAM_REPLICA_DIR", replica_dir), \
         patch.object(ahc, "LIVE_DB_PATH", db_path), \
         patch.object(ahc, "send_report") as mock_send:
        result = ahc.check(CONFIG, state_path=state_path)
    assert result["healthy"] is False
    assert result["sent"] is True
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert "2 problems" in kwargs["subject_override"]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(state["alerted"]) == {"task:Leviathan-GateNotifier", "litestream"}


def test_check_does_not_realert_same_problem_same_day(tmp_path):
    now = datetime.now(timezone.utc)
    tasks = _all_healthy_tasks(now=now)
    for t in tasks:
        if t["TaskName"] == "Leviathan-GateNotifier":
            t["LastRunTime"] = _dotnet_date(now - timedelta(hours=40))
    db_path = tmp_path / "leviathan.db"
    replica_dir = tmp_path / "replica"
    _touch(db_path, now)
    _touch(replica_dir / "00000001.ltx", now)
    state_path = tmp_path / "state.json"
    with patch.object(ahc, "get_raw_task_info", return_value=tasks), \
         patch.object(ahc, "LITESTREAM_REPLICA_DIR", replica_dir), \
         patch.object(ahc, "LIVE_DB_PATH", db_path), \
         patch.object(ahc, "send_report") as mock_send:
        ahc.check(CONFIG, state_path=state_path)
        result2 = ahc.check(CONFIG, state_path=state_path)
    assert mock_send.call_count == 1
    assert result2["sent"] is False


def test_check_realerts_when_new_problem_appears_same_day(tmp_path):
    now = datetime.now(timezone.utc)
    tasks = _all_healthy_tasks(now=now)
    for t in tasks:
        if t["TaskName"] == "Leviathan-GateNotifier":
            t["LastRunTime"] = _dotnet_date(now - timedelta(hours=40))
    db_path = tmp_path / "leviathan.db"
    replica_dir = tmp_path / "replica"
    _touch(db_path, now)
    _touch(replica_dir / "00000001.ltx", now)
    state_path = tmp_path / "state.json"
    with patch.object(ahc, "get_raw_task_info", return_value=tasks), \
         patch.object(ahc, "LITESTREAM_REPLICA_DIR", replica_dir), \
         patch.object(ahc, "LIVE_DB_PATH", db_path), \
         patch.object(ahc, "send_report") as mock_send:
        ahc.check(CONFIG, state_path=state_path)
        # a second, distinct problem shows up later the same day
        tasks2 = [dict(t) for t in tasks]
        for t in tasks2:
            if t["TaskName"] == "Leviathan-ResolveFirst":
                t["LastTaskResult"] = 1
        with patch.object(ahc, "get_raw_task_info", return_value=tasks2):
            result2 = ahc.check(CONFIG, state_path=state_path)
    assert mock_send.call_count == 2
    assert result2["sent"] is True


def test_check_dry_run_sends_nothing_and_persists_nothing(tmp_path, capsys):
    now = datetime.now(timezone.utc)
    tasks = _all_healthy_tasks(now=now)
    for t in tasks:
        if t["TaskName"] == "Leviathan-GateNotifier":
            t["LastRunTime"] = _dotnet_date(now - timedelta(hours=40))
    db_path = tmp_path / "leviathan.db"
    replica_dir = tmp_path / "replica"
    _touch(db_path, now)
    _touch(replica_dir / "00000001.ltx", now)
    state_path = tmp_path / "state.json"
    with patch.object(ahc, "get_raw_task_info", return_value=tasks), \
         patch.object(ahc, "LITESTREAM_REPLICA_DIR", replica_dir), \
         patch.object(ahc, "LIVE_DB_PATH", db_path), \
         patch.object(ahc, "send_report") as mock_send:
        result = ahc.check(CONFIG, state_path=state_path, dry_run=True)
    mock_send.assert_not_called()
    assert result["sent"] is False
    assert not state_path.exists()
    captured = capsys.readouterr()
    assert "ALERT" in captured.out


def test_check_send_failure_does_not_persist_state(tmp_path):
    now = datetime.now(timezone.utc)
    tasks = _all_healthy_tasks(now=now)
    for t in tasks:
        if t["TaskName"] == "Leviathan-GateNotifier":
            t["LastRunTime"] = _dotnet_date(now - timedelta(hours=40))
    db_path = tmp_path / "leviathan.db"
    replica_dir = tmp_path / "replica"
    _touch(db_path, now)
    _touch(replica_dir / "00000001.ltx", now)
    state_path = tmp_path / "state.json"
    with patch.object(ahc, "get_raw_task_info", return_value=tasks), \
         patch.object(ahc, "LITESTREAM_REPLICA_DIR", replica_dir), \
         patch.object(ahc, "LIVE_DB_PATH", db_path), \
         patch.object(ahc, "send_report", side_effect=RuntimeError("SMTP down")):
        result = ahc.check(CONFIG, state_path=state_path)
    assert result["error"] == "SMTP down"
    assert not state_path.exists()
