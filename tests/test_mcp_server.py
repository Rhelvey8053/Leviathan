"""
tests/test_mcp_server.py — Tests for mcp_server/server.py.

Uses a throwaway SQLite DB (never leviathan.db) — same tmp_db pattern as
test_logger.py. Tools are called through mcp.call_tool() end-to-end so
the MCP schema/dispatch layer is exercised, not just the underlying
core.logger query functions (which have their own dedicated tests).
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import logger
from mcp_server import server


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=False)
def tmp_db(tmp_path, monkeypatch):
    """Fresh throwaway DB for each test — never touches leviathan.db."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(logger, "DB_PATH", db_file)
    logger._init_db()
    return db_file


def _insert(call_id, ticker, direction, market_price,
            outcome="", result="", pnl=None, edge=0.10):
    """Insert a signal row directly into whatever DB logger.DB_PATH points at."""
    with logger._db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO signals
            (call_id, timestamp, ticker, title, market_price, our_estimate,
             edge, direction, confidence, whale_detected, whale_direction,
             outcome, result, pnl_if_traded, run_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id,
            datetime.now(timezone.utc).isoformat(),
            ticker, "Test Market",
            market_price, 0.40,
            edge,
            direction, "MED", 0, "",
            outcome, result, pnl,
            "run-test",
        ))


def _call(tool_name, **kwargs):
    """
    Call an MCP tool end-to-end; return the structured result list/dict.

    FastMCP only returns the (content, structured) tuple for tools whose
    return type it can infer an output schema for (e.g. list[dict]) --
    a tool returning a plain, heterogeneously-shaped dict (get_backlog_status)
    falls back to unstructured content only, a bare list of TextContent.
    Handle both rather than assuming every tool takes the structured path.
    """
    result = asyncio.run(server.mcp.call_tool(tool_name, kwargs))
    if isinstance(result, tuple) and len(result) == 2:
        _content, structured = result
        return structured.get("result", structured)
    content = result[0] if isinstance(result, list) else result
    return json.loads(content.text)


# ─── server scaffold ──────────────────────────────────────────────────────────

def test_server_has_seven_tools():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "get_signal_log", "get_resolved_track_record", "lookup_market",
        "get_run_history", "get_category_breakdown",
        "get_backlog_status", "get_pipeline_health",
    }


def test_server_name_is_leviathan():
    assert server.mcp.name == "leviathan"


# ─── get_signal_log ───────────────────────────────────────────────────────────

def test_get_signal_log_tool_returns_real_rows(tmp_db):
    _insert("t1", "KXTOOL1", "YES", 0.40)
    rows = _call("get_signal_log", limit=10)
    assert any(r["ticker"] == "KXTOOL1" for r in rows)


def test_get_signal_log_tool_excludes_pass(tmp_db):
    _insert("t2", "KXTOOLPASS", "PASS", 0.40)
    rows = _call("get_signal_log", limit=10)
    assert not any(r["ticker"] == "KXTOOLPASS" for r in rows)


def test_get_signal_log_tool_respects_limit(tmp_db):
    for i in range(5):
        _insert(f"tl{i}", f"KXTOOLLIM{i}", "YES", 0.40)
    rows = _call("get_signal_log", limit=2)
    assert len(rows) == 2


def test_get_signal_log_tool_ticker_filter(tmp_db):
    _insert("t3", "KXTOOLFOO", "YES", 0.40)
    _insert("t4", "KXTOOLBAR", "YES", 0.40)
    rows = _call("get_signal_log", limit=10, ticker="KXTOOLFOO")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "KXTOOLFOO"


def test_get_signal_log_tool_resolved_only(tmp_db):
    _insert("t5", "KXTOOLUNRES", "YES", 0.40)
    _insert("t6", "KXTOOLRES", "YES", 0.40, outcome="yes", result="WIN")
    rows = _call("get_signal_log", limit=10, resolved_only=True)
    tickers = [r["ticker"] for r in rows]
    assert "KXTOOLRES" in tickers
    assert "KXTOOLUNRES" not in tickers


# ─── get_resolved_track_record ────────────────────────────────────────────────

def test_get_resolved_track_record_tool_only_resolved(tmp_db):
    _insert("t7", "KXTOOLOPEN", "YES", 0.40)
    _insert("t8", "KXTOOLDONE", "YES", 0.40, outcome="yes", result="WIN", pnl=0.60)
    rows = _call("get_resolved_track_record")
    tickers = [r["ticker"] for r in rows]
    assert "KXTOOLDONE" in tickers
    assert "KXTOOLOPEN" not in tickers


