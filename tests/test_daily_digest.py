"""
tests/test_daily_digest.py — Offline tests for scripts/daily_digest.py
(unattended-ops: consolidated daily operations digest).

No live Task Scheduler, no live email (core.report.send_report is patched
throughout), no live DB (core.logger.DB_PATH is monkeypatched per test),
no live filesystem beyond tmp_path fixtures.
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
import scripts.daily_digest as dd

CONFIG = {"report": {"email_to": "owner@example.com"}, "environment": "demo"}
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
    conn.execute("""
        CREATE TABLE signals (
            call_id TEXT PRIMARY KEY, timestamp TEXT, ticker TEXT,
            direction TEXT, flag_path TEXT
        )
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(logger, "DB_PATH", str(db_path))
    return db_path


def _healthy_task_results():
    return [{"task": n, "problem": None, "hours_since_run": 1.0, "last_result": 0}
            for n in dd._ahc.TASK_CADENCE_HOURS]


def _write_backlog(tmp_path, items):
    path = tmp_path / "backlog.json"
    path.write_text(json.dumps({"items": items}), encoding="utf-8")
    return path


def _item(id_, status="ready", priority=3, action="Do the thing.", notes=""):
    return {"id": id_, "status": status, "priority": priority,
            "trigger": {"all": []}, "depends_on": [], "action": action, "notes": notes}


# ─── section_task_health ─────────────────────────────────────────────────

def test_section_task_health_all_healthy():
    with patch.object(dd._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()), \
         patch.object(dd._ahc, "check_litestream_replica", return_value={"problem": None, "lag_hours": 0.1}), \
         patch.object(dd._hb, "get_last_run", return_value={"run_id": "run-1", "timestamp": NOW.isoformat()}):
        text, problems = dd.section_task_health(now=NOW)
    assert problems == []
    assert "[!]" not in text
    # every monitored task listed by name, not collapsed into a summary line
    for name in dd._ahc.TASK_CADENCE_HOURS:
        assert name.replace("Leviathan-", "") in text
    assert "[x] Litestream replica" in text
    assert "[x] Daily pipeline (main.py)" in text


def test_section_task_health_labels_weekly_tasks():
    # CodeAudit/WeeklyAudit pass their 192h cadence check on every day of
    # the week, not just the day they ran -- a bare [x] would misread as
    # "ran today" on, say, a Tuesday. Daily tasks get no such suffix.
    with patch.object(dd._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()), \
         patch.object(dd._ahc, "check_litestream_replica", return_value={"problem": None, "lag_hours": 0.1}), \
         patch.object(dd._hb, "get_last_run", return_value={"run_id": "run-1", "timestamp": NOW.isoformat()}):
        text, _ = dd.section_task_health(now=NOW)
    assert "[x] CodeAudit (weekly)" in text
    assert "[x] WeeklyAudit (weekly)" in text
    assert "[x] Heartbeat (weekly)" not in text
    assert "[x] GateNotifier (weekly)" not in text


def test_section_task_health_flags_task_problem():
    results = _healthy_task_results()
    results[0]["problem"] = "no run in 40.0h (threshold 30h)"
    flagged_task = results[0]["task"]
    with patch.object(dd._ahc, "check_scheduled_tasks", return_value=results), \
         patch.object(dd._ahc, "check_litestream_replica", return_value={"problem": None, "lag_hours": 0.1}), \
         patch.object(dd._hb, "get_last_run", return_value={"run_id": "run-1", "timestamp": NOW.isoformat()}):
        text, problems = dd.section_task_health(now=NOW)
    assert len(problems) == 1
    assert flagged_task.replace("Leviathan-", "") in problems[0]
    assert "[!]" in text


def test_section_task_health_flags_litestream_problem():
    with patch.object(dd._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()), \
         patch.object(dd._ahc, "check_litestream_replica",
                       return_value={"problem": "replica is 6.0h behind the live DB", "lag_hours": 6.0}), \
         patch.object(dd._hb, "get_last_run", return_value=None):
        text, problems = dd.section_task_health(now=NOW)
    assert len(problems) == 2  # no runs recorded + litestream lag
    assert "Litestream replica" in text
    assert "no runs recorded" in text


# ─── section_reconciliation ─────────────────────────────────────────────

def test_section_reconciliation_missing_file(tmp_path):
    with patch.object(dd, "RECONCILIATION_DIR", tmp_path):
        text, has_problem = dd.section_reconciliation(now=NOW)
    assert has_problem is False
    assert "No reconciliation file" in text


