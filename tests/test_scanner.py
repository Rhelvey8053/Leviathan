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


# ─── filter_markets: empty order book fallback ───────────────────────────────

def test_one_sided_ask_only_uses_last_price():
    """bid=0, ask=1.0 (stale settled leg) — mid should use last_price, not ask/2."""
    m = _market()
    m["yes_bid"] = 0.0
    m["yes_ask"] = 1.0
    m["last_price_dollars"] = "0.0150"  # 1.5% — below 5% floor
    # Old code would compute mid = 1.0/2 = 0.50, keeping the market in
    # New code: mid = last_price = 0.015 → dropped
    assert scanner.filter_markets([m], BASE_CFG) == []

def test_one_sided_ask_only_valid_last_price_kept():
    """bid=0, ask=0.40, last=0.25 — mid should use last_price 0.25, which passes."""
    m = _market()
    m["yes_bid"] = 0.0
    m["yes_ask"] = 0.40
    m["last_price_dollars"] = "0.2500"
    result = scanner.filter_markets([m], BASE_CFG)
    assert len(result) == 1

def test_score_market_one_sided_uses_last_price():
    """score_market mid_price should be last_price when only ask is present."""
    m = _market()
    m["yes_bid"] = 0.0
    m["yes_ask"] = 1.0
    m["last_price_dollars"] = "0.0300"
    result = scanner.score_market(m, BASE_CFG)
    assert result["mid_price"] == pytest.approx(0.03, abs=1e-6)

def test_empty_orderbook_low_last_price_dropped():
    """Empty bid/ask + last_price_dollars below min → should be dropped."""
    m = _market()
    m["yes_bid"] = 0.0
    m["yes_ask"] = 0.0
    m["yes_bid_dollars"] = "0.0000"
    m["yes_ask_dollars"] = "0.0000"
    m["last_price_dollars"] = "0.0300"  # 3% — below 5% floor
    assert scanner.filter_markets([m], BASE_CFG) == []

def test_empty_orderbook_valid_last_price_kept():
    """Empty bid/ask + last_price_dollars above min → should pass."""
    m = _market()
    m["yes_bid"] = 0.0
    m["yes_ask"] = 0.0
    m["yes_bid_dollars"] = "0.0000"
    m["yes_ask_dollars"] = "0.0000"
    m["last_price_dollars"] = "0.2500"  # 25% — passes price filter
    assert len(scanner.filter_markets([m], BASE_CFG)) == 1

def test_empty_orderbook_no_last_price_kept():
    """Empty bid/ask AND no last_price_dollars → mid=None → price check skipped → kept."""
    m = _market()
    m["yes_bid"] = 0.0
    m["yes_ask"] = 0.0
    m["yes_bid_dollars"] = "0.0000"
    m["yes_ask_dollars"] = "0.0000"
    # no last_price_dollars key → mid stays None → no price check (unchanged behaviour)
    assert len(scanner.filter_markets([m], BASE_CFG)) == 1

def test_score_market_uses_last_price_when_no_bid_ask():
    """score_market mid_price falls back to last_price_dollars when bid/ask are 0."""
    m = _market()
    m["yes_bid"] = 0.0
    m["yes_ask"] = 0.0
    m["last_price_dollars"] = "0.3000"
    result = scanner.score_market(m, BASE_CFG)
    assert result["mid_price"] == pytest.approx(0.30, abs=1e-6)

def test_score_market_no_spurious_drift_from_empty_orderbook():
    """Empty order book should NOT generate a drift signal vs last_price_dollars."""
    m = _market()
    m["yes_bid"] = 0.0
    m["yes_ask"] = 0.0
    m["last_price_dollars"] = "0.2700"
    # mid_price == last_price_dollars → no drift
    result = scanner.score_market(m, BASE_CFG)
    assert result["drift_flag"] is False
    assert result["mid_price"] == pytest.approx(0.27, abs=1e-6)


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


# ─── Flag modes ───────────────────────────────────────────────────────────────
#
# Five constructed market archetypes, each scored under all three flag modes.
# Archetypes:
#   1. BR_NONE-no-anomaly  — no heuristic match, no drift, no edge
#   2. EDGE-only           — heuristic match with large edge, no drift
#   3. DRIFT-only          — heuristic match with zero edge, large drift
#   4. Totally-inert       — heuristic match with zero edge, no drift
#   5. BR_NONE-with-drift  — no heuristic match, large drift
#
# Expected flag outcomes per mode:
#
#   Archetype              passthrough  strict_anomaly_only  strict_with_heuristic
#   BR_NONE-no-anomaly     True(BR_NONE)  False                False
#   EDGE-only              True(EDGE)     False                True(HEURISTIC)
#   DRIFT-only             True(DRIFT)    True(DRIFT)          True(DRIFT)
#   Totally-inert          False          False                False
#   BR_NONE-with-drift     True(BR_NONE)  True(DRIFT)          True(DRIFT)

def _cfg(flag_mode: str) -> dict:
    """Return BASE_CFG with the given flag_mode injected."""
    import copy
    c = copy.deepcopy(BASE_CFG)
    c["markets"]["flag_mode"] = flag_mode
    return c


# ── Archetype builders ────────────────────────────────────────────────────────

def _br_none_no_anomaly():
    """No heuristic, no drift. Pure BR_NONE candidate."""
    return _market(mid=0.50, title="Some random event with no keywords")

def _edge_only():
    """base_rate=0.40 (rain), mid=0.22 → raw_edge=0.18>0.08, no drift."""
    return _market(mid=0.22, title="Will it rain tomorrow")

def _drift_only():
    """base_rate=0.40 (rain), mid=0.40 → edge=0. drift=33% via last_price."""
    m = _market(mid=0.40, title="Will it rain tomorrow")
    m["last_price_dollars"] = 0.30
    return m

def _totally_inert():
    """base_rate=0.40 (rain), mid=0.40 → edge=0, no drift. Nothing fires."""
    return _market(mid=0.40, title="Will it rain tomorrow")

def _br_none_with_drift():
    """No heuristic, large drift. BR_NONE path in passthrough, DRIFT in strict."""
    m = _market(mid=0.50, title="Some random event with no keywords")
    m["last_price_dollars"] = 0.40   # drift ≈ 25%
    return m


# ── Mode: passthrough ─────────────────────────────────────────────────────────

