"""
Offline tests for scanner.py.

No network calls, no DB access — scanner is pure-function logic.
Run: python -m pytest -q
"""

import pytest
from datetime import datetime, timezone, timedelta

import scanner


# ─── Config and helpers ───────────────────────────────────────────────────────

BASE_CFG = {
    "markets": {
        "min_volume":        100,
        "max_volume_filter": 150_000,
        "min_market_price":  0.05,
        "max_market_price":  0.95,
        "min_days_to_close": 0,
        "max_days_to_close": 180,
        "edge_threshold":    0.08,
        "bucket_min_volume": {
            "INTRADAY":  50,
            "WEEKLY":    150,
            "MONTHLY":   100,
            "QUARTERLY": 50,
            "LONG":      50,
        },
        "efficient_market_keywords": ["CPI", "Federal Reserve"],
        "categories": [],
    }
}


def _close(days_out: float) -> str:
    """ISO-8601 close_time N days from now (UTC)."""
    return (datetime.now(timezone.utc) + timedelta(days=days_out)).isoformat()


def _market(mid=0.50, volume=500, days_out=30, title="Some random event", **kwargs):
    """
    Build a minimal market dict. yes_bid/yes_ask bracket mid with a ±1¢ spread
    so (yes_bid + yes_ask) / 2 == mid exactly.
    """
    return {
        "ticker":     "TEST",
        "title":      title,
        "yes_bid":    round(mid - 0.01, 4),
        "yes_ask":    round(mid + 0.01, 4),
        "volume":     volume,
        "close_time": _close(days_out),
        **kwargs,
    }


# ─── filter_markets: price bounds ────────────────────────────────────────────

def test_price_below_min_dropped():
    assert scanner.filter_markets([_market(mid=0.03)], BASE_CFG) == []

def test_price_above_max_dropped():
    assert scanner.filter_markets([_market(mid=0.97)], BASE_CFG) == []

def test_price_at_min_boundary_kept():
    assert len(scanner.filter_markets([_market(mid=0.05)], BASE_CFG)) == 1

def test_price_at_max_boundary_kept():
    assert len(scanner.filter_markets([_market(mid=0.95)], BASE_CFG)) == 1

def test_price_midrange_kept():
    assert len(scanner.filter_markets([_market(mid=0.50)], BASE_CFG)) == 1


# ─── filter_markets: volume floors ───────────────────────────────────────────

def test_volume_below_bucket_min_dropped():
    # days_out=30 → MONTHLY bucket, min=100; volume=10 < 100
    assert scanner.filter_markets([_market(volume=10, days_out=30)], BASE_CFG) == []

def test_volume_above_bucket_min_kept():
    assert len(scanner.filter_markets([_market(volume=500, days_out=30)], BASE_CFG)) == 1

def test_volume_above_global_max_dropped():
    assert scanner.filter_markets([_market(volume=200_000)], BASE_CFG) == []

def test_volume_per_bucket_intraday_lower_floor():
    # INTRADAY floor = 50; volume=60 should pass
    assert len(scanner.filter_markets([_market(volume=60, days_out=0.5)], BASE_CFG)) == 1

def test_volume_per_bucket_weekly_floor():
    # WEEKLY floor = 150; volume=100 < 150 → dropped
    assert scanner.filter_markets([_market(volume=100, days_out=3)], BASE_CFG) == []


# ─── filter_markets: close-time bounds ───────────────────────────────────────

def test_no_close_time_dropped():
    m = _market()
    del m["close_time"]
    assert scanner.filter_markets([m], BASE_CFG) == []

def test_expiration_time_field_accepted():
    """scanner accepts expiration_time as a fallback for close_time."""
    m = _market()
    m["expiration_time"] = m.pop("close_time")
    assert len(scanner.filter_markets([m], BASE_CFG)) == 1

def test_too_far_out_dropped():
    assert scanner.filter_markets([_market(days_out=200)], BASE_CFG) == []

def test_within_window_kept():
    assert len(scanner.filter_markets([_market(days_out=60)], BASE_CFG)) == 1

def test_closes_today_kept():
    # min_days_to_close=0, so same-day close is valid if in INTRADAY bucket with enough vol
    assert len(scanner.filter_markets([_market(days_out=0.5, volume=60)], BASE_CFG)) == 1


# ─── filter_markets: efficient-market keyword gate ───────────────────────────

def test_efficient_market_keyword_dropped():
    m = _market(title="Will the CPI print above 3%?")
    assert scanner.filter_markets([m], BASE_CFG) == []

def test_no_keyword_match_kept():
    m = _market(title="Will the unemployment rate rise?")
    assert len(scanner.filter_markets([m], BASE_CFG)) == 1