def test_section_reconciliation_clean(tmp_path):
    data = {"run_at": NOW.isoformat(), "paper_open": 3, "positions_fetched": 3,
            "aligned": [{"ticker": "ABC", "direction": "YES"}],
            "misaligned": [], "unplaced": [], "unexpected": []}
    (tmp_path / "2026-08-24.json").write_text(json.dumps(data), encoding="utf-8")
    with patch.object(dd, "RECONCILIATION_DIR", tmp_path):
        text, has_problem = dd.section_reconciliation(now=NOW)
    assert has_problem is False
    assert "Aligned (1)" in text


def test_section_reconciliation_truncates_long_lists(tmp_path):
    unplaced = [{"ticker": f"TICK{i}", "direction": "YES"} for i in range(40)]
    data = {"run_at": NOW.isoformat(), "paper_open": 40, "positions_fetched": 0,
            "aligned": [], "misaligned": [], "unplaced": unplaced, "unexpected": []}
    (tmp_path / "2026-08-24.json").write_text(json.dumps(data), encoding="utf-8")
    with patch.object(dd, "RECONCILIATION_DIR", tmp_path):
        text, has_problem = dd.section_reconciliation(now=NOW)
    assert has_problem is False
    assert "truncated" in text
    assert len(text.splitlines()) <= dd.MAX_RECONCILIATION_LINES + 2  # header + truncation note


def test_section_reconciliation_flags_misaligned(tmp_path):
    data = {"run_at": NOW.isoformat(), "paper_open": 1, "positions_fetched": 1,
            "aligned": [], "misaligned": [{"ticker": "ABC", "signal": "YES", "position": "NO"}],
            "unplaced": [], "unexpected": []}
    (tmp_path / "2026-08-24.json").write_text(json.dumps(data), encoding="utf-8")
    with patch.object(dd, "RECONCILIATION_DIR", tmp_path):
        text, has_problem = dd.section_reconciliation(now=NOW)
    assert has_problem is True
    assert "MISALIGNED" in text


# ─── section_smart_money ────────────────────────────────────────────────

def test_section_smart_money_missing(tmp_path):
    with patch.object(dd, "SMART_MONEY_LATEST", tmp_path / "nope.json"):
        text = dd.section_smart_money(now=NOW)
    assert "No smart-money cache" in text


def test_section_smart_money_fresh(tmp_path):
    p = tmp_path / "latest_signals.json"
    p.write_text(json.dumps({"run_at": NOW.isoformat(), "signal_count": 4}), encoding="utf-8")
    with patch.object(dd, "SMART_MONEY_LATEST", p):
        text = dd.section_smart_money(now=NOW)
    assert "4" in text and "today" in text


def test_section_smart_money_stale(tmp_path):
    p = tmp_path / "latest_signals.json"
    old = (NOW - timedelta(days=3)).isoformat()
    p.write_text(json.dumps({"run_at": old, "signal_count": 0}), encoding="utf-8")
    with patch.object(dd, "SMART_MONEY_LATEST", p):
        text = dd.section_smart_money(now=NOW)
    assert "stale" in text


# ─── section_resolve_first ──────────────────────────────────────────────

def _insert_signal(db_path, call_id, direction, flag_path, hours_ago, now=NOW):
    ts = (now - timedelta(hours=hours_ago)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO signals (call_id, timestamp, ticker, direction, flag_path) "
                 "VALUES (?,?,?,?,?)", (call_id, ts, "TICK", direction, flag_path))
    conn.commit()
    conn.close()


def test_section_resolve_first_none(_isolate_db):
    text = dd.section_resolve_first(now=NOW)
    assert "No resolve-first-only signals" in text


def test_section_resolve_first_counts_recent_only(_isolate_db):
    _insert_signal(_isolate_db, "a", "YES", "RESOLVE_FIRST", hours_ago=2)
    _insert_signal(_isolate_db, "b", "NO", "RESOLVE_FIRST", hours_ago=3)
    _insert_signal(_isolate_db, "c", "YES", "RESOLVE_FIRST", hours_ago=40)  # outside lookback
    _insert_signal(_isolate_db, "d", "YES", "OTHER_PATH", hours_ago=1)      # wrong flag_path
    text = dd.section_resolve_first(now=NOW)
    assert "2 signal(s)" in text
    assert "YES=1" in text and "NO=1" in text