def test_get_resolved_track_record_tool_excludes_pass(tmp_db):
    _insert("t9", "KXTOOLPASSDONE", "PASS", 0.40, outcome="yes", result="")
    rows = _call("get_resolved_track_record")
    assert not any(r["ticker"] == "KXTOOLPASSDONE" for r in rows)


def test_get_resolved_track_record_tool_has_score_and_outcome(tmp_db):
    _insert("t10", "KXTOOLSCORED", "YES", 0.40, outcome="yes", result="WIN", pnl=0.60)
    rows = _call("get_resolved_track_record")
    row = next(r for r in rows if r["ticker"] == "KXTOOLSCORED")
    assert row["our_estimate"] == 0.40
    assert row["outcome"] == "yes"
    assert row["result"] == "WIN"


def test_get_resolved_track_record_tool_no_args_needed():
    """Tool takes zero arguments — the schema should have no properties."""
    tools = asyncio.run(server.mcp.list_tools())
    tool = next(t for t in tools if t.name == "get_resolved_track_record")
    assert tool.inputSchema.get("properties", {}) == {}


# ─── lookup_market ────────────────────────────────────────────────────────────

def test_lookup_market_tool_by_ticker_partial_match(tmp_db):
    _insert("t11", "KXCABLEAVE-TOOL", "YES", 0.40)
    rows = _call("lookup_market", ticker="CABLEAVE")
    assert any(r["ticker"] == "KXCABLEAVE-TOOL" for r in rows)


def test_lookup_market_tool_by_date(tmp_db):
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source)
            VALUES ('dated_tool','2026-05-03T12:00:00+00:00','KXDATEDTOOL','YES',0.30,'paper')
        """)
    rows = _call("lookup_market", date="2026-05-03")
    assert any(r["ticker"] == "KXDATEDTOOL" for r in rows)


def test_lookup_market_tool_date_excludes_other_days(tmp_db):
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source)
            VALUES ('dated_tool2','2026-05-04T12:00:00+00:00','KXOTHERDAYTOOL','YES',0.30,'paper')
        """)
    rows = _call("lookup_market", date="2026-05-03")
    assert not any(r["ticker"] == "KXOTHERDAYTOOL" for r in rows)


def test_lookup_market_tool_no_filters_returns_empty(tmp_db):
    _insert("t12", "KXTOOLANY", "YES", 0.40)
    rows = _call("lookup_market")
    assert rows == []


# ─── get_run_history ──────────────────────────────────────────────────────────

def _insert_run(run_id, timestamp, model_used="claude-sonnet-4-6", signals_generated=0):
    with logger._db() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, timestamp, markets_scanned, signals_generated, "
            "whale_flags, model_used, tokens_used, cost_usd, runtime_ms) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, timestamp, 100, signals_generated, 5, model_used, 0, 0.0, 60000),
        )


def test_get_run_history_tool_returns_real_rows(tmp_db):
    _insert_run("r1", "2026-09-01T12:00:00+00:00")
    rows = _call("get_run_history", limit=10)
    assert any(r["run_id"] == "r1" for r in rows)


def test_get_run_history_tool_newest_first(tmp_db):
    _insert_run("r_old", "2026-08-30T12:00:00+00:00")
    _insert_run("r_new", "2026-09-01T12:00:00+00:00")
    rows = _call("get_run_history", limit=10)
    assert rows[0]["run_id"] == "r_new"


def test_get_run_history_tool_respects_limit(tmp_db):
    for i in range(5):
        _insert_run(f"r{i}", f"2026-08-2{i}T12:00:00+00:00")
    rows = _call("get_run_history", limit=2)
    assert len(rows) == 2


def test_get_run_history_tool_includes_model_used(tmp_db):
    _insert_run("r_opus", "2026-09-01T12:00:00+00:00", model_used="opus")
    rows = _call("get_run_history", limit=10)
    row = next(r for r in rows if r["run_id"] == "r_opus")
    assert row["model_used"] == "opus"


# ─── get_category_breakdown ───────────────────────────────────────────────────

