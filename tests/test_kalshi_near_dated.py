"""
tests/test_kalshi_near_dated.py — Offline tests for
core.kalshi.fetch_event_detail / fetch_near_dated_markets.

Background: fetch_events() (the events-catalog path main.py's primary
market fetch and analysis/snapshot_markets.py both use) structurally never
surfaces near-dated markets -- the /events object carries no close_time
field, and max_close_ts has no effect on it (confirmed empirically against
the live API: it returns century-scale novelty markets like Mars
colonization regardless). fetch_near_dated_markets() instead queries
/markets?max_close_ts=..., which does genuinely filter by close time, then
excludes the KXMVE multi-event parlay flood that otherwise dominates that
endpoint (~98% of raw volume in live testing).

core.kalshi._sdk_json is mocked throughout — no network calls. (Migrated
from mocking core.kalshi.requests.get by kalshi-sdk-migration-implementation,
which replaced the hand-rolled requests-based HTTP layer with kalshi-sdk's
transport; _sdk_json is the one function per call that actually reaches the
network, and it returns the raw JSON dict directly rather than a
requests.Response, so _resp() below is now an identity helper kept only to
minimize the diff against the pre-migration version of this file.)
"""

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import kalshi


def _resp(json_data):
    return json_data


def _market(ticker, event_ticker="EVT1"):
    return {"ticker": ticker, "event_ticker": event_ticker, "close_time": "2026-08-10T00:00:00Z"}


# ─── fetch_event_detail ────────────────────────────────────────────────────

def test_fetch_event_detail_returns_event_dict():
    with patch("core.kalshi._sdk_json", return_value=_resp({"event": {"series_ticker": "KXFOO", "category": "Sports"}})) as mock_get:
        event = kalshi.fetch_event_detail({"environment": "demo"}, "KXFOO-26AUG10")
    assert event == {"series_ticker": "KXFOO", "category": "Sports"}
    args, kwargs = mock_get.call_args
    assert "/events/KXFOO-26AUG10" in args[1]


# ─── fetch_near_dated_markets ──────────────────────────────────────────────

def test_uses_max_close_ts_param_derived_from_max_days():
    with patch("core.kalshi._sdk_json", return_value=_resp({"markets": []})) as mock_get:
        kalshi.fetch_near_dated_markets({"environment": "demo"}, max_days=14)
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["status"] == "open"
    assert "max_close_ts" in kwargs["params"]


def test_excludes_kxmve_flood_by_default():
    page = _resp({
        "markets": [
            _market("KXMVESPORTSMULTIGAMEEXTENDED-ABC", "EVT1"),
            _market("KXWTACHALLENGERMATCH-DEF", "EVT2"),
        ],
        "cursor": None,
    })
    with patch("core.kalshi._sdk_json", return_value=page), \
         patch("core.kalshi.fetch_event_detail", return_value={}):
        markets = kalshi.fetch_near_dated_markets({"environment": "demo"})
    tickers = [m["ticker"] for m in markets]
    assert "KXWTACHALLENGERMATCH-DEF" in tickers
    assert not any(t.startswith("KXMVE") for t in tickers)


def test_custom_exclude_prefixes_from_config():
    page = _resp({
        "markets": [_market("KXCUSTOMFLOOD-ABC", "EVT1"), _market("KXREAL-DEF", "EVT2")],
        "cursor": None,
    })
    config = {"environment": "demo", "markets": {"near_dated_exclude_prefixes": ["KXCUSTOMFLOOD"]}}
    with patch("core.kalshi._sdk_json", return_value=page), \
         patch("core.kalshi.fetch_event_detail", return_value={}):
        markets = kalshi.fetch_near_dated_markets(config)
    tickers = [m["ticker"] for m in markets]
    assert tickers == ["KXREAL-DEF"]


def test_paginates_via_cursor():
    """
    max_days=1 pins this to a single day-chunk (near-dated-window-chunking,
    2026-08-02) so it exercises cursor-following within one chunk, matching
    the 2-item side_effect list -- without pinning it, the function would
    move on to querying day-chunk 2 once day-chunk 1's cursor exhausts,
    and the mock has no more responses queued for that call.
    """
    page1 = _resp({"markets": [_market("A", "E1")], "cursor": "next"})
    page2 = _resp({"markets": [_market("B", "E2")], "cursor": None})
    with patch("core.kalshi._sdk_json", side_effect=[page1, page2]) as mock_get, \
         patch("core.kalshi.fetch_event_detail", return_value={}):
        markets = kalshi.fetch_near_dated_markets({"environment": "demo"}, max_days=1, target_count=200, max_pages=30)
    assert mock_get.call_count == 2
    assert [m["ticker"] for m in markets] == ["A", "B"]


def test_stops_at_target_count():
    page = _resp({"markets": [_market(f"T{i}", "E1") for i in range(50)], "cursor": "more"})
    with patch("core.kalshi._sdk_json", return_value=page), \
         patch("core.kalshi.fetch_event_detail", return_value={}):
        markets = kalshi.fetch_near_dated_markets({"environment": "demo"}, target_count=10, max_pages=30)
    assert len(markets) == 10