# ─── section_weekly_logs ────────────────────────────────────────────────

def test_section_weekly_logs_none_fresh(tmp_path):
    with patch.object(dd, "WEEKLY_LOGS", {"Weekly project audit": tmp_path / "nope.log"}):
        assert dd.section_weekly_logs(now=NOW) is None


def test_section_weekly_logs_includes_fresh_tail(tmp_path):
    import os as _os
    log_path = tmp_path / "weekly_audit.log"
    log_path.write_text("line1\nline2\n[weekly_audit] done\n", encoding="utf-8")
    ts = NOW.timestamp() - 3600  # 1h ago -- within freshness window
    _os.utime(log_path, (ts, ts))
    with patch.object(dd, "WEEKLY_LOGS", {"Weekly project audit": log_path}):
        text = dd.section_weekly_logs(now=NOW)
    assert text is not None
    assert "Weekly project audit" in text
    assert "[weekly_audit] done" in text


def test_section_weekly_logs_excludes_old(tmp_path):
    import os as _os
    log_path = tmp_path / "weekly_audit.log"
    log_path.write_text("stuff", encoding="utf-8")
    ts = NOW.timestamp() - (30 * 3600)  # 30h ago -- outside freshness window
    _os.utime(log_path, (ts, ts))
    with patch.object(dd, "WEEKLY_LOGS", {"Weekly project audit": log_path}):
        assert dd.section_weekly_logs(now=NOW) is None


# ─── compose_digest ─────────────────────────────────────────────────────

def test_compose_digest_clean_subject_has_no_attention_flag(tmp_path):
    backlog_path = _write_backlog(tmp_path, [])
    with patch.object(dd._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()), \
         patch.object(dd._ahc, "check_litestream_replica", return_value={"problem": None, "lag_hours": 0.1}), \
         patch.object(dd._hb, "get_last_run", return_value={"run_id": "run-1", "timestamp": NOW.isoformat()}), \
         patch.object(dd, "RECONCILIATION_DIR", tmp_path), \
         patch.object(dd, "SMART_MONEY_LATEST", tmp_path / "nope.json"), \
         patch.object(dd, "WEEKLY_LOGS", {}), \
         patch.object(dd, "BACKLOG_PATH", backlog_path):
        body, subject = dd.compose_digest(now=NOW)
    assert "[ATTENTION]" not in subject
    assert "NEEDS YOUR ATTENTION" in body
    assert "Nothing --" in body
    assert "TASK CHECKLIST" in body and "RECONCILIATION" in body


def test_compose_digest_flags_attention_in_subject(tmp_path):
    results = _healthy_task_results()
    results[0]["problem"] = "no run in 40.0h (threshold 30h)"
    backlog_path = _write_backlog(tmp_path, [])
    with patch.object(dd._ahc, "check_scheduled_tasks", return_value=results), \
         patch.object(dd._ahc, "check_litestream_replica", return_value={"problem": None, "lag_hours": 0.1}), \
         patch.object(dd._hb, "get_last_run", return_value={"run_id": "run-1", "timestamp": NOW.isoformat()}), \
         patch.object(dd, "RECONCILIATION_DIR", tmp_path), \
         patch.object(dd, "SMART_MONEY_LATEST", tmp_path / "nope.json"), \
         patch.object(dd, "WEEKLY_LOGS", {}), \
         patch.object(dd, "BACKLOG_PATH", backlog_path):
        body, subject = dd.compose_digest(now=NOW)
    assert "[ATTENTION]" in subject
    attention_section = body.split("\n\n")[0]
    assert "NEEDS YOUR ATTENTION" in attention_section
    assert "no run in 40.0h" in attention_section
    assert "Nothing --" not in attention_section


def test_compose_digest_attention_aggregates_reconciliation_too(tmp_path):
    data = {"run_at": NOW.isoformat(), "paper_open": 1, "positions_fetched": 1,
            "aligned": [], "misaligned": [{"ticker": "ABC", "signal": "YES", "position": "NO"}],
            "unplaced": [], "unexpected": []}
    (tmp_path / "2026-08-24.json").write_text(json.dumps(data), encoding="utf-8")
    backlog_path = _write_backlog(tmp_path, [])
    with patch.object(dd._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()), \
         patch.object(dd._ahc, "check_litestream_replica", return_value={"problem": None, "lag_hours": 0.1}), \
         patch.object(dd._hb, "get_last_run", return_value={"run_id": "run-1", "timestamp": NOW.isoformat()}), \
         patch.object(dd, "RECONCILIATION_DIR", tmp_path), \
         patch.object(dd, "SMART_MONEY_LATEST", tmp_path / "nope.json"), \
         patch.object(dd, "WEEKLY_LOGS", {}), \
         patch.object(dd, "BACKLOG_PATH", backlog_path):
        body, subject = dd.compose_digest(now=NOW)
    assert "[ATTENTION]" in subject
    attention_section = body.split("\n\n")[0]
    assert "Reconciliation" in attention_section