def test_get_category_breakdown_tool_groups_by_category_and_flag_path(tmp_db):
    with logger._db() as conn:
        conn.execute(
            "INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, "
            "source, category, flag_path) VALUES "
            "('c1','2026-09-01T00:00:00+00:00','KXCATA','YES',0.3,'paper','Politics','DRIFT')"
        )
        conn.execute(
            "INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, "
            "source, category, flag_path) VALUES "
            "('c2','2026-09-01T00:00:00+00:00','KXCATB','YES',0.3,'paper','','RESOLVE_FIRST')"
        )
    rows = _call("get_category_breakdown")
    by_key = {(r["category"], r["flag_path"]): r["n"] for r in rows}
    assert by_key.get(("Politics", "DRIFT")) == 1
    assert by_key.get(("", "RESOLVE_FIRST")) == 1


def test_get_category_breakdown_tool_excludes_pass(tmp_db):
    _insert("c3", "KXCATPASS", "PASS", 0.30)
    rows = _call("get_category_breakdown")
    tickers_covered = sum(r["n"] for r in rows)
    assert tickers_covered == 0


# ─── get_backlog_status ───────────────────────────────────────────────────────

_FAKE_BACKLOG = {
    "items": [
        {"id": "ready-1", "title": "Ready thing", "status": "ready", "priority": 2,
         "area": "infra", "action": "Do the ready thing.", "trigger": {"all": []},
         "depends_on": []},
        {"id": "locked-1", "title": "Locked thing", "status": "locked", "priority": 3,
         "area": "data-quality", "action": "Do the locked thing.",
         "trigger": {"all": [{"metric": "resolved_count", "op": ">=", "value": 30}]},
         "depends_on": []},
        {"id": "blocked-1", "title": "Blocked thing", "status": "blocked", "priority": 4,
         "area": "infra", "action": "Do the blocked thing.", "trigger": {"all": []},
         "depends_on": ["locked-1"]},
        {"id": "done-1", "title": "Done thing", "status": "done", "priority": 1,
         "area": "infra", "action": "Already done.", "trigger": {"all": []},
         "depends_on": []},
    ]
}
_FAKE_METRICS = {"resolved_count": 22, "resolved_count_per_category_max": 3, "_data_gaps": []}


def test_get_backlog_status_tool_counts_by_status():
    with patch.object(server, "load_backlog", return_value=_FAKE_BACKLOG), \
         patch.object(server._checker, "compute_metrics", return_value=_FAKE_METRICS):
        result = _call("get_backlog_status")
    assert result["counts"] == {"ready": 1, "locked": 1, "blocked": 1, "done": 1, "total": 4}


def test_get_backlog_status_tool_locked_item_shows_gate_progress():
    with patch.object(server, "load_backlog", return_value=_FAKE_BACKLOG), \
         patch.object(server._checker, "compute_metrics", return_value=_FAKE_METRICS):
        result = _call("get_backlog_status")
    locked = next(i for i in result["locked_items"] if i["id"] == "locked-1")
    assert "resolved_count=22" in locked["gate_or_deps"]
    assert "not met" in locked["gate_or_deps"]


def test_get_backlog_status_tool_blocked_item_shows_deps():
    with patch.object(server, "load_backlog", return_value=_FAKE_BACKLOG), \
         patch.object(server._checker, "compute_metrics", return_value=_FAKE_METRICS):
        result = _call("get_backlog_status")
    blocked = next(i for i in result["blocked_items"] if i["id"] == "blocked-1")
    assert blocked["gate_or_deps"] == "waiting on: locked-1"


def test_get_backlog_status_tool_includes_live_metrics():
    with patch.object(server, "load_backlog", return_value=_FAKE_BACKLOG), \
         patch.object(server._checker, "compute_metrics", return_value=_FAKE_METRICS):
        result = _call("get_backlog_status")
    assert result["live_metrics"]["resolved_count"] == 22


# ─── get_pipeline_health ──────────────────────────────────────────────────────

def test_get_pipeline_health_tool_healthy_task_has_no_problem():
    healthy = [{"task": "Leviathan-DailyRun", "problem": None,
                "hours_since_run": 2.0, "last_result": 0}]
    with patch.object(server._ahc, "check_scheduled_tasks", return_value=healthy):
        result = _call("get_pipeline_health")
    assert result[0]["problem"] is None


def test_get_pipeline_health_tool_flags_stale_task():
    stale = [{"task": "Leviathan-ResolveFirst", "problem": "no run in 48.0h (max 26.0h)",
              "hours_since_run": 48.0, "last_result": 0}]
    with patch.object(server._ahc, "check_scheduled_tasks", return_value=stale):
        result = _call("get_pipeline_health")
    assert "no run in" in result[0]["problem"]
