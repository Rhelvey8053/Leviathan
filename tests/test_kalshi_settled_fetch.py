"""
tests/test_kalshi_settled_fetch.py — Offline tests for
core.kalshi.fetch_settled_events / fetch_settled_event_markets
(replay-settled-fetcher).

requests.get is mocked throughout — no network calls.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import kalshi


def _resp(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


def test_fetch_settled_events_uses_status_settled_param():
    with patch("core.kalshi.requests.get", return_value=_resp({"events": []})) as mock_get:
        kalshi.fetch_settled_events({"environment": "demo"}, max_fetch=200)
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["status"] == "settled"


def test_fetch_settled_events_paginates_via_cursor():
    page1 = _resp({"events": [{"event_ticker": "E1"}], "cursor": "abc"})
    page2 = _resp({"events": [{"event_ticker": "E2"}], "cursor": None})
    with patch("core.kalshi.requests.get", side_effect=[page1, page2]) as mock_get:
        events = kalshi.fetch_settled_events({"environment": "demo"}, max_fetch=200)
    assert mock_get.call_count == 2
    assert [e["event_ticker"] for e in events] == ["E1", "E2"]


def test_fetch_settled_events_stops_at_max_fetch():
    page = _resp({"events": [{"event_ticker": f"E{i}"} for i in range(200)], "cursor": "more"})
    with patch("core.kalshi.requests.get", return_value=page):
        events = kalshi.fetch_settled_events({"environment": "demo"}, max_fetch=50)
    assert len(events) == 50


def test_fetch_settled_events_stops_when_page_empty():
    empty = _resp({"events": [], "cursor": None})
    with patch("core.kalshi.requests.get", return_value=empty) as mock_get:
        events = kalshi.fetch_settled_events({"environment": "demo"}, max_fetch=200)
    assert events == []
    mock_get.assert_called_once()


def test_fetch_settled_event_markets_uses_status_and_event_ticker_params():
    with patch("core.kalshi.requests.get", return_value=_resp({"markets": []})) as mock_get:
        kalshi.fetch_settled_event_markets({"environment": "demo"}, "KXFOO-26JUN01")
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["status"] == "settled"
    assert kwargs["params"]["event_ticker"] == "KXFOO-26JUN01"


def test_fetch_settled_event_markets_returns_market_list():
    markets = [{"ticker": "KXFOO-26JUN01-YES", "result": "yes"}]
    with patch("core.kalshi.requests.get", return_value=_resp({"markets": markets})):
        result = kalshi.fetch_settled_event_markets({"environment": "demo"}, "KXFOO-26JUN01")
    assert result == markets


def test_fetch_event_markets_unaffected_still_uses_open_status():
    """Regression guard: the existing open-only function must not have changed."""
    with patch("core.kalshi.requests.get", return_value=_resp({"markets": []})) as mock_get:
        kalshi.fetch_event_markets({"environment": "demo"}, "KXFOO-26JUN01")
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["status"] == "open"


# ─── fetch_market_candlesticks (replay-asof-reconstruction) ──────────────────

def test_fetch_market_candlesticks_hits_series_scoped_path():
    with patch("core.kalshi.requests.get", return_value=_resp({"candlesticks": []})) as mock_get:
        kalshi.fetch_market_candlesticks({"environment": "demo"}, "KXFOO", "KXFOO-26JUN01", 1000, 2000)
    args, kwargs = mock_get.call_args
    assert args[0].endswith("/series/KXFOO/markets/KXFOO-26JUN01/candlesticks")
    assert kwargs["params"]["start_ts"] == 1000
    assert kwargs["params"]["end_ts"] == 2000
    assert kwargs["params"]["period_interval"] == 1440


def test_fetch_market_candlesticks_default_period_interval_is_daily():
    with patch("core.kalshi.requests.get", return_value=_resp({"candlesticks": []})) as mock_get:
        kalshi.fetch_market_candlesticks({"environment": "demo"}, "KXFOO", "KXFOO-26JUN01", 1000, 2000)
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["period_interval"] == 1440


def test_fetch_market_candlesticks_returns_candlestick_list():
    candles = [{"end_period_ts": 1500, "price": {"close_dollars": "0.50"}}]
    with patch("core.kalshi.requests.get", return_value=_resp({"candlesticks": candles})):
        result = kalshi.fetch_market_candlesticks({"environment": "demo"}, "KXFOO", "KXFOO-26JUN01", 1000, 2000)
    assert result == candles


def test_fetch_market_candlesticks_404_returns_empty_list():
    resp = MagicMock()
    resp.status_code = 404
    with patch("core.kalshi.requests.get", return_value=resp):
        result = kalshi.fetch_market_candlesticks({"environment": "demo"}, "KXFOO", "KXFOO-26JUN01", 1000, 2000)
    assert result == []


def test_fetch_market_candlesticks_distinct_endpoint_from_broken_history():
    """
    The now-deleted fetch_market_history() called /markets/{ticker}/history,
    which does not exist on Kalshi's API (confirmed empirically — plain-text
    404 for every ticker tried, never JSON). fetch_market_candlesticks()
    must hit a completely different, series-scoped path, not a variant of
    the same dead one.
    """
    with patch("core.kalshi.requests.get", return_value=_resp({"candlesticks": []})) as mock_get:
        kalshi.fetch_market_candlesticks({"environment": "demo"}, "KXFOO", "KXFOO-26JUN01", 1000, 2000)
    args, _ = mock_get.call_args
    assert "/history" not in args[0]
    assert "/candlesticks" in args[0]