def test_passthrough_br_none_no_anomaly_flags():
    r = scanner.score_market(_br_none_no_anomaly(), _cfg("passthrough"))
    assert r["flag"] is True
    assert r["flag_path"] == "BR_NONE"

def test_passthrough_edge_only_flags():
    r = scanner.score_market(_edge_only(), _cfg("passthrough"))
    assert r["flag"] is True
    assert r["flag_path"] == "EDGE"

def test_passthrough_drift_only_flags():
    r = scanner.score_market(_drift_only(), _cfg("passthrough"))
    assert r["flag"] is True
    assert r["flag_path"] == "DRIFT"

def test_passthrough_inert_does_not_flag():
    r = scanner.score_market(_totally_inert(), _cfg("passthrough"))
    assert r["flag"] is False
    assert r["flag_path"] is None

def test_passthrough_br_none_with_drift_flags_via_br_none():
    """passthrough checks BR_NONE before DRIFT, so flag_path=BR_NONE wins."""
    r = scanner.score_market(_br_none_with_drift(), _cfg("passthrough"))
    assert r["flag"] is True
    assert r["flag_path"] == "BR_NONE"


# ── Mode: strict_anomaly_only ─────────────────────────────────────────────────

def test_strict_anomaly_br_none_no_anomaly_does_not_flag():
    """Key regression: BR_NONE catch-all must be suppressed in strict mode."""
    r = scanner.score_market(_br_none_no_anomaly(), _cfg("strict_anomaly_only"))
    assert r["flag"] is False
    assert r["flag_path"] is None

def test_strict_anomaly_edge_only_does_not_flag():
    """Edge alone (heuristic-derived) must not flag under strict_anomaly_only."""
    r = scanner.score_market(_edge_only(), _cfg("strict_anomaly_only"))
    assert r["flag"] is False
    assert r["flag_path"] is None

def test_strict_anomaly_drift_flags():
    r = scanner.score_market(_drift_only(), _cfg("strict_anomaly_only"))
    assert r["flag"] is True
    assert r["flag_path"] == "DRIFT"

def test_strict_anomaly_inert_does_not_flag():
    r = scanner.score_market(_totally_inert(), _cfg("strict_anomaly_only"))
    assert r["flag"] is False

def test_strict_anomaly_br_none_with_drift_flags_via_drift():
    r = scanner.score_market(_br_none_with_drift(), _cfg("strict_anomaly_only"))
    assert r["flag"] is True
    assert r["flag_path"] == "DRIFT"


# ── Mode: strict_with_heuristic ───────────────────────────────────────────────

def test_strict_heuristic_br_none_no_anomaly_does_not_flag():
    """No heuristic match → still excluded even in strict_with_heuristic."""
    r = scanner.score_market(_br_none_no_anomaly(), _cfg("strict_with_heuristic"))
    assert r["flag"] is False
    assert r["flag_path"] is None

def test_strict_heuristic_edge_flags_as_heuristic():
    """Heuristic base_rate with large edge → HEURISTIC path."""
    r = scanner.score_market(_edge_only(), _cfg("strict_with_heuristic"))
    assert r["flag"] is True
    assert r["flag_path"] == "HEURISTIC"

def test_strict_heuristic_drift_flags():
    r = scanner.score_market(_drift_only(), _cfg("strict_with_heuristic"))
    assert r["flag"] is True
    assert r["flag_path"] == "DRIFT"

def test_strict_heuristic_inert_does_not_flag():
    """Heuristic matches but edge too small → no flag."""
    r = scanner.score_market(_totally_inert(), _cfg("strict_with_heuristic"))
    assert r["flag"] is False

def test_strict_heuristic_br_none_with_drift_flags_via_drift():
    r = scanner.score_market(_br_none_with_drift(), _cfg("strict_with_heuristic"))
    assert r["flag"] is True
    assert r["flag_path"] == "DRIFT"


# ── Unknown mode raises ───────────────────────────────────────────────────────

def test_unknown_flag_mode_raises():
    import pytest
    with pytest.raises(ValueError, match="Unknown flag_mode"):
        scanner.score_market(_market(), _cfg("made_up_mode"))


# ── flag_path present on all scored markets ───────────────────────────────────

def test_flag_path_always_returned():
    """score_market must return flag_path key regardless of mode or outcome."""
    for mode in ("passthrough", "strict_anomaly_only", "strict_with_heuristic"):
        r = scanner.score_market(_market(), _cfg(mode))
        assert "flag_path" in r
        assert "flag_mode" in r


# ── Signal attribution — sig_* fields ────────────────────────────────────────
#
# sig_edge / sig_drift / sig_br_none must reflect whether the signal FIRED,
# independent of which mode is active and independent of branch evaluation order.
# A market where both edge and drift are present must report both sig_edge=True
# AND sig_drift=True under every mode, even when only one drives flag_path.

def _edge_and_drift():
    """
    base_rate=0.40 (rain), mid=0.22 → edge=0.18>0.08.
    last_price=0.30 → abs_drift=0.08, pct=27% > 5% → drift fires too.
    Both signals are present simultaneously.
    """
    m = _market(mid=0.22, title="Will it rain tomorrow")
    m["last_price_dollars"] = 0.30
    return m


def test_attribution_both_signals_reported_under_passthrough():
    """passthrough takes EDGE path but sig_drift must still be True."""
    r = scanner.score_market(_edge_and_drift(), _cfg("passthrough"))
    assert r["sig_edge"]  is True
    assert r["sig_drift"] is True
    assert r["flag_path"] == "EDGE"   # branch order determines flag_path


def test_attribution_both_signals_reported_under_strict_anomaly():
    """strict_anomaly_only takes DRIFT path but sig_edge must still be True."""
    r = scanner.score_market(_edge_and_drift(), _cfg("strict_anomaly_only"))
    assert r["sig_edge"]  is True
    assert r["sig_drift"] is True
    assert r["flag_path"] == "DRIFT"


def test_attribution_both_signals_reported_under_strict_heuristic():
    """strict_with_heuristic takes DRIFT path but sig_edge must still be True."""
    r = scanner.score_market(_edge_and_drift(), _cfg("strict_with_heuristic"))
    assert r["sig_edge"]  is True
    assert r["sig_drift"] is True
    assert r["flag_path"] == "DRIFT"


