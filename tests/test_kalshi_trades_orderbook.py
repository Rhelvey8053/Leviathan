"""
tests/test_kalshi_trades_orderbook.py — Offline tests for
core.kalshi.fetch_trades / fetch_orderbook.

core.kalshi._sdk_json is mocked throughout — no network calls. (Migrated
from mocking core.kalshi.requests.get by kalshi-sdk-migration-implementation
— see test_kalshi_near_dated.py's module docstring for why.) Added
alongside a fix for both functions: fetch_trades() called a nonexistent
/markets/{ticker}/trades endpoint (real one is /markets/trades?ticker=...),
and fetch_orderbook() returned the raw response unchanged when consumers
expected an "orderbook" envelope key that never exists on Kalshi's real
API (the real key is "orderbook_fp") — both previously failed silently
(404 swallowed / defaulted-to-zero-depth) with zero test coverage.
"""

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import kalshi


def _resp(json_data):
    return json_data


# ─── fetch_trades ─────────────────────────────────────────────────────────

def test_fetch_trades_uses_global_trades_path_with_ticker_filter():
    with patch("core.kalshi._sdk_json", return_value=_resp({"trades": []})) as mock_get:
        kalshi.fetch_trades({"environment": "demo"}, "KXFOO-26JUN01")
    args, kwargs = mock_get.call_args
    assert args[1].endswith("/markets/trades")
    assert "/markets/KXFOO-26JUN01/trades" not in args[1]
    assert kwargs["params"]["ticker"] == "KXFOO-26JUN01"


def test_fetch_trades_returns_trade_list():
    trades = [{"ticker": "KXFOO-26JUN01", "count_fp": "2.00", "taker_side": "yes"}]
    with patch("core.kalshi._sdk_json", return_value=_resp({"trades": trades})):
        result = kalshi.fetch_trades({"environment": "demo"}, "KXFOO-26JUN01", limit=10)
    assert result == trades


def test_fetch_trades_paginates_via_cursor():
    page1 = _resp({"trades": [{"trade_id": "1"}], "cursor": "abc"})
    page2 = _resp({"trades": [{"trade_id": "2"}], "cursor": None})
    with patch("core.kalshi._sdk_json", side_effect=[page1, page2]) as mock_get:
        result = kalshi.fetch_trades({"environment": "demo"}, "KXFOO-26JUN01", limit=150)
    assert mock_get.call_count == 2
    assert [t["trade_id"] for t in result] == ["1", "2"]


def test_fetch_trades_stops_when_page_empty():
    empty = _resp({"trades": [], "cursor": None})
    with patch("core.kalshi._sdk_json", return_value=empty) as mock_get:
        result = kalshi.fetch_trades({"environment": "demo"}, "KXFOO-26JUN01")
    assert result == []
    mock_get.assert_called_once()


def test_fetch_trades_respects_limit_cap():
    page = _resp({"trades": [{"trade_id": str(i)} for i in range(100)], "cursor": "more"})
    with patch("core.kalshi._sdk_json", return_value=page):
        result = kalshi.fetch_trades({"environment": "demo"}, "KXFOO-26JUN01", limit=50)
    assert len(result) == 50


# ─── fetch_orderbook ────────────────────────────────────────────────────────

def test_fetch_orderbook_returns_response_unchanged():
    """
    Real Kalshi shape is {"orderbook_fp": {...}} -- fetch_orderbook must
    pass it through as-is (core.scanner.compute_orderbook_signal is the
    consumer that reads orderbook_fp specifically). A prior version
    looked for a nonexistent "orderbook" envelope key.
    """
    real_shape = {"orderbook_fp": {"yes_dollars": [["0.5000", "10.00"]], "no_dollars": []}}
    with patch("core.kalshi._sdk_json", return_value=_resp(real_shape)):
        result = kalshi.fetch_orderbook({"environment": "demo"}, "KXFOO-26JUN01")
    assert result == real_shape


def test_fetch_orderbook_uses_market_scoped_path():
    with patch("core.kalshi._sdk_json", return_value=_resp({"orderbook_fp": {}})) as mock_get:
        kalshi.fetch_orderbook({"environment": "demo"}, "KXFOO-26JUN01")
    args, _ = mock_get.call_args
    assert args[1].endswith("/markets/KXFOO-26JUN01/orderbook")
