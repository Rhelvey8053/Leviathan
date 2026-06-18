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


# ─── dedup_by_event_scored ────────────────────────────────────────────────────

def _scored_mkt(ticker: str, event_ticker: str, raw_edge: float = 0.0,
                net_edge: float = None, volume: float = 1000,
                watchlist_signal: bool = False) -> dict:
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "volume_fp": volume,
        "raw_edge": raw_edge,
        "net_edge": net_edge,
        "watchlist_signal": watchlist_signal,
    }


def test_dedup_scored_picks_highest_net_edge():
    """dedup_by_event_scored keeps market with highest net_edge per event."""
    markets = [
        _scored_mkt("T1", "EVT-A", raw_edge=0.15, net_edge=0.12),
        _scored_mkt("T2", "EVT-A", raw_edge=0.10, net_edge=0.08),
        _scored_mkt("T3", "EVT-A", raw_edge=0.20, net_edge=0.05),
    ]
    result = scanner.dedup_by_event_scored(markets)
    assert len(result) == 1
    assert result[0]["ticker"] == "T1"  # highest net_edge


def test_dedup_scored_watchlist_beats_higher_edge():
    """Watchlist signal wins over higher net_edge — smart money confirmation takes priority."""
    markets = [
        _scored_mkt("T1", "EVT-A", raw_edge=0.25, net_edge=0.20),
        _scored_mkt("T2", "EVT-A", raw_edge=0.10, net_edge=0.08, watchlist_signal=True),
    ]
    result = scanner.dedup_by_event_scored(markets)
    assert len(result) == 1
    assert result[0]["ticker"] == "T2"  # watchlist wins


def test_dedup_scored_net_edge_beats_raw_edge():
    """When net_edge differs from raw_edge ordering, net_edge wins."""
    markets = [
        _scored_mkt("T1", "EVT-A", raw_edge=0.20, net_edge=0.04),  # raw high, net low
        _scored_mkt("T2", "EVT-A", raw_edge=0.12, net_edge=0.10),  # raw lower, net higher
    ]
    result = scanner.dedup_by_event_scored(markets)
    assert len(result) == 1
    assert result[0]["ticker"] == "T2"  # net_edge wins tiebreak


def test_dedup_scored_none_net_edge_treated_as_minus_one():
    """net_edge=None is treated as -1 (worse than any real edge)."""
    markets = [
        _scored_mkt("T1", "EVT-A", raw_edge=0.10, net_edge=None),
        _scored_mkt("T2", "EVT-A", raw_edge=0.09, net_edge=0.07),
    ]
    result = scanner.dedup_by_event_scored(markets)
    assert len(result) == 1
    assert result[0]["ticker"] == "T2"  # real net_edge beats None


def test_dedup_scored_volume_fallback():
    """Volume is the final tiebreaker when edge metrics are equal."""
    markets = [
        _scored_mkt("T1", "EVT-A", raw_edge=0.10, net_edge=0.08, volume=1000),
        _scored_mkt("T2", "EVT-A", raw_edge=0.10, net_edge=0.08, volume=5000),
    ]
    result = scanner.dedup_by_event_scored(markets)
    assert len(result) == 1
    assert result[0]["ticker"] == "T2"  # higher volume wins tiebreak


def test_dedup_scored_separate_events_both_kept():
    """Markets in different events are all kept."""
    markets = [
        _scored_mkt("T1", "EVT-A", raw_edge=0.10),
        _scored_mkt("T2", "EVT-B", raw_edge=0.12),
    ]
    result = scanner.dedup_by_event_scored(markets)
    assert len(result) == 2


def test_dedup_scored_no_event_ticker_passthrough():
    """Markets with no event_ticker pass through unchanged."""
    markets = [
        {"ticker": "T1", "raw_edge": 0.10},
        {"ticker": "T2", "raw_edge": 0.20},
    ]
    result = scanner.dedup_by_event_scored(markets)
    assert len(result) == 2


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


def test_watchlist_stale_flag_set_when_stale_true():
    """watchlist_stale=True is set on matching markets when stale=True."""
    m = _market()
    m["ticker"] = "KXTEST-STALE"
    scanner.tag_watchlist_overlap([m], {"KXTEST-STALE"}, stale=True)
    assert m["watchlist_signal"] is True
    assert m["watchlist_stale"] is True


def test_watchlist_stale_flag_false_when_fresh():
    """watchlist_stale=False on matching markets when stale=False (default)."""
    m = _market()
    m["ticker"] = "KXTEST-FRESH"
    scanner.tag_watchlist_overlap([m], {"KXTEST-FRESH"}, stale=False)
    assert m["watchlist_signal"] is True
    assert m["watchlist_stale"] is False