def test_attribution_br_none_market_sets_sig_br_none():
    """Market with no heuristic and no drift must have sig_br_none=True, others False."""
    r = scanner.score_market(_br_none_no_anomaly(), _cfg("passthrough"))
    assert r["sig_br_none"] is True
    assert r["sig_edge"]    is False
    assert r["sig_drift"]   is False


def test_attribution_fields_always_present():
    """sig_* keys must appear in every scored market regardless of mode."""
    for mode in ("passthrough", "strict_anomaly_only", "strict_with_heuristic"):
        r = scanner.score_market(_market(), _cfg(mode))
        assert "sig_edge"    in r
        assert "sig_drift"   in r
        assert "sig_br_none" in r


# ── Dual-threshold drift (abs + pct) ─────────────────────────────────────────

def _drift_abs_cfg(drift_min_abs: float, drift_min_pct: float) -> dict:
    c = _cfg("passthrough")
    c["markets"]["drift_min_abs"] = drift_min_abs
    c["markets"]["drift_min_pct"] = drift_min_pct
    return c


def test_drift_tiny_abs_suppressed_by_abs_threshold():
    """0.5¢ absolute move at 5.5¢ price: pct=9% passes but abs=0.005 < 0.02 → no drift."""
    m = _market(mid=0.055, title="Some event")
    m["last_price_dollars"] = 0.060  # abs=0.005, pct≈8.3%
    r = scanner.score_market(m, _drift_abs_cfg(drift_min_abs=0.02, drift_min_pct=0.05))
    assert r["drift_flag"] is False
    assert r["sig_drift"]  is False


def test_drift_requires_both_conditions():
    """Large abs but pct below threshold → no drift flag."""
    m = _market(mid=0.53, title="Some event")
    m["last_price_dollars"] = 0.50  # abs=0.03>0.02, pct=6%<10%
    r = scanner.score_market(m, _drift_abs_cfg(drift_min_abs=0.02, drift_min_pct=0.10))
    assert r["drift_flag"] is False


def test_drift_fires_when_both_thresholds_met():
    """abs=0.05 > 0.02 AND pct=12.5% > 0.05 → drift fires."""
    m = _market(mid=0.45, title="Some event")
    m["last_price_dollars"] = 0.40  # abs=0.05, pct=12.5%
    r = scanner.score_market(m, _drift_abs_cfg(drift_min_abs=0.02, drift_min_pct=0.05))
    assert r["drift_flag"] is True
    assert r["sig_drift"]  is True


def test_drift_price_drift_abs_always_returned():
    """price_drift_abs must be present in every scored market with a last price."""
    m = _market(mid=0.40, title="Will it rain tomorrow")
    m["last_price_dollars"] = 0.30
    r = scanner.score_market(m, BASE_CFG)
    assert "price_drift_abs" in r
    assert r["price_drift_abs"] == pytest.approx(0.10)


# ─── filter_markets: open interest floor ─────────────────────────────────────

def _oi_cfg(min_oi: int) -> dict:
    import copy
    c = copy.deepcopy(BASE_CFG)
    c["markets"]["min_open_interest"] = min_oi
    return c

def test_open_interest_zero_allows_any():
    """min_open_interest=0 disables the filter — all markets pass."""
    m = _market()
    m["open_interest_fp"] = 0
    assert len(scanner.filter_markets([m], _oi_cfg(0))) == 1

def test_open_interest_below_floor_dropped():
    """Market with OI=10 should be dropped when floor=25."""
    m = _market()
    m["open_interest_fp"] = 10
    assert scanner.filter_markets([m], _oi_cfg(25)) == []

def test_open_interest_at_floor_kept():
    m = _market()
    m["open_interest_fp"] = 25
    assert len(scanner.filter_markets([m], _oi_cfg(25))) == 1

def test_open_interest_above_floor_kept():
    m = _market()
    m["open_interest_fp"] = 500
    assert len(scanner.filter_markets([m], _oi_cfg(25))) == 1

def test_open_interest_missing_treated_as_zero():
    """Market with no OI field should be dropped when floor > 0."""
    m = _market()
    assert scanner.filter_markets([m], _oi_cfg(25)) == []


# ─── dedup_by_event ───────────────────────────────────────────────────────────

def _mkt_ev(ticker: str, event_ticker: str, volume: float) -> dict:
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "volume_fp": volume,
        "yes_bid": 0.49, "yes_ask": 0.51,
        "close_time": _close(30),
        "title": f"Market {ticker}",
        "time_horizon": "MONTHLY",
    }

def test_dedup_keeps_highest_volume_per_event():
    markets = [
        _mkt_ev("T1", "EVT-A", 1000),
        _mkt_ev("T2", "EVT-A", 5000),
        _mkt_ev("T3", "EVT-A", 200),
    ]
    result = scanner.dedup_by_event(markets)
    assert len(result) == 1
    assert result[0]["ticker"] == "T2"

def test_dedup_keeps_separate_events():
    markets = [
        _mkt_ev("T1", "EVT-A", 1000),
        _mkt_ev("T2", "EVT-B", 500),
    ]
    result = scanner.dedup_by_event(markets)
    assert len(result) == 2
    tickers = {m["ticker"] for m in result}
    assert tickers == {"T1", "T2"}

def test_dedup_passthrough_no_event_ticker():
    """Markets with no event_ticker are not deduplicated — pass through as-is."""
    markets = [
        {"ticker": "T1", "volume_fp": 100},
        {"ticker": "T2", "volume_fp": 200},
    ]
    result = scanner.dedup_by_event(markets)
    assert len(result) == 2

def test_dedup_single_market_unchanged():
    m = _mkt_ev("T1", "EVT-A", 1000)
    result = scanner.dedup_by_event([m])
    assert len(result) == 1
    assert result[0]["ticker"] == "T1"

def test_dedup_mixed_event_and_no_event():
    """Some markets have event_ticker, some don't — both preserved correctly."""
    markets = [
        _mkt_ev("T1", "EVT-A", 1000),
        _mkt_ev("T2", "EVT-A", 500),
        {"ticker": "T3", "volume_fp": 200},  # no event_ticker
    ]
    result = scanner.dedup_by_event(markets)
    assert len(result) == 2  # T1 (wins EVT-A) + T3 (no event)
    tickers = {m["ticker"] for m in result}
    assert "T1" in tickers
    assert "T3" in tickers


# ─── tag_watchlist_overlap ────────────────────────────────────────────────────