# ─── section_backlog ────────────────────────────────────────────────────

def test_section_backlog_lists_ready_items_priority_ordered(tmp_path):
    items = [_item("low-pri", status="ready", priority=5, action="Low priority thing."),
             _item("high-pri", status="ready", priority=1, action="High priority thing."),
             _item("done-item", status="done", priority=1, action="Already shipped.")]
    backlog_path = _write_backlog(tmp_path, items)
    with patch.object(dd, "BACKLOG_PATH", backlog_path):
        text, snapshot = dd.section_backlog(prev_snapshot={"seed": {"status": "ready", "notes_len": 0}})
    ready_block = text.split("Changes since last digest")[0]
    assert "Ready (2)" in ready_block
    assert "high-pri" in ready_block and "low-pri" in ready_block
    assert "done-item" not in ready_block
    assert ready_block.index("high-pri") < ready_block.index("low-pri")  # priority order


def test_section_backlog_first_run_reports_no_baseline_not_every_item_new(tmp_path):
    items = [_item("existing-1"), _item("existing-2")]
    backlog_path = _write_backlog(tmp_path, items)
    with patch.object(dd, "BACKLOG_PATH", backlog_path):
        text, snapshot = dd.section_backlog(prev_snapshot=None)
    assert "no prior snapshot yet" in text
    assert "new," not in text  # nothing should be reported as "new" on the very first run
    assert snapshot["existing-1"]["status"] == "ready"


def test_section_backlog_detects_new_item(tmp_path):
    items = [_item("old-item"), _item("brand-new")]
    backlog_path = _write_backlog(tmp_path, items)
    prev_snapshot = {"old-item": {"status": "ready", "notes_len": 0}}
    with patch.object(dd, "BACKLOG_PATH", backlog_path):
        text, snapshot = dd.section_backlog(prev_snapshot=prev_snapshot)
    assert "brand-new (new, ready)" in text
    assert "old-item" not in text.split("Changes since last digest")[1]


def test_section_backlog_detects_status_transition(tmp_path):
    items = [_item("flipped", status="done")]
    backlog_path = _write_backlog(tmp_path, items)
    prev_snapshot = {"flipped": {"status": "ready", "notes_len": 0}}
    with patch.object(dd, "BACKLOG_PATH", backlog_path):
        text, snapshot = dd.section_backlog(prev_snapshot=prev_snapshot)
    assert "flipped (ready -> done)" in text


def test_section_backlog_detects_notes_only_update(tmp_path):
    items = [_item("investigated", status="ready",
                    notes="Checked again to rule out flakiness, still no repro.")]
    backlog_path = _write_backlog(tmp_path, items)
    prev_snapshot = {"investigated": {"status": "ready", "notes_len": 5}}
    with patch.object(dd, "BACKLOG_PATH", backlog_path):
        text, snapshot = dd.section_backlog(prev_snapshot=prev_snapshot)
    assert "investigated (ready, notes updated)" in text
    assert "rule out flakiness" in text


def test_section_backlog_purpose_uses_last_notes_paragraph():
    item = _item("multi-note", notes="First finding here.\n\nSECOND UPDATE: the real reason this changed.")
    purpose = dd._change_purpose(item)
    assert "real reason this changed" in purpose
    assert "First finding" not in purpose


def test_section_backlog_no_changes_when_snapshot_matches(tmp_path):
    items = [_item("stable")]
    backlog_path = _write_backlog(tmp_path, items)
    prev_snapshot = {"stable": {"status": "ready", "notes_len": 0}}
    with patch.object(dd, "BACKLOG_PATH", backlog_path):
        text, snapshot = dd.section_backlog(prev_snapshot=prev_snapshot)
    assert "Changes since last digest (0)" in text
    assert "(none)" in text


# ─── check(): end-to-end ────────────────────────────────────────────────