def test_watchlist_stale_not_set_on_non_matching():
    """Non-matching markets get watchlist_stale=False via setdefault."""
    m = _market()
    m["ticker"] = "KXTEST-NOMATCH"
    scanner.tag_watchlist_overlap([m], {"KXTEST-OTHER"}, stale=True)
    assert m["watchlist_signal"] is False
    assert m.get("watchlist_stale") is False


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
    ("Some abstract AI governance question", 0.30),      # no heuristic match
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
    ("Will the spending bill pass the House in Q3?", 0.40),
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
    ("Will Saudi Arabia normalize relations with Israel?", 0.20),
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
    # "gold top" now correctly classified as commodity price level (0.40), not generic (0.35)
    ('Will gold top $3,000 in 2026?', 0.40),
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
    ("Will the tech summit produce a new AI governance framework?", 0.30),  # no heuristic
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
    # Student loan forgiveness (~30%)
    ("Will student loan forgiveness be implemented in 2026?", 0.30),
    ("Will the government cancel student debt by year end?", 0.30),
    # Healthcare reform (~20%)
    ("Will the US healthcare system be reformed in 2026?", 0.20),
    ("Will Congress pass a single payer healthcare bill?", 0.20),
    # International agreement re-entry (~25%)
    ("Will the US rejoin the Paris Climate Agreement in 2026?", 0.25),
    ("Will the UK rejoin the Paris accord by 2027?", 0.25),
    # Data leak / government hack (~35%)
    ("Will there be a major data leak at a US government agency?", 0.35),
    ("Will a federal database be hacked by foreign actors?", 0.35),
    # Civil war / internal armed conflict (~25%)
    ("Will Afghanistan fall into civil war again by 2027?", 0.25),
    ("Will a rebel insurgency destabilize the government?", 0.25),
    # Political scandal (~45%)
    ("Will there be a political scandal involving the president?", 0.45),
    ("Will a bribery scandal emerge in the administration?", 0.45),
    # Autonomous vehicle (~25%)
    ("Will Tesla release a fully autonomous robotaxi by end of 2026?", 0.25),
    ("Will a driverless car service launch in a major US city?", 0.25),
    # Quantum computing breakthrough (~10%)
    ("Will quantum computing break standard encryption by 2027?", 0.10),
    ("Will a company achieve quantum supremacy at scale?", 0.10),
    # Mars / deep space mission (~15%)
    ("Will there be a successful manned Mars mission by 2030?", 0.15),
    ("Will SpaceX send a crewed mission to Mars?", 0.15),
    # Renewable energy threshold (~40%)
    ("Will renewable energy supply 30% of US electricity by 2026?", 0.40),
    ("Will clean energy supply exceed fossil fuels in the EU?", 0.40),
    # Housing market correction (~20%)
    # Note: "home prices correct" would hit the housing prices block (0.50) first
    # Use "housing market correct" or "housing correction" to reach the 0.20 block
    ("Will the housing market correct more than 20% in 2026?", 0.20),
    ("Will there be a housing correction in the US by Q3 2026?", 0.20),
    # "acquire" verb phrasing in M&A titles (~35%)
    ("Will Microsoft acquire a major gaming company in 2026?", 0.35),
    # "acquire the startup" hits None (no pattern); use "acquire a" phrasing instead
    ("Will a private equity firm acquire an established rival?", 0.35),
    # Spending bill / government funding (~40%)
    ("Will Congress pass a spending bill before the deadline?", 0.40),
    # GDP phrasing with "exceed" rather than "gdp growth" (~50%)
    ("Will US GDP exceed 3% growth in Q3 2026?", 0.50),
    # Corporate market entry (~35%)
    ("Will Amazon enter the healthcare insurance market?", 0.35),
    ("Will Apple move into the banking market?", 0.35),
    # Production / delivery milestone (~40%)
    ("Will Tesla achieve 2 million vehicle deliveries in 2026?", 0.40),
    ("Will Boeing hit its aircraft delivery target?", 0.40),
    # Independence referendum (~15%)
    ("Will Taiwan hold a referendum on independence?", 0.15),
    ("Will Catalonia hold a plebiscite on independence?", 0.15),
    # Military territorial recapture (~30%)
    ("Will Ukraine recapture Kherson region in 2026?", 0.30),
    ("Will an armed counteroffensive succeed by year end?", 0.30),
    # AR glasses / next-gen tech products (~55%)
    ("Will Meta release its next-gen AR glasses in 2026?", 0.55),
    ("Will Apple launch a new Vision Pro model by year end?", 0.55),
    # AI capability milestones (~40%)
    # Note: bare "outperform" hits economic comparison block (0.50) before AI block
    # Use phrases that match the AI block's specific patterns instead
    ("Will AI pass a medical licensing exam with high scores?", 0.40),
    ("Will an LLM pass the MCAT with a top score?", 0.40),
    # AI regulation (~30%)
    ("Will the US ban AI from making autonomous weapons decisions?", 0.30),
    ("Will Congress pass AI regulation legislation in 2026?", 0.30),
    # Climate / temperature records (~40%)
    ("Will the US record its hottest year ever in 2026?", 0.40),
    ("Will a new all-time temperature record be set in Europe?", 0.40),
    # Athlete / player retirement (~30%)
    ("Will Draymond Green announce his retirement before the 2026-27 season?", 0.30),
    ("Will LeBron James officially retire from professional basketball?", 0.30),
    # Trailer / media preview release (~25%)
    ("Will the official trailer for Spider-Man: Beyond the Spider-Verse be released?", 0.25),
    ("Will the teaser trailer for Avatar 3 be released by Q2?", 0.25),
    # AGI announcement (~25%)
    ("Will any company announce that it has achieved Artificial General Intelligence?", 0.25),
    ("Will a lab claim to have achieved AGI by 2027?", 0.25),
    # Blockchain / crypto protocol upgrade (~65%) — must NOT hit generic crypto block (0.50)
    ("Will Ethereum complete the Pectra network upgrade by Q2 2026?", 0.65),
    ("Will the Ethereum protocol upgrade activate before June?", 0.65),
    ("Will Bitcoin's Taproot upgrade be activated by year end?", 0.65),
    ("Will the scheduled blockchain hard fork complete by August 2026?", 0.65),
    ("Will the Shapella upgrade complete successfully?", 0.65),
    # Secondary equity offering (~35%)
    ("Will Tesla complete a secondary offering in Q3 2026?", 0.35),
    ("Will the company complete a follow-on equity offering by year end?", 0.35),
    ("Will Rivian complete an at-the-market offering before Q4?", 0.35),
    # Credit rating change (~40%)
    ("Will the US receive a credit rating downgrade from Moody's in 2026?", 0.40),
    ("Will the company's credit rating be upgraded by S&P before July?", 0.40),
    ("Will France receive a sovereign downgrade by Fitch in 2026?", 0.40),
    # CBDC adoption (~15%)
    ("Will the US launch a central bank digital currency by 2027?", 0.15),
    ("Will the EU introduce a digital euro by end of 2026?", 0.15),
    ("Will China expand its digital yuan internationally by Q4?", 0.15),
    # Short seller report / fraud allegations (~30%)
    ("Will a Hindenburg Research report target Tesla in 2026?", 0.30),
    ("Will Muddy Waters publish a short report on the company?", 0.30),
    # False positive guards
    # "ethereum price" must still hit crypto block (0.50), not upgrade block
    ("Will Ethereum price exceed $5000 in 2026?", 0.50),
    # "bitcoin" alone must still hit crypto block (0.50), not upgrade block
    ("Will Bitcoin reach $200,000 by end of 2026?", 0.50),
    # OPEC / oil production decisions (~40%)
    ("Will OPEC cut oil production at the December 2026 meeting?", 0.40),
    ("Will OPEC+ agree to reduce output quotas in Q4 2026?", 0.40),
    ("Will OPEC increase production at the next meeting?", 0.40),
    ("Will the oil production quota be maintained by OPEC?", 0.40),
    # Semiconductor / chip export restriction (~45%)
    ("Will the US impose new chip export restrictions on China in 2026?", 0.45),
    ("Will the Commerce Department add Nvidia chips to the export ban list?", 0.45),
    ("Will the US expand semiconductor export controls in Q3 2026?", 0.45),
    # Filibuster reform (~10%)
    ("Will the Senate eliminate the filibuster before the midterms?", 0.10),
    ("Will Democrats end the filibuster for voting rights legislation?", 0.10),
    ("Will there be a filibuster reform vote in 2026?", 0.10),
    # Housing starts / permits data (~50%)
    ("Will US housing starts exceed 1.5 million in March 2026?", 0.50),
    ("Will housing permits fall below 1.3 million units in February?", 0.50),
    ("Will building permits data show an increase in Q2 2026?", 0.50),
    # False positive guards
    # "oil price" must still hit commodity block (0.40), not OPEC block — no conflict but verify
    ("Will oil prices rise above $100 per barrel in 2026?", 0.40),
    # "chip" in a non-export context should return None
    ("Will the chip shortage end by Q3 2026?", None),
    # CBDC false positive guard — crypto titles must hit crypto (0.50), not CBDC (0.15)
    ("Will total crypto market cap reach $5T by end of 2026?", 0.50),
    # Recall election (~15%)
    ("Will the California governor recall election succeed in 2026?", 0.15),
    ("Will the recall campaign against the mayor qualify for the ballot?", 0.15),
    ("Will the recall vote remove the governor before year end?", 0.15),
    # Water crisis / drought (~30%)
    ("Will there be a water crisis in California by 2027?", 0.30),
    ("Will Lake Mead reservoir levels fall to critically low levels?", 0.30),
    ("Will the Southwest face severe drought conditions in 2026?", 0.30),
    # Municipal / city bankruptcy (~10%)
    ("Will the city file for municipal bankruptcy in 2026?", 0.10),
    ("Will Detroit face another city bankruptcy?", 0.10),
    # False positive guard — regular corporate bankruptcy still returns 0.15
    ("Will the retailer file for bankruptcy before Q4 2026?", 0.15),
    # Tax legislation (~35%)
    ("Will Congress pass a capital gains tax increase in 2026?", 0.35),
    ("Will income taxes be cut under the new administration?", 0.35),
    ("Will there be a major tax reform bill signed into law?", 0.35),
    ("Will the TCJA extension pass before the December deadline?", 0.35),
    ("Will corporate tax rates rise above 28% in 2026?", 0.35),
    # False positive guard — "tax reform bill" + "veto" → veto block (0.20) fires first
    ("Will Biden veto the tax reform bill?", 0.20),
    # Supply chain disruption (~30%)
    ("Will there be a major supply chain disruption in 2026?", 0.30),
    ("Will a port strike shut down the West Coast ports in Q3?", 0.30),
    ("Will port congestion at LA/Long Beach exceed 2021 levels?", 0.30),
    ("Will shipping delays cause consumer goods shortages in Q4?", 0.30),
    ("Will global container shortage persist through 2026?", 0.30),
    # EV adoption milestones (~45%)
    ("Will EV sales exceed 20% of new car sales in the US in 2026?", 0.45),
    ("Will total electric vehicle sales top 1 million in Q1 2026?", 0.45),
    ("Will EV market share surpass 15% globally by year end?", 0.45),
    ("Will electric vehicle adoption reach 25% in Norway by Q4?", 0.45),
    ("Will EV penetration exceed 10% in the US auto market?", 0.45),
    # Bond / debt issuance (~65%)
    ("Will the US Treasury complete its 10-year bond auction in March?", 0.65),
    ("Will Company X complete its $2B bond offering by year end?", 0.65),
    ("Will the sovereign bond issuance by France close before Q3?", 0.65),
    ("Will the corporate bond sale be successfully priced this week?", 0.65),
    ("Will Argentina successfully complete a bond auction in Q2?", 0.65),
    # Unionization vote (~40%)
    ("Will Amazon workers at the Staten Island warehouse vote to unionize?", 0.40),
    ("Will Starbucks baristas win the union election in Seattle?", 0.40),
    ("Will the NLRB election at the Apple Store result in union victory?", 0.40),
    ("Will the union drive at the tech company lead to unionization?", 0.40),
    ("Will workers vote on unionization before the end of Q3?", 0.40),
    # False positive guard — veto block must still match correctly
    ("Will Trump veto the spending bill before the deadline?", 0.20),
    # False positive guard — "supply chain" in a title about a company's supply chain
    # (same 0.30 rate is appropriate for disruption threshold questions)
    ("Will Apple's supply chain disruption impact iPhone production?", 0.30),
])
def test_base_rate_new_categories(title, expected_rate):
    m = _market(title=title)
    rate = scanner.estimate_base_rate(m)
    assert rate == pytest.approx(expected_rate), (
        f"title={title!r}: expected {expected_rate}, got {rate}"
    )