def test_watchlist_tag_sets_true_for_matching_ticker():
    m = _market()
    m["ticker"] = "KXABRAHAMSA-29-JAN20"
    scanner.tag_watchlist_overlap([m], {"KXABRAHAMSA-29-JAN20"})
    assert m["watchlist_signal"] is True

def test_watchlist_tag_false_for_non_matching():
    m = _market()
    m["ticker"] = "KXOTHER-TICKER"
    scanner.tag_watchlist_overlap([m], {"KXABRAHAMSA-29-JAN20"})
    assert m["watchlist_signal"] is False

def test_watchlist_tag_empty_set_all_false():
    markets = [_market(), _market()]
    for m in markets:
        m["ticker"] = "KXSOME-TICKER"
    scanner.tag_watchlist_overlap(markets, set())
    assert all(not m["watchlist_signal"] for m in markets)

def test_watchlist_tag_priority_sorts_first():
    """score_markets must rank watchlist-tagged markets before non-tagged."""
    tagged   = _market(mid=0.30, title="Some random event")
    tagged["ticker"] = "KXTAGGED"
    tagged["watchlist_signal"] = True
    tagged["time_horizon"] = "MONTHLY"
    tagged["close_time"] = _close(30)
    untagged = _market(mid=0.30, title="Some random event 2")
    untagged["ticker"] = "KXUNTAGGED"
    untagged["watchlist_signal"] = False
    untagged["time_horizon"] = "MONTHLY"
    untagged["close_time"] = _close(30)
    results = scanner.score_markets([untagged, tagged], BASE_CFG)
    assert results[0]["ticker"] == "KXTAGGED"


def test_watchlist_ticker_details_annotates_matching_market():
    m = _market()
    m["ticker"] = "KXABRAHAMSA-29-JAN20"
    details = {
        "KXABRAHAMSA-29-JAN20": {
            "consensus_direction": "NO",
            "trader_count": 3,
            "total_position_val": 15000.0,
            "kalshi_title": "Will Saudi Arabia join BRICS by Jan 2029?",
        }
    }
    scanner.tag_watchlist_overlap([m], {"KXABRAHAMSA-29-JAN20"}, ticker_details=details)
    assert m["watchlist_signal"] is True
    assert m["watchlist_direction"] == "NO"
    assert m["watchlist_trader_count"] == 3
    assert m["watchlist_position_val"] == 15000.0


def test_watchlist_ticker_details_non_matching_gets_none():
    m = _market()
    m["ticker"] = "KXOTHER-TICKER"
    details = {
        "KXABRAHAMSA-29-JAN20": {
            "consensus_direction": "YES",
            "trader_count": 2,
            "total_position_val": 5000.0,
            "kalshi_title": "Saudi Arabia BRICS",
        }
    }
    scanner.tag_watchlist_overlap([m], {"KXABRAHAMSA-29-JAN20"}, ticker_details=details)
    assert m["watchlist_signal"] is False
    assert m.get("watchlist_direction") is None
    assert m.get("watchlist_position_val") is None
    assert m.get("watchlist_trader_count") is None


def test_watchlist_ticker_details_unknown_direction_fallback():
    m = _market()
    m["ticker"] = "KXTEST-TICKER"
    details = {
        "KXTEST-TICKER": {
            "consensus_direction": "MIXED",
            "trader_count": 5,
            "total_position_val": 25000.0,
            "kalshi_title": "Test market",
        }
    }
    scanner.tag_watchlist_overlap([m], {"KXTEST-TICKER"}, ticker_details=details)
    assert m["watchlist_direction"] == "MIXED"
    assert m["watchlist_trader_count"] == 5
    assert m["watchlist_position_val"] == 25000.0


def test_watchlist_ticker_details_none_still_works():
    """Passing ticker_details=None should behave the same as omitting it."""
    m = _market()
    m["ticker"] = "KXABRAHAMSA-29-JAN20"
    scanner.tag_watchlist_overlap([m], {"KXABRAHAMSA-29-JAN20"}, ticker_details=None)
    assert m["watchlist_signal"] is True
    # Direction fields should not be set when no details provided
    assert m.get("watchlist_direction") is None
    assert m.get("watchlist_position_val") is None
    assert m.get("watchlist_trader_count") is None


def test_watchlist_ticker_details_multiple_markets():
    """ticker_details annotations apply only to tickers in the watchlist set."""
    m1 = _market()
    m1["ticker"] = "KXFOO"
    m2 = _market()
    m2["ticker"] = "KXBAR"
    m3 = _market()
    m3["ticker"] = "KXBAZ"
    details = {
        "KXFOO": {"consensus_direction": "YES", "trader_count": 1, "total_position_val": 1000.0, "kalshi_title": ""},
        "KXBAR": {"consensus_direction": "NO",  "trader_count": 2, "total_position_val": 2000.0, "kalshi_title": ""},
    }
    scanner.tag_watchlist_overlap([m1, m2, m3], {"KXFOO", "KXBAR"}, ticker_details=details)
    assert m1["watchlist_signal"] is True
    assert m1["watchlist_direction"] == "YES"
    assert m2["watchlist_signal"] is True
    assert m2["watchlist_direction"] == "NO"
    assert m3["watchlist_signal"] is False
    assert m3.get("watchlist_direction") is None


def test_watchlist_flag_path_override():
    """Unflagged watchlist market gets flag_path=WATCHLIST when force-flagged."""
    # Build an inert market: mid=0.50, no drift, no keyword match in heuristics
    # Using a title with no heuristic match and a volume tier that gives no edge
    m = _market(mid=0.50, title="Will the XYZ regulation pass the committee vote?")
    m["ticker"]           = "KXREGULATION-29"
    m["time_horizon"]     = "LONG"
    m["close_time"]       = _close(500)
    m["last_price"]       = 0.50   # no drift
    m["watchlist_signal"] = False

    cfg   = BASE_CFG
    scored = scanner.score_market(m, cfg)
    # If naturally unflagged, simulate the main.py watchlist force-flag
    if not scored["flag"]:
        scored["watchlist_signal"] = True
        scored["flag"]      = True
        scored["flag_path"] = "WATCHLIST"
        assert scored["flag"] is True
        assert scored["flag_path"] == "WATCHLIST"
    else:
        # Already flagged by heuristic — just verify flag_path is set
        assert scored["flag_path"] is not None


# ─── estimate_base_rate: expanded heuristics ─────────────────────────────────

