"""
tests/test_market_shape_validation.py — Offline tests for
main._validate_market_shape() (unattended-ops: graceful degradation when
an upstream API changes shape).

Pure function, no network/DB. main.py's own orchestration has no test
harness (verified by running the pipeline directly instead), consistent
with the rest of this session's main.py changes -- this covers the one
piece of new logic that's actually a testable, standalone function.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import main


def _good_market(**overrides):
    m = {
        "ticker": "KXFOO-26JUN01", "close_time": "2026-06-01T00:00:00Z",
        "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.35", "title": "Will foo?",
    }
    m.update(overrides)
    return m


def test_empty_markets_is_full_anomaly():
    result = main._validate_market_shape([])
    assert result["checked"] == 0
    assert result["anomaly_rate"] == 1.0


def test_all_fields_present_zero_anomaly():
    markets = [_good_market() for _ in range(10)]
    result = main._validate_market_shape(markets)
    assert result["checked"] == 10
    assert result["anomaly_rate"] == 0.0


def test_one_missing_field_across_all_markets_full_anomaly():
    markets = []
    for i in range(10):
        m = _good_market(ticker=f"T{i}")
        del m["yes_bid_dollars"]
        markets.append(m)
    result = main._validate_market_shape(markets)
    assert result["anomaly_rate"] == 1.0
    assert result["missing_fields"]["yes_bid_dollars"] == 10
    assert result["missing_fields"]["ticker"] == 0


def test_partial_anomaly_rate_computed_correctly():
    good = [_good_market(ticker=f"G{i}") for i in range(7)]
    bad = []
    for i in range(3):
        m = _good_market(ticker=f"B{i}")
        del m["title"]
        bad.append(m)
    result = main._validate_market_shape(good + bad)
    assert result["checked"] == 10
    assert result["anomaly_rate"] == 0.3
    assert result["missing_fields"]["title"] == 3


def test_market_missing_multiple_fields_counted_once_in_anomaly_rate():
    """A market missing 2 fields is still just 1 anomalous market, not 2."""
    m = _good_market()
    del m["title"]
    del m["close_time"]
    result = main._validate_market_shape([m, _good_market()])
    assert result["checked"] == 2
    assert result["anomaly_rate"] == 0.5  # 1 of 2 markets anomalous


def test_checked_count_lets_caller_apply_a_min_sample_gate():
    """
    _validate_market_shape() itself has no min-sample floor -- main.py's
    own abort gate (config.markets.shape_anomaly_min_sample, default 20)
    is applied by the caller using this "checked" count, so a handful of
    anomalous markets from a genuinely thin fetch doesn't trip a false
    alarm. Verified here at the function-output level, since main.py's
    own orchestration has no test harness.
    """
    markets = []
    for i in range(5):
        m = _good_market(ticker=f"T{i}")
        del m["ticker"]
        markets.append(m)
    result = main._validate_market_shape(markets)
    assert result["anomaly_rate"] == 1.0
    assert result["checked"] == 5  # below the default min_sample of 20 -- caller's job to gate on this
