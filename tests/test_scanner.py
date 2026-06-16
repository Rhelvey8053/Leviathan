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