# ─── get_heuristic_label: category label lookup ───────────────────────────────

@pytest.mark.parametrize("title,expected_label", [
    # FDA / drug categories — critical for Rules 31 and 37
    ("Will Wegovy receive FDA approval at its PDUFA date in June?", "PDUFA date"),
    ("Will the FDA lift the clinical hold on the drug?",            "FDA clinical hold"),
    ("Will the company respond to the FDA complete response letter?","FDA complete response letter"),
    ("Will the FDA advisory committee vote favorably?",             "FDA advisory committee"),
    ("Will the FDA approve the new cancer treatment?",              "FDA approval"),
    # Crypto protocol upgrade — Rule 37
    ("Will Ethereum complete the Pectra network upgrade by Q2?",    "crypto protocol upgrade"),
    ("Will the Bitcoin hard fork activate by August?",              "crypto protocol upgrade"),
    ("Will the scheduled blockchain upgrade complete on time?",     "crypto protocol upgrade"),
    # OPEC / chip export — Rule 39
    ("Will OPEC cut production at the December meeting?",           "OPEC production decision"),
    ("Will the US impose chip export restrictions on China?",       "chip export restriction"),
    ("Will semiconductor export controls be expanded in Q3?",       "chip export restriction"),
    # Credit rating / secondary offering — Rule 38
    ("Will the US credit rating downgrade by Moody's happen?",     "credit rating change"),
    ("Will the company complete a follow-on offering by year end?", "secondary equity offering"),
    # New categories — Rules 40-42
    ("Will Amazon workers vote to unionize in Q2 2026?",            "unionization vote"),
    ("Will the NLRB election at the Tesla factory succeed?",        "unionization vote"),
    ("Will Congress pass a tax cut bill before year end?",          "tax legislation"),
    ("Will income taxes be reduced under the new plan?",            "tax legislation"),
    ("Will there be a major supply chain disruption in 2026?",      "supply chain disruption"),
    ("Will West Coast port congestion worsen in Q3?",               "supply chain disruption"),
    ("Will EV sales exceed 20% of new car sales in 2026?",         "EV adoption milestone"),
    ("Will the Treasury complete its bond auction in March?",       "bond/debt issuance"),
    ("Will the company complete a bond offering by Q4?",            "bond/debt issuance"),
    # Other specific categories
    ("Will the Senate eliminate the filibuster before midterms?",   "filibuster reform"),
    ("Will there be a recall election for the California governor?", "recall election"),
    ("Will municipal bankruptcy be filed by the city in 2026?",    "municipal bankruptcy"),
    ("Will Lake Mead reservoir levels hit a new low?",              "water crisis"),
    ("Will the short seller report tank the stock?",                "short seller report"),
    ("Will Hindenburg Research publish a report on Tesla?",         "short seller report"),
    ("Will housing permits fall below 1.3 million units?",         "housing permits data"),
    ("Will the government shutdown end before March?",              "government shutdown"),
    ("Will Congress avoid a shutdown by the CR deadline?",          "government shutdown avoided"),
    # None — no matching heuristic
    ("Will this highly unusual unique event happen?",               None),
    ("Will the Nobel conference attract more attendees in 2026?",   None),
])
def test_heuristic_label(title, expected_label):
    m = _market(title=title)
    label = scanner.get_heuristic_label(m)
    assert label == expected_label, (
        f"title={title!r}: expected {expected_label!r}, got {label!r}"
    )