# ─── classify_time_horizon ───────────────────────────────────────────────────

@pytest.mark.parametrize("days,expected", [
    (0.0,   "INTRADAY"),
    (0.5,   "INTRADAY"),
    (0.99,  "INTRADAY"),
    (1.0,   "WEEKLY"),
    (3.0,   "WEEKLY"),
    (6.99,  "WEEKLY"),
    (7.0,   "MONTHLY"),
    (15.0,  "MONTHLY"),
    (29.99, "MONTHLY"),
    (30.0,  "QUARTERLY"),
    (60.0,  "QUARTERLY"),
    (89.99, "QUARTERLY"),
    (90.0,  "LONG"),
    (180.0, "LONG"),
    (365.0, "LONG"),
])
def test_classify_time_horizon(days, expected):
    now   = datetime.now(timezone.utc)
    close = now + timedelta(days=days)
    assert scanner.classify_time_horizon(close, now) == expected


# ─── score_market: flag logic ────────────────────────────────────────────────

FUTURE_30D = _close(30)

def test_flag_true_when_no_base_rate_and_mid_exists():
    """
    Most markets have no heuristic base rate. flag fires whenever mid is set
    and base_rate is None — this is the dominant flag trigger in practice.
    """
    m = _market(mid=0.30, title="Some random event no keywords")
    result = scanner.score_market(m, BASE_CFG)

    assert result["base_rate"]  is None
    assert result["mid_price"]  == pytest.approx(0.30)
    assert result["flag"]       is True


def test_flag_true_when_edge_exceeds_threshold():
    """base_rate=0.40 (rain heuristic), mid=0.22 → edge=0.18 > 0.08."""
    m = _market(mid=0.22, title="Will it rain tomorrow")
    result = scanner.score_market(m, BASE_CFG)

    assert result["base_rate"] == pytest.approx(0.40)
    assert result["raw_edge"]  == pytest.approx(0.18)
    assert result["flag"]      is True


def test_flag_false_when_base_rate_matches_mid_and_no_drift():
    """
    base_rate=0.40, mid=0.40 → edge=0.00 < 0.08.
    No drift (no last_price_dollars). Flag must be False.
    """
    m = _market(mid=0.40, title="Will it rain tomorrow")
    result = scanner.score_market(m, BASE_CFG)

    assert result["base_rate"] == pytest.approx(0.40)
    assert result["raw_edge"]  == pytest.approx(0.00)
    assert result["flag"]      is False


def test_flag_true_from_drift_even_when_edge_small():
    """Drift > 5% triggers flag independently of edge."""
    m = _market(mid=0.40, title="Will it rain tomorrow")
    m["last_price_dollars"] = 0.30   # drift = (0.40-0.30)/0.30 ≈ 33%
    result = scanner.score_market(m, BASE_CFG)

    assert result["drift_flag"] is True
    assert result["flag"]       is True


def test_flag_false_when_no_mid_price():
    """Market with no bid/ask → mid_price=None → flag must not fire blindly."""
    m = {
        "ticker": "X", "title": "Will it rain tomorrow",
        "yes_bid": 0, "yes_ask": 0,
        "close_time": FUTURE_30D, "volume": 500,
        "time_horizon": "MONTHLY",
    }
    result = scanner.score_market(m, BASE_CFG)

    assert result["mid_price"] is None
    assert result["flag"]      is False


# ─── score_market: spread signal ─────────────────────────────────────────────

def test_wide_spread_flagged():
    m = _market()
    m["yes_bid"] = 0.30
    m["yes_ask"] = 0.50   # spread = 0.20, mid ≈ 0.40, spread_pct ≈ 50% > 5%
    result = scanner.score_market(m, BASE_CFG)
    assert result["spread_wide"] is True

def test_narrow_spread_not_flagged():
    m = _market(mid=0.50)   # ±0.01 spread → spread_pct = 4% < 5%
    result = scanner.score_market(m, BASE_CFG)
    assert result["spread_wide"] is False


# ─── score_markets: sort order ───────────────────────────────────────────────

def test_score_markets_flagged_first():
    """score_markets must sort flagged markets before unflagged ones."""
    # "Will it rain" triggers base_rate=0.40; mid=0.40 → edge=0, flag=False
    unflagged = _market(mid=0.40, title="Will it rain tomorrow")
    # "Some random event" → base_rate=None → flag=True
    flagged   = _market(mid=0.30, title="Some random event")

    results = scanner.score_markets([unflagged, flagged], BASE_CFG)
    assert results[0]["flag"] is True
    assert results[1]["flag"] is False
