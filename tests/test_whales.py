"""
Offline tests for whales.py — no network, no subprocess calls.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from core import whales


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _cfg(size_multiplier=5, spike_hours=1, min_whale_size=100):
    return {
        "whales": {
            "size_multiplier":  size_multiplier,
            "volume_spike_hours": spike_hours,
            "min_whale_size":   min_whale_size,
        }
    }


def _trade(size, side="yes", minutes_ago=30, block=False):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {
        "count_fp":       str(float(size)),
        "taker_side":     side,
        "created_time":   ts,
        "is_block_trade": block,
    }


# ─── _size field normalisation ────────────────────────────────────────────────

def test_size_reads_count_fp():
    """_size() must read count_fp (the real Kalshi field)."""
    t = {"count_fp": "250.0", "count": "0", "size": "0"}
    assert whales._size(t) == 250.0


def test_size_falls_back_to_count():
    t = {"count": "75"}
    assert whales._size(t) == 75.0


def test_size_falls_back_to_size():
    t = {"size": "50"}
    assert whales._size(t) == 50.0


def test_size_returns_zero_when_all_missing():
    assert whales._size({}) == 0.0


# ─── detect_whale_activity ────────────────────────────────────────────────────

def test_no_trades_returns_not_detected():
    result = whales.detect_whale_activity("TEST", [], _cfg())
    assert result["whale_detected"] is False
    assert result["avg_trade_size"] == 0
    assert result["max_trade_size"] == 0


def test_large_trade_triggers_detection():
    """A single trade much larger than avg triggers whale_detected."""
    trades = [_trade(10)] * 9 + [_trade(600)]  # avg=69, threshold=max(345, 100)
    result = whales.detect_whale_activity("TEST", trades, _cfg(size_multiplier=5, min_whale_size=100))
    assert result["whale_detected"] is True
    assert len(result["large_trades"]) == 1
    assert result["max_trade_size"] == 600.0


def test_small_trades_only_not_detected():
    """All trades below threshold → not detected."""
    trades = [_trade(20)] * 10  # avg=20, threshold=max(100, 100)=100; all below
    result = whales.detect_whale_activity("TEST", trades, _cfg(min_whale_size=100))
    assert result["whale_detected"] is False
    assert result["large_trades"] == []


def test_min_whale_size_absolute_floor():
    """Even if relative threshold is low, min_whale_size acts as floor."""
    # avg=10, 5x=50 — but min_whale_size=100 means threshold=100
    trades = [_trade(10)] * 9 + [_trade(60)]  # 60 < 100 → not flagged
    result = whales.detect_whale_activity("TEST", trades, _cfg(size_multiplier=5, min_whale_size=100))
    assert result["whale_detected"] is False


def test_block_trade_triggers_detection():
    """is_block_trade=True must trigger detection even if size is small."""
    trades = [_trade(5, block=True)] + [_trade(5)] * 9
    result = whales.detect_whale_activity("TEST", trades, _cfg())
    assert result["whale_detected"] is True
    assert len(result["block_trades"]) == 1


def test_whale_direction_yes():
    """Large YES trades → whale_direction=YES."""
    trades = [_trade(10)] * 9 + [_trade(600, side="yes")]
    result = whales.detect_whale_activity("TEST", trades, _cfg(min_whale_size=100))
    assert result["whale_direction"] == "YES"


def test_whale_direction_no():
    """Large NO trades → whale_direction=NO."""
    trades = [_trade(10)] * 9 + [_trade(600, side="no")]
    result = whales.detect_whale_activity("TEST", trades, _cfg(min_whale_size=100))
    assert result["whale_direction"] == "NO"


def test_whale_direction_none_when_no_large_trades():
    trades = [_trade(5)] * 5
    result = whales.detect_whale_activity("TEST", trades, _cfg(min_whale_size=100))
    assert result["whale_direction"] is None


def test_volume_spike_detected():
    """Recent burst of volume vs quiet prior period triggers spike."""
    # 10 trades in last 30 min (recent), 1 trade 12h ago (prior)
    recent = [_trade(50, minutes_ago=10)] * 10
    prior  = [_trade(50, minutes_ago=720)]  # 12h ago
    result = whales.detect_whale_activity("TEST", recent + prior, _cfg(spike_hours=1, size_multiplier=3))
    assert result["volume_spike"] is True


def test_avg_and_max_computed_correctly():
    trades = [_trade(100), _trade(200), _trade(300)]
    result = whales.detect_whale_activity("TEST", trades, _cfg(min_whale_size=0))
    assert result["avg_trade_size"] == pytest.approx(200.0)
    assert result["max_trade_size"] == pytest.approx(300.0)


# ─── scan_all_markets ─────────────────────────────────────────────────────────

def test_scan_all_markets_returns_only_flagged():
    trades_by_ticker = {
        "WHALE": [_trade(10)] * 9 + [_trade(600)],
        "QUIET": [_trade(5)] * 10,
    }
    results = whales.scan_all_markets(["WHALE", "QUIET"], trades_by_ticker, _cfg(min_whale_size=100))
    tickers = [r["ticker"] for r in results]
    assert "WHALE" in tickers
    assert "QUIET" not in tickers


def test_scan_all_markets_empty_input():
    results = whales.scan_all_markets([], {}, _cfg())
    assert results == []


# ─── scan_recent_trades ───────────────────────────────────────────────────────

def test_scan_recent_trades_groups_by_ticker():
    """Trades for the same ticker must be grouped together for detection."""
    trades = (
        [{"ticker": "AAA", **_trade(600), "ticker": "AAA"}] +
        [{"ticker": "AAA", **_trade(10)}  for _ in range(9)] +
        [{"ticker": "BBB", **_trade(5)}   for _ in range(10)]
    )
    # Re-build properly (dict comprehension clobbers key)
    trades = (
        [dict(_trade(600), ticker="AAA")] +
        [dict(_trade(10),  ticker="AAA") for _ in range(9)] +
        [dict(_trade(5),   ticker="BBB") for _ in range(10)]
    )
    results = whales.scan_recent_trades(trades, _cfg(min_whale_size=100))
    tickers = [r["ticker"] for r in results]
    assert "AAA" in tickers
    assert "BBB" not in tickers


def test_scan_recent_trades_sorted_by_max_size():
    """Results must be sorted by max_trade_size descending."""
    trades = (
        [dict(_trade(1000), ticker="BIG")] +
        [dict(_trade(10),   ticker="BIG") for _ in range(9)] +
        [dict(_trade(500),  ticker="MED")] +
        [dict(_trade(10),   ticker="MED") for _ in range(9)]
    )
    results = whales.scan_recent_trades(trades, _cfg(min_whale_size=100))
    assert results[0]["ticker"] == "BIG"
    assert results[1]["ticker"] == "MED"


def test_scan_recent_trades_empty():
    assert whales.scan_recent_trades([], _cfg()) == []


def test_scan_recent_trades_skips_missing_ticker():
    trades = [_trade(1000)]  # no "ticker" key
    results = whales.scan_recent_trades(trades, _cfg(min_whale_size=100))
    assert results == []