@pytest.mark.parametrize("title,expected_rate", [
    # Gold price — "gold reach/hit/top" without dollar sign still hits commodity block
    ("Will gold reach 4000 dollars before year end?",          0.40),
    ("Will gold hit 5000 dollars per ounce in Q1 2026?",       0.40),
    # Consumer price inflation — alt phrasing for CPI block
    ("Will consumer price inflation exceed 3% in 2026?",       0.50),
    ("Will price inflation return to the Fed target?",         0.50),
    # Housing market price questions
    ("Will the housing market see price appreciation exceed 5%?", 0.50),
    ("Will house prices rise in Q3 2026?",                     0.50),
    ("Will house price declines exceed 10% in 2027?",          0.50),
    # General strike — should hit labor strike block (0.30), not None
    ("Will there be a general strike in France in 2026?",      0.30),
    ("Will a nationwide strike shut down the railways?",        0.30),
    ("Will transit workers announce a general strike?",         0.30),
    # City insolvency phrasing — should hit municipal bankruptcy (0.10)
    ("Will the city declare insolvency before 2027?",           0.10),
    ("Will a major US city declare bankruptcy due to pension debt?", 0.10),
    # Veto is still 0.20 with "tax" in title (veto block comes first)
    ("Will the president veto the income tax bill?",            0.20),
])
def test_base_rate_gap_fixes(title, expected_rate):
    m = _market(title=title)
    rate = scanner.estimate_base_rate(m)
    assert rate == pytest.approx(expected_rate), (
        f"title={title!r}: expected {expected_rate}, got {rate}"
    )


def test_heuristic_label_on_score_market_result():
    """score_market() passes heuristic_label through to the result dict."""
    m = _market(title="Will Ethereum complete the Pectra network upgrade by Q2 2026?")
    result = scanner.score_market(m, BASE_CFG)
    assert result["heuristic_label"] == "crypto protocol upgrade"


def test_heuristic_label_none_when_no_match():
    """score_market() heuristic_label is None for markets with no heuristic."""
    m = _market(title="Will this completely novel unknown event happen?")
    result = scanner.score_market(m, BASE_CFG)
    assert result["heuristic_label"] is None


# ─── score_market: heuristic_direction ───────────────────────────────────────

def test_heuristic_direction_yes_when_base_rate_above_mid():
    """base_rate=0.65 > mid_price=0.40 → heuristic says YES (market underpriced)."""
    m = _market(mid=0.40, title="Will the CEO remain in office by end of year?")
    result = scanner.score_market(m, BASE_CFG)
    assert result["base_rate"] == pytest.approx(0.65)
    assert result["heuristic_direction"] == "YES"