def _patch_clean_sources(tmp_path, backlog_items=None):
    backlog_path = _write_backlog(tmp_path, backlog_items or [])
    return [
        patch.object(dd._ahc, "check_scheduled_tasks", return_value=_healthy_task_results()),
        patch.object(dd._ahc, "check_litestream_replica", return_value={"problem": None, "lag_hours": 0.1}),
        patch.object(dd._hb, "get_last_run", return_value={"run_id": "run-1", "timestamp": NOW.isoformat()}),
        patch.object(dd, "RECONCILIATION_DIR", tmp_path),
        patch.object(dd, "SMART_MONEY_LATEST", tmp_path / "nope.json"),
        patch.object(dd, "WEEKLY_LOGS", {}),
        patch.object(dd, "BACKLOG_PATH", backlog_path),
    ]


def test_check_sends_once_and_dedupes_same_day(tmp_path):
    state_path = tmp_path / "state.json"
    patches = _patch_clean_sources(tmp_path)
    for p in patches:
        p.start()
    try:
        with patch.object(dd, "send_report") as mock_send:
            result1 = dd.check(CONFIG, state_path=state_path, now=NOW)
            result2 = dd.check(CONFIG, state_path=state_path, now=NOW)
    finally:
        for p in patches:
            p.stop()
    assert result1["sent"] is True
    assert result2["sent"] is False
    assert result2["skipped_duplicate"] is True
    mock_send.assert_called_once()


def test_check_sends_again_next_day(tmp_path):
    state_path = tmp_path / "state.json"
    patches = _patch_clean_sources(tmp_path)
    for p in patches:
        p.start()
    try:
        with patch.object(dd, "send_report") as mock_send:
            dd.check(CONFIG, state_path=state_path, now=NOW)
            dd.check(CONFIG, state_path=state_path, now=NOW + timedelta(days=1))
    finally:
        for p in patches:
            p.stop()
    assert mock_send.call_count == 2


def test_check_dry_run_sends_nothing_and_persists_nothing(tmp_path, capsys):
    state_path = tmp_path / "state.json"
    patches = _patch_clean_sources(tmp_path)
    for p in patches:
        p.start()
    try:
        with patch.object(dd, "send_report") as mock_send:
            result = dd.check(CONFIG, state_path=state_path, dry_run=True, now=NOW)
    finally:
        for p in patches:
            p.stop()
    mock_send.assert_not_called()
    assert result["sent"] is False
    assert not state_path.exists()
    captured = capsys.readouterr()
    assert "Leviathan Daily Digest" in captured.out


def test_check_send_failure_does_not_persist_state(tmp_path):
    state_path = tmp_path / "state.json"
    patches = _patch_clean_sources(tmp_path)
    for p in patches:
        p.start()
    try:
        with patch.object(dd, "send_report", side_effect=RuntimeError("SMTP down")):
            result = dd.check(CONFIG, state_path=state_path, now=NOW)
    finally:
        for p in patches:
            p.stop()
    assert result["error"] == "SMTP down"
    assert not state_path.exists()


def test_check_persists_backlog_snapshot_and_diffs_next_run(tmp_path):
    """
    End-to-end: day 1 has no prior snapshot (reports "no prior snapshot
    yet"), persists one to state.json; day 2 reads that snapshot back and
    correctly reports the item added between the two runs as new, not as
    a repeat of everything from day 1.
    """
    state_path = tmp_path / "state.json"
    day1_patches = _patch_clean_sources(tmp_path, backlog_items=[_item("item-a")])
    for p in day1_patches:
        p.start()
    try:
        with patch.object(dd, "send_report") as mock_send:
            dd.check(CONFIG, state_path=state_path, now=NOW)
            day1_body = mock_send.call_args[0][0]
    finally:
        for p in day1_patches:
            p.stop()
    assert "no prior snapshot yet" in day1_body

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "item-a" in state["backlog_snapshot"]

    day2_patches = _patch_clean_sources(tmp_path, backlog_items=[_item("item-a"), _item("item-b")])
    for p in day2_patches:
        p.start()
    try:
        with patch.object(dd, "send_report") as mock_send:
            dd.check(CONFIG, state_path=state_path, now=NOW + timedelta(days=1))
            day2_body = mock_send.call_args[0][0]
    finally:
        for p in day2_patches:
            p.stop()
    assert "item-b (new, ready)" in day2_body
    assert "item-a (new" not in day2_body  # already in the day-1 snapshot, not re-reported as new
