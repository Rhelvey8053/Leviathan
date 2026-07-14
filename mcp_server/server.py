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

mcp = FastMCP("leviathan")


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


if __name__ == "__main__":
    mcp.run()