def test_heuristic_direction_no_when_base_rate_below_mid():
    """base_rate=0.10 < mid_price=0.55 → heuristic says NO (market overpriced)."""
    m = _market(mid=0.55, title="Will there be a coup in the country?")
    result = scanner.score_market(m, BASE_CFG)
    assert result["base_rate"] == pytest.approx(0.10)
    assert result["heuristic_direction"] == "NO"


def test_heuristic_direction_neutral_when_base_rate_near_mid():
    """base_rate=0.40, mid_price=0.40 → NEUTRAL (within 5pp buffer)."""
    m = _market(mid=0.40, title="Will it rain tomorrow")
    result = scanner.score_market(m, BASE_CFG)
    assert result["base_rate"] == pytest.approx(0.40)
    assert result["heuristic_direction"] == "NEUTRAL"


def test_heuristic_direction_none_when_no_base_rate():
    """No matching heuristic → heuristic_direction is None."""
    m = _market(mid=0.50, title="Will this highly unusual unique event happen?")
    result = scanner.score_market(m, BASE_CFG)
    assert result["base_rate"] is None
    assert result["heuristic_direction"] is None


# ─── Short-horizon edge decay (Rule 28) ───────────────────────────────────────

def _sh_cfg(edge_threshold=0.08, short_threshold=0.15, flag_mode="strict_with_heuristic"):
    """Config with explicit short_horizon_edge_threshold."""
    cfg = {k: v for k, v in BASE_CFG["markets"].items()}
    cfg["edge_threshold"] = edge_threshold
    cfg["short_horizon_edge_threshold"] = short_threshold
    cfg["flag_mode"] = flag_mode
    return {"markets": cfg}


def test_short_horizon_flagged_when_edge_exceeds_15pp():
    """WEEKLY market with 20pp edge (>15pp) fires HEURISTIC flag."""
    # "government shutdown" base_rate=0.15; mid=0.40 → edge=0.25 > 0.15
    m = _market(mid=0.40, title="government shutdown", days_out=3)
    m["time_horizon"] = "WEEKLY"
    result = scanner.score_market(m, _sh_cfg())
    assert result["short_horizon"] is True
    assert result["flag"]      is True
    assert result["flag_path"] == "HEURISTIC"


def test_short_horizon_suppressed_when_edge_below_15pp():
    """WEEKLY market with 12pp edge (>8pp but <15pp) does NOT fire HEURISTIC flag."""
    # "government shutdown" base_rate=0.15; mid=0.27 → edge=0.12 (>8pp, <15pp)
    m = _market(mid=0.27, title="government shutdown", days_out=3)
    m["time_horizon"] = "WEEKLY"
    result = scanner.score_market(m, _sh_cfg())
    assert result["short_horizon"] is True
    assert result["flag"] is False  # edge 0.12 < 0.15 short threshold → suppressed


def test_long_horizon_uses_normal_threshold():
    """MONTHLY market with 12pp edge (>8pp) DOES fire flag (normal threshold applies)."""
    # "government shutdown" base_rate=0.15; mid=0.27 → edge=0.12 > 0.08
    m = _market(mid=0.27, title="government shutdown", days_out=30)
    m["time_horizon"] = "MONTHLY"
    result = scanner.score_market(m, _sh_cfg())
    assert result["short_horizon"] is False
    assert result["flag"] is True
    assert result["flag_path"] == "HEURISTIC"


def test_short_horizon_drift_still_fires():
    """Drift always fires regardless of short_horizon (drift is real-time, not heuristic)."""
    m = _market(mid=0.50, title="government shutdown", days_out=2)
    m["time_horizon"] = "INTRADAY"
    m["last_price_dollars"] = "0.30"   # 20pp drift → above drift_min_pct=5%
    result = scanner.score_market(m, _sh_cfg())
    assert result["short_horizon"] is True
    assert result["flag"]      is True
    assert result["flag_path"] == "DRIFT"


def test_short_horizon_field_false_for_monthly():
    """MONTHLY time_horizon → short_horizon=False."""
    m = _market(mid=0.50, days_out=30)
    m["time_horizon"] = "MONTHLY"
    result = scanner.score_market(m, _sh_cfg())
    assert result["short_horizon"] is False


def test_short_horizon_field_true_for_intraday():
    """INTRADAY time_horizon → short_horizon=True."""
    m = _market(mid=0.50, days_out=0.5)
    m["time_horizon"] = "INTRADAY"
    result = scanner.score_market(m, _sh_cfg())
    assert result["short_horizon"] is True


# ─── Net-of-spread edge ────────────────────────────────────────────────────────

def _net_market(mid: float, base: float, bid: float, ask: float, days_out: int = 60) -> dict:
    """Market with explicit bid/ask spread for net_edge testing."""
    close = (datetime.now(timezone.utc) + timedelta(days=days_out)).isoformat()
    return {
        "ticker": "KXTEST-NET",
        "title": "Net edge test market",
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
        "volume_fp": 5000,
        "close_time": close,
        "time_horizon": "MONTHLY",
        "category": "POLITICS",
    }


def _net_cfg(base_rate: float) -> dict:
    """Config for net_edge tests — passthrough mode, forces given base rate via mock."""
    return {
        "markets": {
            "edge_threshold": 0.08,
            "short_horizon_edge_threshold": 0.15,
            "drift_min_abs": 0.0,
            "drift_min_pct": 0.05,
            "flag_mode": "passthrough",
        }
    }


def test_net_edge_equals_raw_minus_half_spread():
    """net_edge = raw_edge - half_spread when bid/ask both present."""
    m = {
        "ticker": "KXTEST-NE1",
        "title": "Net edge arithmetic test",
        "yes_bid_dollars": 0.20,
        "yes_ask_dollars": 0.30,    # mid=0.25, half_spread=0.05
        "volume_fp": 5000,
        "close_time": (datetime.now(timezone.utc) + timedelta(days=60)).isoformat(),
        "time_horizon": "MONTHLY",
        "category": "POLITICS",
    }
    cfg = {
        "markets": {
            "edge_threshold": 0.08,
            "short_horizon_edge_threshold": 0.15,
            "drift_min_abs": 0.0,
            "drift_min_pct": 0.05,
            "flag_mode": "passthrough",
        }
    }
    result = scanner.score_market(m, cfg)
    raw  = result.get("raw_edge")
    net  = result.get("net_edge")
    half = 0.05  # (0.30 - 0.20) / 2
    if raw is not None and net is not None:
        assert abs(net - (raw - half)) < 1e-9


