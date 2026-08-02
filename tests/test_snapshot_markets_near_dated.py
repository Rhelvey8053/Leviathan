"""
tests/test_snapshot_markets_near_dated.py — Offline tests for
analysis.snapshot_markets.fetch_snapshot's near-dated supplement.

Covers only the new merge/dedup/failure-handling behavior added around the
pre-existing events-catalog fetch (untouched) -- kalshi.fetch_near_dated_markets
itself is tested in tests/test_kalshi_near_dated.py.

core.kalshi.fetch_events / fetch_event_markets / fetch_near_dated_markets
are all mocked -- no network calls.
"""

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis import snapshot_markets

_CFG = {"environment": "demo", "markets": {}}


def test_near_dated_markets_merged_into_result():
    with patch("core.kalshi.fetch_events", return_value=[]), \
         patch("core.kalshi.fetch_near_dated_markets", return_value=[{"ticker": "KXNEAR-1"}]):
        markets, event_count = snapshot_markets.fetch_snapshot(_CFG)
    assert [m["ticker"] for m in markets] == ["KXNEAR-1"]
    assert event_count == 0


def test_near_dated_dedup_against_events_catalog_markets():
    """A ticker already picked up via the events-catalog loop must not be
    duplicated by the near-dated supplement."""
    event = {"event_ticker": "EVT1", "category": ""}
    with patch("core.kalshi.fetch_events", return_value=[event]), \
         patch("core.kalshi.fetch_event_markets", return_value=[{"ticker": "SHARED"}]), \
         patch("core.kalshi.fetch_near_dated_markets", return_value=[{"ticker": "SHARED"}, {"ticker": "NEW"}]):
        markets, _ = snapshot_markets.fetch_snapshot(_CFG)
    tickers = [m["ticker"] for m in markets]
    assert tickers.count("SHARED") == 1
    assert "NEW" in tickers


def test_near_dated_failure_does_not_break_events_catalog_result():
    event = {"event_ticker": "EVT1", "category": ""}
    with patch("core.kalshi.fetch_events", return_value=[event]), \
         patch("core.kalshi.fetch_event_markets", return_value=[{"ticker": "FROM_EVENTS"}]), \
         patch("core.kalshi.fetch_near_dated_markets", side_effect=Exception("network error")):
        markets, event_count = snapshot_markets.fetch_snapshot(_CFG)
    assert [m["ticker"] for m in markets] == ["FROM_EVENTS"]
    assert event_count == 1