def test_stops_at_max_pages_safety_cap():
    """Even if the flood ratio is bad enough that target_count is never
    reached, this must not loop forever -- max_pages is a hard cap."""
    page = _resp({"markets": [_market("KXMVEFLOOD-X", "E1")], "cursor": "more"})
    with patch("core.kalshi._sdk_json", return_value=page) as mock_get, \
         patch("core.kalshi.fetch_event_detail", return_value={}):
        kalshi.fetch_near_dated_markets({"environment": "demo"}, target_count=1000, max_pages=5)
    assert mock_get.call_count == 5


def test_flood_saturated_early_day_does_not_starve_later_days():
    """
    backlog: near-dated-window-chunking. The whole point of chunking by
    day: a day-chunk that's 100% flood (real Kalshi behavior confirmed at
    the 10-14 day horizon, 2026-08-02 -- ~99.5% flood, not exhausted even
    at 200 pages) must not consume the shared page budget and prevent a
    later, flood-free day from being reached. Day 0 here returns nothing
    but flood for its whole per-chunk page budget; day 1 has one real
    market on its first page.
    """
    flood_page = _resp({"markets": [_market("KXMVEFLOOD-X", "E1")], "cursor": "more"})
    real_page = _resp({"markets": [_market("REAL-A", "E2")], "cursor": None})
    # CHUNK_MAX_PAGES is 5 -- day 0 exhausts its 5-page budget on pure
    # flood, then day 1's first page has the real market.
    responses = [flood_page] * 5 + [real_page]
    with patch("core.kalshi._sdk_json", side_effect=responses) as mock_get, \
         patch("core.kalshi.fetch_event_detail", return_value={}):
        markets = kalshi.fetch_near_dated_markets(
            {"environment": "demo"}, max_days=2, target_count=200, max_pages=30,
        )
    assert mock_get.call_count == 6
    assert [m["ticker"] for m in markets] == ["REAL-A"]


def test_chunk_windows_use_min_and_max_close_ts_per_day():
    """
    backlog: near-dated-window-chunking. Each day-chunk must bound both
    ends of its window (min_close_ts AND max_close_ts) -- a bare
    max_close_ts query is exactly the flat-query behavior this change
    replaced, which let an unbounded flood run ahead of the page cap.
    """
    page = _resp({"markets": [], "cursor": None})
    with patch("core.kalshi._sdk_json", return_value=page) as mock_get, \
         patch("core.kalshi.fetch_event_detail", return_value={}):
        kalshi.fetch_near_dated_markets({"environment": "demo"}, max_days=2)
    assert mock_get.call_count == 2
    first_params = mock_get.call_args_list[0].kwargs["params"]
    second_params = mock_get.call_args_list[1].kwargs["params"]
    assert "min_close_ts" in first_params and "max_close_ts" in first_params
    assert first_params["min_close_ts"] < first_params["max_close_ts"]
    # Day-chunk 2's window starts where day-chunk 1's window ended.
    assert second_params["min_close_ts"] == first_params["max_close_ts"]


def test_stops_when_page_empty():
    """
    Under near-dated-window-chunking (2026-08-02), an empty page ends that
    day-chunk immediately (1 call/day here, since cursor is None) but is
    NOT sufficient evidence to stop the whole scan -- live sampling showed
    the flood/real ratio varies wildly from day to day, so day N being
    empty says nothing about day N+1. With default max_days=14 and every
    day empty, that's 14 total calls, one per day-chunk.
    """
    page = _resp({"markets": [], "cursor": None})
    with patch("core.kalshi._sdk_json", return_value=page) as mock_get, \
         patch("core.kalshi.fetch_event_detail", return_value={}):
        markets = kalshi.fetch_near_dated_markets({"environment": "demo"})
    assert mock_get.call_count == 14
    assert markets == []


def test_backfills_series_ticker_and_category_one_call_per_unique_event():
    """Three markets share two event_tickers -- fetch_event_detail must be
    called once per UNIQUE event, not once per market."""
    page = _resp({
        "markets": [
            _market("A", "EVT-SHARED"),
            _market("B", "EVT-SHARED"),
            _market("C", "EVT-OTHER"),
        ],
        "cursor": None,
    })
    def fake_detail(config, event_ticker):
        return {"EVT-SHARED": {"series_ticker": "SER1", "category": "Sports"},
                "EVT-OTHER":  {"series_ticker": "SER2", "category": "Politics"}}[event_ticker]

    with patch("core.kalshi._sdk_json", return_value=page), \
         patch("core.kalshi.fetch_event_detail", side_effect=fake_detail) as mock_detail:
        markets = kalshi.fetch_near_dated_markets({"environment": "demo"})

    assert mock_detail.call_count == 2
    by_ticker = {m["ticker"]: m for m in markets}
    assert by_ticker["A"]["series_ticker"] == "SER1"
    assert by_ticker["A"]["category"] == "Sports"
    assert by_ticker["B"]["series_ticker"] == "SER1"
    assert by_ticker["C"]["series_ticker"] == "SER2"
    assert by_ticker["C"]["category"] == "Politics"


def test_event_detail_failure_defaults_to_empty_not_raise():
    page = _resp({"markets": [_market("A", "EVT-BROKEN")], "cursor": None})
    with patch("core.kalshi._sdk_json", return_value=page), \
         patch("core.kalshi.fetch_event_detail", side_effect=Exception("network error")):
        markets = kalshi.fetch_near_dated_markets({"environment": "demo"})
    assert markets[0]["series_ticker"] == ""
    assert markets[0]["category"] == ""