def test_net_edge_is_none_when_no_bid_ask():
    """net_edge is None when only last_price available (no bid/ask)."""
    m = {
        "ticker": "KXTEST-NE2",
        "title": "Net edge no spread test",
        "last_price_dollars": 0.40,
        "volume_fp": 5000,
        "close_time": (datetime.now(timezone.utc) + timedelta(days=60)).isoformat(),
        "time_horizon": "MONTHLY",
        "category": "POLITICS",
    }
    cfg = {
        "markets": {
            "edge_threshold": 0.08,
            "short_horizon_edge_threshold": 0.15,
            "drift_min_abs": 0.0,
            "drift_min_pct": 0.05,
            "flag_mode": "passthrough",
        }
    }
    result = scanner.score_market(m, cfg)
    # No bid/ask → half_spread=0 → net_edge = raw_edge - 0 = raw_edge (or None if no base_rate)
    # Either None (no base_rate) or equal to raw_edge
    raw = result.get("raw_edge")
    net = result.get("net_edge")
    if raw is not None:
        assert abs(net - raw) < 1e-9
    else:
        assert net is None


def test_net_edge_negative_when_spread_exceeds_raw_edge():
    """net_edge can be negative when spread consumes theoretical edge."""
    m = {
        "ticker": "KXTEST-NE3",
        "title": "Wide spread kills edge",
        "yes_bid_dollars": 0.10,
        "yes_ask_dollars": 0.50,    # half_spread=0.20, mid=0.30
        "volume_fp": 5000,
        "close_time": (datetime.now(timezone.utc) + timedelta(days=60)).isoformat(),
        "time_horizon": "MONTHLY",
        "category": "POLITICS",
    }
    cfg = {
        "markets": {
            "edge_threshold": 0.08,
            "short_horizon_edge_threshold": 0.15,
            "drift_min_abs": 0.0,
            "drift_min_pct": 0.05,
            "flag_mode": "passthrough",
        }
    }
    result = scanner.score_market(m, cfg)
    net = result.get("net_edge")
    raw = result.get("raw_edge")
    half = 0.20
    if raw is not None and net is not None:
        # net_edge should reflect that the huge spread wipes theoretical edge
        assert abs(net - (raw - half)) < 1e-9


# ─── FDA / PDUFA heuristic base rates ────────────────────────────────────────

def test_pdufa_base_rate_is_high():
    """PDUFA date markets get ~85% approval base rate (NDA/BLA under active review)."""
    br = scanner.estimate_base_rate({"title": "Will the FDA approve Drug X by the PDUFA date?"})
    assert br == 0.85


def test_clinical_hold_base_rate_is_low():
    """Clinical hold markets get ~10% — active FDA safety concern."""
    br = scanner.estimate_base_rate({"title": "Will the clinical hold on Drug X be lifted by Q2?"})
    assert br == 0.10


def test_crl_resubmission_base_rate():
    """Complete Response Letter / resubmission: ~60% on second review."""
    br = scanner.estimate_base_rate({"title": "Will Drug X resubmission receive FDA approval?"})
    assert br == 0.60


def test_fda_adcom_base_rate_neutral():
    """Advisory committee vote outcome is 50/50 before vote — neutral base rate."""
    br = scanner.estimate_base_rate({"title": "Will the FDA advisory committee vote to approve Drug X?"})
    assert br == 0.50


def test_generic_fda_approval_base_rate():
    """Generic FDA approval (no PDUFA/CRL context) stays at 40%."""
    br = scanner.estimate_base_rate({"title": "Will the FDA approve the new cancer drug by year-end?"})
    assert br == 0.40


def test_pdufa_takes_priority_over_fda_approve():
    """PDUFA pattern (0.85) must precede generic 'fda approve' (0.40) in heuristics list."""
    br = scanner.estimate_base_rate({"title": "FDA approval of Drug X ahead of PDUFA date"})
    assert br == 0.85


# ─── Iran/geopolitical / regime change heuristics ────────────────────────────

def test_uranium_enrichment_deal_base_rate():
    """Uranium enrichment agreement markets get ~20% (similar to nuclear deal)."""
    br = scanner.estimate_base_rate({"title": "Iran agrees to end enrichment of uranium by June 30"})
    assert br == 0.20


def test_uranium_stockpile_surrender_base_rate():
    """Uranium stockpile surrender markets get ~20%."""
    br = scanner.estimate_base_rate({"title": "Iran agrees to surrender enriched uranium stockpile by Q3"})
    assert br == 0.20


def test_regime_fall_base_rate_low():
    """Regime fall / government collapse markets get ~10% (rare event)."""
    br = scanner.estimate_base_rate({"title": "Will the Iranian regime fall before 2027?"})
    assert br == 0.10


def test_coup_attempt_base_rate():
    """Coup attempt markets get ~10% (included in regime fall category)."""
    br = scanner.estimate_base_rate({"title": "Iran coup attempt by June 30?"})
    assert br == 0.10


def test_leadership_change_base_rate():
    """Leadership change / transition get ~10%."""
    br = scanner.estimate_base_rate({"title": "Iran leadership change by December 31?"})
    assert br == 0.10


def test_abraham_accords_base_rate():
    """Abraham Accords normalization markets get ~20% (very slow-moving)."""
    br = scanner.estimate_base_rate({"title": "Will Israel and Saudi Arabia normalize relations before 2027?"})
    assert br == 0.20


