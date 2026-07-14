"""
tests/test_mcp_server.py — Tests for mcp_server/server.py.

Uses a throwaway SQLite DB (never leviathan.db) — same tmp_db pattern as
test_logger.py. Tools are called through mcp.call_tool() end-to-end so
the MCP schema/dispatch layer is exercised, not just the underlying
core.logger query functions (which have their own dedicated tests).
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

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
    """Call an MCP tool end-to-end; return the structured result list/dict."""
    _content, structured = asyncio.run(server.mcp.call_tool(tool_name, kwargs))
    return structured.get("result", structured)


# ─── server scaffold ──────────────────────────────────────────────────────────

def test_server_has_three_tools():
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"get_signal_log", "get_resolved_track_record", "lookup_market"}


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
