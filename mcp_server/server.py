"""
mcp_server/server.py — Leviathan MCP server (v1: stdio transport, tools only).

Exposes the signal log, resolved track record, and market-data lookup as
MCP tools so the resolved record can be interrogated conversationally
instead of by opening files or writing one-off queries. Reads
data/leviathan.db via core.logger — the same database the daily
pipeline writes, not a copy or snapshot.

v1 scope: stdio transport, tools only. No StreamableHTTP, sampling,
roots, or resources/prompts — those belong to a later, hardened build.

Run:
    mcp dev mcp_server/server.py       # Inspector, for interactive testing
    claude mcp add leviathan -- python mcp_server/server.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mcp.server.fastmcp import FastMCP

from core import logger
from backlog.engine import load_backlog
from backlog import checker as _checker
import scripts.automation_health_check as _ahc

mcp = FastMCP("leviathan")

BACKLOG_PATH = os.path.join(ROOT, "backlog", "backlog.json")


@mcp.tool()
def get_signal_log(limit: int = 20, resolved_only: bool = False,
                    ticker: str | None = None) -> list[dict]:
    """
    Query Leviathan's live signal log.

    Returns the most recent scored paper signals (PASS rows excluded),
    newest first. Reads the same database the daily pipeline writes.

    Args:
        limit: max rows to return (default 20).
        resolved_only: only return signals with a settled outcome.
        ticker: exact ticker to filter to, e.g. "KXIMPEACHCABINET-26JUL01".
    """
    return logger.get_signal_log(limit=limit, resolved_only=resolved_only, ticker=ticker)


@mcp.tool()
def get_resolved_track_record() -> list[dict]:
    """
    Return Leviathan's full resolved track record: every settled paper
    signal with its probability estimate (score) and actual outcome
    (WIN/LOSS, and whether the market resolved YES/NO). Same filter used
    for the headline win-rate/Brier stats reported in the README.
    """
    return logger.get_resolved_track_record()


@mcp.tool()
def lookup_market(ticker: str | None = None, date: str | None = None) -> list[dict]:
    """
    Look up scored market data for a given ticker or signal date.

    At least one of ticker/date must be supplied; passing neither returns
    no rows.

    Args:
        ticker: partial or full Kalshi ticker, e.g. "CABLEAVE" or
            "KXCABLEAVE-26MAY22-26JUL".
        date: signal date in YYYY-MM-DD form, e.g. "2026-07-14".
    """
    return logger.get_market_data(ticker=ticker, date=date)


@mcp.tool()
def get_run_history(limit: int = 20) -> list[dict]:
    """
    Most recent pipeline runs, newest first -- one row per main.py
    invocation with markets_scanned, signals_generated, whale_flags,
    runtime_ms, model_used, and Brier stats. Use this to compare a
    config trial (e.g. a stronger-model trial via cli_model_override)
    against its preceding baseline window.

    Args:
        limit: max rows to return (default 20).
    """
    return logger.get_run_history(limit=limit)


@mcp.tool()
def get_category_breakdown() -> list[dict]:
    """
    Signal counts grouped by (category, flag_path), most common first.
    Categories come directly from Kalshi's own event-level field -- no
    synthetic taxonomy. A large blank-category bucket on one flag_path
    but not others usually means a real capture-path bug, not a data
    gap on Kalshi's side.
    """
    return logger.get_category_breakdown()


@mcp.tool()
def get_backlog_status() -> dict:
    """
    Full backlog snapshot: counts by status, live gate-unlock metrics
    (the same ones every real gate-unlock decision in this project
    reads), and per-item detail for every ready/locked/blocked item.
    Locked items show real-time progress toward their trigger; blocked
    items show what they're waiting on. Never a stale, separately
    computed number -- reads backlog.json and leviathan.db live.
    """
    backlog = load_backlog(BACKLOG_PATH)
    items = backlog.get("items", [])
    metrics = _checker.compute_metrics()

    counts: dict[str, int] = {}
    for item in items:
        counts[item.get("status", "")] = counts.get(item.get("status", ""), 0) + 1
    counts["total"] = len(items)

    def _gate_or_deps(item: dict) -> str:
        if item["status"] == "blocked" and item.get("depends_on"):
            return f"waiting on: {', '.join(item['depends_on'])}"
        if item["status"] == "locked":
            return _checker.gate_progress_str(item, metrics)
        if item["status"] == "blocked":
            return "manual/policy hold -- see notes"
        return ""

    def _brief(item: dict) -> dict:
        return {
            "id": item["id"],
            "title": item.get("title", ""),
            "priority": item.get("priority"),
            "area": item.get("area", ""),
            "summary": _checker._summarize_action(item.get("action", ""), max_len=160),
            "gate_or_deps": _gate_or_deps(item),
        }

    by_status = {"ready": [], "locked": [], "blocked": []}
    for item in items:
        status = item.get("status")
        if status in by_status:
            by_status[status].append(_brief(item))

    return {
        "counts": counts,
        "live_metrics": metrics,
        "ready_items": by_status["ready"],
        "locked_items": by_status["locked"],
        "blocked_items": by_status["blocked"],
    }


@mcp.tool()
def get_pipeline_health() -> list[dict]:
    """
    Live Task Scheduler health for every daily/weekly Leviathan task
    (the same check Leviathan-AutomationHealthCheck runs). Each entry
    has task/problem/hours_since_run/last_result -- problem is None
    when a task is healthy, otherwise a plain-English description of
    what's stale or missing.
    """
    return _ahc.check_scheduled_tasks()


if __name__ == "__main__":
    mcp.run()