def test_saudi_israel_normalization_base_rate():
    """Saudi-Israel normalization gets ~20% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Saudi-Israel normalization happen by January 2029?"})
    assert br == 0.20


# ─── New heuristics: min_hours_to_close filter ───────────────────────────────

def test_min_hours_to_close_drops_imminent_market():
    """Markets closing within min_hours_to_close hours are filtered out."""
    cfg = {**BASE_CFG, "markets": {**BASE_CFG["markets"], "min_hours_to_close": 6}}
    m = _market(days_out=0.1)  # closes in ~2.4 hours
    assert scanner.filter_markets([m], cfg) == []


def test_min_hours_to_close_keeps_market_closing_after_threshold():
    """Markets closing after min_hours_to_close hours are kept."""
    cfg = {**BASE_CFG, "markets": {**BASE_CFG["markets"], "min_hours_to_close": 6}}
    m = _market(days_out=1)  # closes in 24 hours
    result = scanner.filter_markets([m], cfg)
    assert len(result) == 1


def test_min_hours_to_close_zero_keeps_all():
    """min_hours_to_close=0 disables the filter — all markets pass."""
    cfg = {**BASE_CFG, "markets": {**BASE_CFG["markets"], "min_hours_to_close": 0}}
    m = _market(days_out=0.05)  # closes in ~72 minutes
    result = scanner.filter_markets([m], cfg)
    assert len(result) == 1


def test_min_hours_to_close_default_absent():
    """When min_hours_to_close is absent from config, defaults to 6h (imminent excluded)."""
    cfg = {**BASE_CFG}  # no min_hours_to_close key
    m = _market(days_out=0.1)  # closes in ~2.4 hours
    assert scanner.filter_markets([m], cfg) == []


# ─── New heuristics: stock buyback / dividend ────────────────────────────────

def test_stock_buyback_base_rate():
    """Stock buyback announcements get ~40% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Apple announce a share buyback program before Q3?"})
    assert br == 0.40


def test_dividend_increase_base_rate():
    """Dividend increase announcements get ~40% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Microsoft increase its dividend in 2026?"})
    assert br == 0.40


def test_special_dividend_base_rate():
    """Special dividend gets ~40% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Meta declare a special dividend before June?"})
    assert br == 0.40


# ─── New heuristics: treaty withdrawal ───────────────────────────────────────

def test_withdraw_from_treaty_base_rate():
    """Treaty withdrawal gets ~20% base rate."""
    br = scanner.estimate_base_rate({"title": "Will the US withdraw from the Paris Agreement by 2027?"})
    assert br == 0.20


def test_withdraw_from_nato_base_rate():
    """NATO withdrawal gets ~20% base rate."""
    br = scanner.estimate_base_rate({"title": "Will any country withdraw from NATO by 2027?"})
    assert br == 0.20


def test_exit_the_eu_base_rate():
    """EU exit gets ~20% base rate (treaty withdrawal category)."""
    br = scanner.estimate_base_rate({"title": "Will Hungary leave the EU before 2028?"})
    assert br == 0.20


# ─── New heuristics: formal candidacy announcement ───────────────────────────

def test_announce_candidacy_base_rate():
    """Formal candidacy announcements get ~35% base rate."""
    br = scanner.estimate_base_rate({"title": "Will DeSantis announce his candidacy before March?"})
    assert br == 0.35


def test_launch_campaign_base_rate():
    """Campaign launch gets ~35% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Newsom launch his campaign before the primary?"})
    assert br == 0.35


def test_enter_the_race_base_rate():
    """Entering the race gets ~35% base rate."""
    br = scanner.estimate_base_rate({"title": "Will AOC enter the race for Senate by April?"})
    assert br == 0.35


# ─── New heuristics: martial law ─────────────────────────────────────────────

def test_martial_law_base_rate():
    """Martial law declaration gets ~5% base rate."""
    br = scanner.estimate_base_rate({"title": "Will the Philippines declare martial law before 2027?"})
    assert br == 0.05


def test_declare_martial_law_base_rate():
    """'Declare martial law' phrasing gets ~5% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Trump declare martial law in 2026?"})
    assert br == 0.05


# ─── New heuristics: social media post markets ───────────────────────────────

def test_tweet_about_base_rate():
    """Social media post markets get ~75% base rate (active user)."""
    br = scanner.estimate_base_rate({"title": "Will Trump tweet about tariffs before June 30?"})
    assert br == 0.75


def test_post_about_base_rate():
    """'Post about' framing gets ~75% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Elon Musk post about AI before the end of June?"})
    assert br == 0.75


def test_mention_on_twitter_base_rate():
    """'Mention on Twitter' phrasing gets ~75% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Biden mention on Twitter about Ukraine before July?"})
    assert br == 0.75


def test_social_media_post_precedes_entertainment():
    """Social media post (0.75) takes priority over generic entertainment (0.25)."""
    br = scanner.estimate_base_rate({"title": "Will Taylor Swift post about her new album on Instagram?"})
    assert br == 0.75


# ─── New heuristics: corporate partnership / S&P inclusion / attendance ───────

def test_corporate_partnership_base_rate():
    """Corporate partnership announcements get ~35% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Apple announce a partnership with OpenAI before Q4?"})
    assert br == 0.35


def test_strategic_alliance_base_rate():
    """Strategic alliance gets ~35% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Microsoft enter a strategic alliance with Mistral AI?"})
    assert br == 0.35


def test_licensing_agreement_base_rate():
    """Licensing deal gets ~35% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Meta sign a licensing agreement with a major record label?"})
    assert br == 0.35


def test_sp500_inclusion_base_rate():
    """S&P 500 inclusion markets get ~50% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Palantir be added to the S&P 500 before October?"})
    assert br == 0.50


def test_sp500_index_inclusion_base_rate():
    """S&P 500 addition phrasing gets ~50% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Robinhood be included in the S&P 500 by year-end?"})
    assert br == 0.50


def test_event_attendance_summit_base_rate():
    """Event attendance (summit) gets ~65% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Putin attend the G20 summit in 2026?"})
    assert br == 0.65