@pytest.mark.parametrize("title,expected_not_none", [
    ("Will Argentina win on 2026-06-16?", True),       # "win on" pattern
    ("Will Vitality win IEM Cologne Major 2026?", True), # "win" + championship
    ("Will France win the 2026 FIFA World Cup?", True), # "win the"
    ("FDA approval for new Alzheimer drug", True),       # FDA approval
    ("Will company X file for bankruptcy?", True),       # bankruptcy
    ("Will peace deal be signed by June 30?", True),    # peace deal
    ("Some abstract AI governance question", None),      # no heuristic match
])
def test_base_rate_expanded_heuristics(title, expected_not_none):
    m = _market(title=title)
    rate = scanner.estimate_base_rate(m)
    if expected_not_none:
        assert rate is not None, f"Expected non-None base rate for: {title}"
    else:
        assert rate is None, f"Expected None base rate for: {title}"


@pytest.mark.parametrize("title,expected_rate", [
    # Legislative
    ("Will the infrastructure bill pass the Senate by July?", 0.35),
    ("Will the spending bill pass the House in Q3?", 0.35),
    ("Will the bill be signed into law before August?", 0.35),
    ("Will Biden veto the tax reform bill?", 0.20),
    # Executive / appointments
    ("Will Trump sign an executive order on immigration?", 0.45),
    ("Will the new Treasury Secretary be confirmed by the Senate?", 0.55),
    ("Will the Fed chair resign before 2027?", 0.20),
    ("Will Trump pardon Roger Stone?", 0.35),
    # Sanctions
    ("Will the US impose new sanctions on Iran?", 0.45),
    ("Will the EU lift sanctions on Russia by 2027?", 0.20),
    # International agreements
    ("Will the US and Iran reach a nuclear deal by 2027?", 0.20),
    ("Will Ukraine join NATO before 2030?", 0.35),
    # Legal / court
    ("Will the Supreme Court overturn Chevron deference?", 0.50),
    ("Will SCOTUS rule in favor of the plaintiff?", 0.50),
    ("Will the appeals court strike down the regulation?", 0.50),
    ("Will Elon Musk reach a lawsuit settlement?", 0.40),
    # Economic indicators
    ("Will the unemployment rate fall below 4% in June?", 0.50),
    ("Will CPI inflation exceed 3% in May 2026?", 0.50),
    ("Will US GDP growth exceed 2% in Q2 2026?", 0.50),
    # Crypto
    ("Will Bitcoin price exceed $120,000 by end of 2026?", 0.50),
    ("Will Ethereum ETF approval happen before July 2026?", 0.50),
    ("Will crypto market cap exceed $5T by 2027?", 0.50),
    # Weather / natural
    ("Will a Category 4 hurricane hit the US in 2026?", 0.45),
    # Geopolitical
    ("Will US recognize Palestinian statehood before 2027?", 0.30),
    ("Will Saudi Arabia normalize relations with Israel?", 0.30),
    # IPO announcement timing markets
    ("When will Canva officially announce an IPO?", 0.25),
    ("When will Fannie Mae officially announce an IPO?", 0.25),
    ("When will Stripe going public be confirmed?", 0.25),
    # Sports debut markets
    ("Will Kade Anderson play in a game for any team in the MLB before Nov 1?", 0.35),
    ("Will Scott Walcott play in a game for any MLB team before November 1?", 0.35),
    ("Will the prospect make his MLB debut before August?", 0.35),
    # Cabinet departure markets
    ("Will any member of Trump's Cabinet leave before Sep 2026?", 0.65),
    ("Will any Trump cabinet member depart before January 2027?", 0.65),
    ("Will any senior official leave the cabinet before midterms?", 0.65),
    # Congressional control markets
    ("Will Democrats control the Senate after 2026 midterms?", 0.50),
    ("Which party will have a senate majority after November 2026?", 0.50),
    ("Will Republicans flip the House in 2026?", 0.50),
    ("Senate control 2026 election outcome", 0.50),
    # Space / aerospace
    ("Will SpaceX Starship complete an orbital flight in Q3 2026?", 0.40),
    ("Will NASA's Artemis mission launch before end of 2026?", 0.30),
    # Health / clinical trials
    ("Will the Phase 3 trial for X drug succeed by Q2 2026?", 0.35),
    ("Will a pandemic be declared by the WHO in 2026?", 0.25),
    # Climate / energy policy
    ("Will Congress pass a carbon tax before 2027?", 0.35),
    ("Will the EU reach its net zero emissions target by 2030?", 0.35),
    # AI model release timing markets
    ("Will OpenAI release GPT-5 by end of Q3 2026?", 0.25),
    ("Will GPT-6 be released before December 2026?", 0.25),
    ("Will Claude 4 be released before July 2026?", 0.25),
    ("Will Gemini Ultra launch before the end of 2026?", 0.25),
    ("Will AGI by 2028 be achieved by any lab?", 0.25),
    # Trade / tariff markets
    ("Will the US impose a tariff on Canadian steel above 25%?", 0.40),
    ("Will Trump raise tariffs on China imports in Q3 2026?", 0.40),
    ("Will tariff rates on EU goods be reduced by end of 2026?", 0.40),
    # Sports awards
    ("Will Shohei Ohtani win the NL MVP in 2026?", 0.20),
    ("Will Connor McDavid win the NHL MVP award?", 0.20),
    ("Will the Heisman Trophy go to a running back?", 0.20),
    # Sports playoff qualification
    ("Will the New York Yankees make the playoffs in 2026?", 0.35),
    ("Will Manchester City qualify for the Champions League?", 0.35),
    ("Will the Dallas Cowboys reach the playoffs in 2026?", 0.35),
    # Sports trades
    ("Will LeBron James get traded before the deadline?", 0.30),
    ("Will the NBA trade deadline result in a blockbuster deal?", 0.30),
    # Immigration / deportation
    ("Will mass deportation operations exceed 1 million in 2026?", 0.35),
    # Note: "Will the Supreme Court block deportation flights?" hits the "supreme court"
    # pattern (0.50) before the "deportation" pattern — SCOTUS ruling base rate is correct.
    ("Will ICE deport more than 50,000 people in June 2026?", 0.35),
    # Fired / dismissed
    ("Will the Treasury Secretary be fired before 2027?", 0.25),
    ("Will Kash Patel be dismissed from his position before June 2027?", 0.25),
    ("Will the White House Chief of Staff get fired before Q4 2026?", 0.25),
    # Government shutdown — STARTS (0.15) vs AVOIDED (0.85)
    ("Will there be a government shutdown before October 2026?", 0.15),
    ("Will a partial shutdown begin in September 2026?", 0.15),
    ("Will Congress avoid a shutdown by the deadline?", 0.85),
    ("Will lawmakers avert a shutdown before the CR expires?", 0.85),
    ("Will the government shutdown end before March 2026?", 0.85),
    # Debt ceiling — raise/deal (0.70) vs generic (0.65)
    ("Will Congress raise the debt ceiling before August 2026?", 0.70),
    ("Will Democrats and Republicans reach a debt ceiling deal?", 0.70),
    ("Will the debt limit be suspended again by March 2026?", 0.70),
    ("Will the US hit the debt ceiling in 2026?", 0.65),
    ("Will the US breach the debt limit before September 2026?", 0.65),
    # Congressional spending / continuing resolution
    ("Will Congress pass a continuing resolution before October 2026?", 0.40),
    ("Will an omnibus bill be signed into law before end of 2026?", 0.40),
    # Antitrust / FTC / DOJ
    ("Will the FTC block the Microsoft-Activision deal?", 0.40),
    ("Will the DOJ block the American Airlines merger?", 0.40),
    ("Will the antitrust case against Google succeed?", 0.40),
    # North Korea / DPRK
    ("Will North Korea launch a missile test in Q3 2026?", 0.40),
    ("Will DPRK conduct a nuclear test in 2026?", 0.40),
    ("Will North Korea nuclear provocations escalate before end of 2026?", 0.40),
    # Fed / FOMC — "raise rates" word ordering
    ("Will the FOMC raise rates at the March 2026 meeting?", 0.50),
    ("Will the Fed cut rates by 25bps in June?", 0.50),
    ("Will the Fed hike rates before year end?", 0.50),
    ("Will the FOMC lower interest rates in Q3 2026?", 0.50),
    # Unemployment without "rate" suffix
    ("Will unemployment be above 4.5% in June 2026?", 0.50),
    ("Will unemployment rise above 5% before Q4?", 0.50),
    # Price levels — "hit $" form
    ('Will Nvidia stock hit $200 before year end?', 0.35),
    ('Will gold top $3,000 in 2026?', 0.35),
    # IPO — "go public" form
    ("Will OpenAI go public before end of 2026?", 0.25),
    # Legislative — senate/house pass reversed ordering
    ("Will the Senate pass the tax reform bill before October?", 0.35),   # "senate pass" → 0.35
    ("Will the Senate pass the budget before October?", 0.40),            # "pass the budget" → appropriations 0.40
    ("Will the House approve the tax bill in Q3 2026?", 0.35),
    ("Will the Senate vote on the tax bill before December?", 0.35),
    # Tech / social media ban
    ("Will TikTok be banned in the US by August 2026?", 0.20),
    ("Will the US ban TikTok before end of 2026?", 0.20),
    ("Will Trump sign a TikTok ban into law?", 0.20),
    # Arrested / in custody
    ("Will Donald Trump be arrested before the 2026 midterms?", 0.30),
    ("Will the suspect be taken into custody before trial?", 0.30),
    ("Will Hunter Biden be arraigned on new charges in 2026?", 0.30),
    # Congressional testimony / hearings
    ("Will Elon Musk testify before the Senate in 2026?", 0.50),
    ("Will the CEO appear before a congressional committee in June?", 0.50),
    # Approval ratings
    ("Will Trump's approval rating be above 50% in June 2026?", 0.50),
    ("Will Biden's net approval be positive by year end?", 0.50),
    # Labor strikes
    ("Will Hollywood writers go on strike again in 2026?", 0.30),
    ("Will the UAW announce a work stoppage in Q3 2026?", 0.30),
    # Entertainment awards — must NOT hit the " win " catch-all (0.52)
    ("Will Beyoncé win the Grammy for Album of the Year?", 0.20),
    ("Will the Oscars Academy Award go to a streaming film?", 0.20),
    ("Will the Golden Globe Award for Best Drama go to HBO?", 0.20),
    # False positive guards
    # "senator" contains "senate" — but "senate hearing" is the pattern, not a substring issue
    # "movie arrest scene" → "arrest" alone not in list; only specific phrases
    ("Will a movie arrest scene win an Oscar?", 0.20),   # hits "oscar" → 0.20, not arrested
    # Reelection — slight incumbent advantage
    ("Will Trump win reelection in 2028?", 0.52),
    ("Will the incumbent be reelected in the 2026 midterms?", 0.52),
    ("Will the governor win re-election in November?", 0.52),
    # Diplomatic summits / meetings
    ("Will there be a bilateral summit between the US and China in 2026?", 0.40),
    ("Will Biden meet with Xi at a diplomatic summit before year end?", 0.40),
    ("Will a peace summit between Israel and Palestine take place in 2026?", 0.40),
    # Earnings beat/miss
    ("Will Apple beat earnings in Q3 2026?", 0.50),
    ("Will Tesla miss earnings estimates in Q2 2026?", 0.50),
    ("Will Nvidia's EPS beat analyst expectations in Q1?", 0.50),
    # Stock index price levels — must NOT hit generic "above $" (0.35)
    ("Will the S&P 500 above 6000 by end of 2026?", 0.50),
    ("Will the Nasdaq exceed 20000 in Q3 2026?", 0.50),
    ("Will the VIX above 30 at any point in 2026?", 0.50),
    ("Will the Dow Jones above 45000 before year end?", 0.50),
    # Health / mortality
    ("Will the suspect die before trial in 2026?", 0.15),
    ("Will the 95-year-old still alive by December 2026?", 0.15),
    # False positive guard — "stock above $150" must NOT hit stock index block
    ("Will Apple stock above $150?", 0.35),            # generic "above $" → 0.35, not 0.50
    # False positive guard — "summit" alone in unrelated context → no match from diplomatic block
    # (summit could be a mountain/location; diplomatic block requires "summit between/with" etc.)
    ("Will the tech summit produce a new AI governance framework?", None),  # no heuristic
    # Nobel Prize — very low base rate (single winner from hundreds of candidates)
    ("Will Elon Musk win the Nobel Prize in Physics in 2026?", 0.10),
    ("Will the Nobel Peace Prize be awarded to a climate activist in 2026?", 0.10),
    # UN Security Council — China/Russia veto risk keeps rate low
    ("Will the UN Security Council pass a resolution on Gaza in 2026?", 0.15),
    ("Will the United Nations Security Council vote to sanction Russia?", 0.15),
    # SEC/regulatory approval
    ("Will the SEC approve the Bitcoin spot ETF application in Q3 2026?", 0.40),
    ("Will the FCC approve the merger of the two telecom companies?", 0.40),
    # Corporate appointment / CEO change
    ("Will Bob Iger become CEO of Disney again?", 0.35),
    ("Will Tesla be named a new CEO to replace Musk by 2026?", 0.35),
    # False positive guard — "Nobel" in unrelated context
    # "Nobel conference" → no match (our patterns require "nobel prize"/"win the nobel"/"nobel laureate")
    ("Will the Nobel conference attract more attendees in 2026?", None),  # no heuristic
    # Political withdrawal (handles year insertion)
    ("Will Biden withdraw from the 2024 race before September?", 0.30),
    ("Will DeSantis suspend his campaign before the Iowa caucus?", 0.30),
    ("Will the candidate drop out of the Democratic primary?", 0.30),
    # Special election
    ("Will there be a special election in Georgia before June 2026?", 0.45),
    ("Will Congress call a special senate election to fill the vacancy?", 0.45),
    # Constitutional amendment — very hard to pass
    ("Will there be a constitutional amendment to abolish the Electoral College?", 0.05),
    ("Will the Equal Rights Amendment be ratified in 2026?", 0.05),
    # Ballot disqualification (handles year insertion)
    ("Will Trump be disqualified from the 2024 ballot?", 0.20),
    ("Will the candidate be kicked off the ballot by court order?", 0.20),
    # Divestiture / forced sale
    ("Will TikTok be sold by the deadline?", 0.35),
    ("Will the company be forced to divest its media division?", 0.35),
    # Stock split
    ("Will Apple announce a stock split before year end?", 0.20),
    ("Will Nvidia split its stock in 2026?", 0.20),
    # FDA base form (missing 's')
    ("Will the FDA approve semaglutide for weight loss in 2026?", 0.40),
    # Moon landing — China case
    ("Will China land astronauts on the Moon before 2028?", 0.30),
    # COVID variant
    ("Will a new COVID variant be declared a variant of concern in 2026?", 0.30),
    # Pulitzer Prize — small finalists field, single annual winner → ~10%
    ("Will Bob Woodward win the Pulitzer Prize in 2026?", 0.10),
    ("Will the newspaper win a Pulitzer for its investigative series?", 0.10),
    # Extradition — legal process already underway → ~35%
    ("Will Julian Assange be extradited to the United States?", 0.35),
    ("Will the cartel leader face an extradition request by Q3 2026?", 0.35),
    # Primary challenge — incumbent faces opposition (not whether challenger wins) → ~30%
    ("Will Biden face a primary challenge from the left?", 0.30),
    ("Will the incumbent senator face a primary opponent in 2026?", 0.30),
    # False positive guard — "primary" in context of "win the primary" stays at 0.52
    ("Will Trump win the primary in 2028?", 0.52),
    # Fed pause / hold rates
    ("Will the Fed pause rate hikes at the June 2026 meeting?", 0.50),
    ("Will the FOMC hold rates unchanged in Q3 2026?", 0.50),
    ("Will the Fed maintain rates at current levels through year end?", 0.50),
    # Federal budget / budget deal
    ("Will Congress pass a federal budget deal by October 2026?", 0.40),
    ("Will Congress reach a budget agreement before the deadline?", 0.40),
    # 25th Amendment — historically never successfully invoked non-voluntarily
    ("Will the 25th Amendment be invoked against the President?", 0.05),
    ("Will Congress invoke the 25th to remove the President?", 0.05),
    # Pardon / clemency — president has broad authority; depends on political climate
    ("Will Trump pardon Michael Flynn before the end of 2026?", 0.35),
    ("Will the President grant clemency to the former official?", 0.35),
    ("Will Hunter Biden receive a presidential pardon in 2026?", 0.35),
    ("Will the governor grant clemency to the former official?", 0.35),
    # Plea deal — most criminal cases resolve via plea (~45% for high-profile cases)
    ("Will Michael Cohen enter a plea deal with prosecutors?", 0.45),
    ("Will the defendant accept a plea agreement before trial?", 0.45),
    ("Will the former official plead guilty to the charges?", 0.45),
    # Acquittal / not guilty — contested high-profile trial outcome (~35%)
    ("Will the defendant be acquitted on all charges?", 0.35),
    ("Will the jury return a not guilty verdict?", 0.35),
    ("Will the former president be acquitted in the second trial?", 0.35),
    # False positive guard: "found guilty" still returns 0.40
    ("Will the executive be found guilty of fraud charges?", 0.40),
    # False positive guard: "convicted" still returns 0.40
    ("Will the official be convicted before year end?", 0.40),
    # Corporate layoffs / workforce reduction
    ("Will Meta announce mass layoffs in Q2 2026?", 0.35),
    ("Will the company reduce its workforce by 20%?", 0.35),
    ("Will Apple announce job cuts before the earnings call?", 0.35),
    # Housing market crash — tail event
    ("Will the US housing market crash by Q4 2026?", 0.15),
    ("Will there be a real estate crash in 2026?", 0.15),
    # Housing prices — near 50/50 like price-level markets
    ("Will median home prices rise in 2026?", 0.50),
    ("Will housing prices fall by year end?", 0.50),
    # Removed from role — extended "be removed" to cover non-positional forms
    ("Will Elon Musk be removed from DOGE by January 2027?", 0.25),
    ("Will the official be removed before the election?", 0.25),
    # False positive guard: "removed from office" still hits impeachment block → 0.15
    ("Will the president be removed from office by Congress?", 0.15),
    # Face trial — criminal proceeding (~35%)
    ("Will Julian Assange face trial in the US by 2026?", 0.35),
    ("Will the former official stand trial on corruption charges?", 0.35),
    # Regulatory fines (~40%)
    ("Will Google be fined by the EU in 2026?", 0.40),
    ("Will the company receive a fine from the FTC?", 0.40),
    # Cyberattack / data breach (~35%)
    ("Will there be a major cyberattack on US infrastructure?", 0.35),
    ("Will a ransomware attack disrupt a critical US agency?", 0.35),
    # Trade deficit / balance (~50%)
    ("Will the US trade deficit widen in Q2 2026?", 0.50),
    ("Will the trade balance improve by year end?", 0.50),
    # Treasury yield / bond level (~50%)
    ("Will the 10-year Treasury yield exceed 5% in 2026?", 0.50),
    ("Will the 30-year Treasury yield stay below 4.5%?", 0.50),
    # Electoral College abolition → constitutional amendment (0.05)
    ("Will the Electoral College be abolished by 2028?", 0.05),
    ("Will Congress vote to eliminate the electoral college?", 0.05),
    # Snap / early election (~25%)
    ("Will France call a snap election in 2026?", 0.25),
    ("Will the UK hold an early general election before 2027?", 0.25),
    # Political withdrawal via "not seek" phrasing (~30%)
    ("Will Biden announce he will not seek a second term?", 0.30),
    ("Will the incumbent senator choose not to run for reelection?", 0.30),
    # Minimum wage legislation (~25%)
    ("Will Congress raise the minimum wage in 2026?", 0.25),
    ("Will the federal minimum wage increase to $15?", 0.25),
    # National emergency declaration (~25%)
    ("Will the president declare a national emergency at the border?", 0.25),
    ("Will Biden invoke emergency powers over housing costs?", 0.25),
    # Nuclear power plant accident (~5%)
    ("Will a nuclear power plant accident occur in Europe?", 0.05),
    ("Will there be a nuclear reactor meltdown in 2026?", 0.05),
    # Nuclear weapons development (~5%)
    ("Will Iran develop a nuclear weapon by 2027?", 0.05),
    ("Will North Korea acquire nuclear warhead miniaturization capability?", 0.05),
    # NATO Article 5 invocation (~5%)
    ("Will NATO invoke Article 5 in response to Russian aggression?", 0.05),
    ("Will Article 5 of the NATO treaty be invoked in 2026?", 0.05),
    # Military troop withdrawal (~30%)
    ("Will the US complete a troop withdrawal from Syria by Q3?", 0.30),
    ("Will US forces leave Afghanistan permanently?", 0.30),
    # Commodity/energy price thresholds (~40%)
    ("Will gold prices exceed $3000 per ounce by June 2026?", 0.40),
    ("Will crude oil prices fall below $60 per barrel?", 0.40),
    ("Will brent crude rise above $90?", 0.40),
    ("Will natural gas prices exceed $4 per MMBtu?", 0.40),
    # Interest rate threshold questions (~50%)
    ("Will interest rates rise above 6% in 2026?", 0.50),
    ("Will interest rates fall below 4% by year end?", 0.50),
    # Inflation threshold questions (~50%)
    ("Will inflation exceed 4% in 2026?", 0.50),
    ("Will inflation fall below the Fed's 2% inflation target?", 0.50),
    # Retail / consumer data (~45%)
    ("Will retail sales decline in Q2 2026?", 0.45),
    ("Will consumer confidence fall below 90 in March?", 0.45),
    # Wildfire / natural disaster (~35%)
    ("Will a wildfire destroy more than 1 million acres in California?", 0.35),
    ("Will the 2026 wildfire season be worse than 2020?", 0.35),
    # Tech product announcements (~55%)
    ("Will Apple announce a new iPhone by September 2026?", 0.55),
    ("Will Samsung launch a new Galaxy flagship in Q1?", 0.55),
    # Concert / tour announcements (~45%)
    ("Will Taylor Swift announce a new concert tour in 2026?", 0.45),
    ("Will Beyonce go on a world tour in 2026?", 0.45),
    # Immigration legislation (~35%)
    ("Will Congress pass a new immigration bill?", 0.35),
    ("Will Congress pass comprehensive immigration reform in 2026?", 0.35),
    # Merger/acquisition — "be acquired" phrasing (~35%)
    ("Will OpenAI be acquired in 2026?", 0.35),
    ("Will X be taken over by a major tech company?", 0.35),
    # Bankruptcy — "go bankrupt" phrasing (~15%)
    ("Will a US airline go bankrupt in 2026?", 0.15),
    ("Will the retailer declare bankruptcy before year end?", 0.15),
    # Bank failure (~15%)
    ("Will there be a major bank failure in the US in 2026?", 0.15),
    ("Will a bank collapse trigger a banking crisis in 2026?", 0.15),
    # Gun control / firearms legislation (~20%)
    ("Will Congress pass a new gun control bill in 2026?", 0.20),
    # "become law" hits the legislative block first (0.35) — gun control framing without
    # "pass" / "become law" is needed to hit the 0.20 gun control block
    ("Will there be a federal assault weapons ban in 2026?", 0.20),
    # Currency / exchange rate (~40%)
    ("Will the Mexican peso depreciate more than 10% against the dollar?", 0.40),
    ("Will the euro appreciate against the dollar by year end?", 0.40),
    # Company valuation (~35%)
    ("Will X be valued above $50B in 2026?", 0.35),
    ("Will OpenAI reach a valuation above $200B?", 0.35),
    # Tech market position (~35%)
    ("Will a new AI coding assistant surpass GitHub Copilot by Q4?", 0.35),
    ("Will Apple beat Microsoft in market cap by end of 2026?", 0.35),
    # Social media age restrictions (~30%)
    ("Will social media use be restricted for minors in the US?", 0.30),
    ("Will Congress pass online age verification legislation?", 0.30),
    # Corporate leadership retention (~65%)
    ("Will Elon Musk remain CEO of Tesla in 2026?", 0.65),
    ("Will Jensen Huang stay as CEO of Nvidia through 2026?", 0.65),
    # Volcanic eruption (~5%)
    ("Will Yellowstone National Park have a major volcanic eruption?", 0.05),
    ("Will a volcanic eruption disrupt European air travel?", 0.05),
    # Common currency (~10%)
    ("Will a BRICS country adopt a common currency by 2027?", 0.10),
    ("Will the BRICS nations form a currency union?", 0.10),
    # Economic performance comparisons (~50%)
    ("Will the UK economy outperform the EU average in 2026?", 0.50),
    ("Will US GDP growth exceed the G7 average in 2026?", 0.50),
])
def test_base_rate_new_categories(title, expected_rate):
    m = _market(title=title)
    rate = scanner.estimate_base_rate(m)
    assert rate == pytest.approx(expected_rate), (
        f"title={title!r}: expected {expected_rate}, got {rate}"
    )