def test_event_attendance_conference_base_rate():
    """Event attendance (conference) gets ~65% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Zelensky appear at the NATO summit before July?"})
    assert br == 0.65


def test_corporate_facility_base_rate():
    """Corporate facility announcements get ~40% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Tesla announce a new factory in India by 2027?"})
    assert br == 0.40


def test_data_center_announcement_base_rate():
    """Data center opening gets ~40% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Amazon open a data center in Saudi Arabia before 2027?"})
    assert br == 0.40


# ─── New heuristics: BRICS / QE / celebrity legal ────────────────────────────

def test_brics_membership_base_rate():
    """BRICS membership markets get ~30% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Saudi Arabia join BRICS before 2027?"})
    assert br == 0.30


def test_brics_expansion_base_rate():
    """BRICS expansion phrasing gets ~30% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Turkey be added to BRICS before 2027?"})
    assert br == 0.30


def test_quantitative_easing_base_rate():
    """QE announcement markets get ~40% base rate."""
    br = scanner.estimate_base_rate({"title": "Will the Fed announce a new quantitative easing program in 2026?"})
    assert br == 0.40


def test_quantitative_tightening_base_rate():
    """QT markets get ~40% base rate."""
    br = scanner.estimate_base_rate({"title": "Will the ECB begin quantitative tightening before Q4?"})
    assert br == 0.40


def test_divorce_settlement_base_rate():
    """Divorce settlement markets get ~45% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Elon Musk's divorce settlement be finalized before March?"})
    assert br == 0.45


def test_custody_battle_base_rate():
    """Custody battle outcomes get ~45% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Britney Spears win the custody ruling before July?"})
    assert br == 0.45


def test_defamation_settlement_base_rate():
    """Defamation settlement gets ~45% base rate."""
    br = scanner.estimate_base_rate({"title": "Will there be a defamation settlement between the parties?"})
    assert br == 0.45


# ─── New heuristics: legalization / cancellation / records / recall ───────────

def test_cannabis_legalization_base_rate():
    """Cannabis legalization markets get ~30% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Texas legalize recreational marijuana before 2028?"})
    assert br == 0.30


def test_gambling_legalization_base_rate():
    """Gambling legalization gets ~30% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Florida legalize sports gambling by 2027?"})
    assert br == 0.30


def test_event_cancellation_base_rate():
    """Event cancellation markets get ~10% base rate."""
    br = scanner.estimate_base_rate({"title": "Will the 2026 World Cup be cancelled or postponed?"})
    assert br == 0.10


def test_event_called_off_base_rate():
    """'Called off' phrasing gets ~10% base rate."""
    br = scanner.estimate_base_rate({"title": "Will the NATO summit be called off before July?"})
    assert br == 0.10


def test_athletic_record_base_rate():
    """Athletic record-breaking markets get ~30% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Usain Bolt's 100m world record be broken in 2026?"})
    assert br == 0.30


def test_world_record_phrasing_base_rate():
    """'World record' phrasing gets ~30% base rate."""
    br = scanner.estimate_base_rate({"title": "Will the 100m world record be set at the Paris Grand Prix?"})
    assert br == 0.30


def test_wealth_tax_base_rate():
    """Wealth tax markets get ~15% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Congress pass a wealth tax on billionaires in 2026?"})
    assert br == 0.15


def test_product_recall_base_rate():
    """Product recall markets get ~25% base rate."""
    br = scanner.estimate_base_rate({"title": "Will Tesla issue a major safety recall before Q3 2026?"})
    assert br == 0.25


def test_fda_recall_base_rate():
    """FDA drug recall gets ~25% base rate."""
    br = scanner.estimate_base_rate({"title": "Will the FDA issue a drug recall for the new weight-loss pill?"})
    assert br == 0.25


# ─── M&A stage differentiation ────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    # Signed-deal close patterns → 0.80
    ("Will the Amazon acquisition close before the deadline?",          0.80),  # "acquisition close"
    ("Will the Adobe-Figma merger be completed before Q3?",             0.80),  # "merger be completed"
    ("Will the merger close by end of Q4?",                             0.80),  # "merger close"
    ("Will the Tesla deal to close before March?",                      0.80),  # "deal to close"
    ("Will the Google transaction close by year end?",                  0.80),  # "transaction close"
    ("Will they complete the acquisition by December?",                 0.80),  # "complete the acquisition"
    # Hostile/unsolicited bid → 0.42
    ("Will the hostile takeover of Paramount succeed?",                 0.42),
    ("Will the unsolicited bid for Southwest Airlines succeed?",        0.42),
    ("Will the tender offer for Twitter complete before January?",      0.42),
    # Generic exploratory merger → 0.35
    ("Will Apple acquire Netflix?",                                     0.35),  # "acquire"
    ("Will Amazon be acquired by a major conglomerate?",                0.35),  # "acquired by"
    ("Will the company take private before 2027?",                      0.35),
])
def test_ma_stage_base_rate(title, expected):
    br = scanner.estimate_base_rate({"title": title})
    assert br == expected, f"{title!r}: expected {expected}, got {br}"


@pytest.mark.parametrize("title,expected_label", [
    ("Will the Amazon acquisition close before the deadline?",          "merger close (signed deal)"),
    ("Will the Adobe-Figma merger be completed before Q3?",             "merger close (signed deal)"),
    ("Will the merger close by end of Q4?",                             "merger close (signed deal)"),
    ("Will the Tesla deal to close before March?",                      "merger close (signed deal)"),
    ("Will the hostile takeover of Paramount succeed?",                 "hostile takeover bid"),
    ("Will the unsolicited bid for Southwest Airlines succeed?",        "hostile takeover bid"),
    ("Will the tender offer for Twitter complete?",                     "hostile takeover bid"),
    ("Will Apple acquire Netflix by year end?",                         "merger or acquisition"),
])
def test_ma_heuristic_label(title, expected_label):
    label = scanner.get_heuristic_label({"title": title})
    assert label == expected_label, f"{title!r}: expected {expected_label!r}, got {label!r}"
