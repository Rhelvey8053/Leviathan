"""
tests/test_forecastbench_pilot.py -- offline tests for analysis/
forecastbench_pilot.py's pure logic (question->market mapping, Brier
grading). No network calls, no Kalshi API, no Claude CLI -- build_pilot_
markets()/score_pilot_markets() are network/CLI-dependent by design and
are exercised by the module's own --dry-run and live run instead, the
same split test_eval_rescore.py/test_replay_runner.py already use for
their own live-only paths.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis import forecastbench_pilot as fbp


# ─── _to_market_dict ────────────────────────────────────────────────────────

def _question(**overrides):
    q = {
        "id": "KXTEST-26",
        "question": "Will X happen?",
        "freeze_datetime_value": "0.65",
        "market_info_close_datetime": "2026-09-01T00:00:00Z",
        "freeze_datetime": "2026-08-20T00:00:00Z",
    }
    q.update(overrides)
    return q


def test_to_market_dict_maps_fields_correctly():
    m = fbp._to_market_dict(_question(), 1.0, "2026-09-05")
    assert m["ticker"] == "KXTEST-26"
    assert m["title"] == "Will X happen?"
    assert m["mid_price"] == 0.65
    assert m["close_time"] == "2026-09-01T00:00:00Z"
    assert m["_ground_truth"] == 1.0
    assert m["_resolution_date"] == "2026-09-05"


def test_to_market_dict_none_when_price_missing():
    assert fbp._to_market_dict(_question(freeze_datetime_value=None), 1.0, None) is None


def test_to_market_dict_none_when_price_not_numeric():
    assert fbp._to_market_dict(_question(freeze_datetime_value="N/A"), 1.0, None) is None


def test_to_market_dict_none_when_price_at_or_outside_0_1_bounds():
    """0.0 and 1.0 themselves are excluded, not just out-of-range values --
    a market with 0/100% market price has nothing meaningful to compare
    our_estimate against for edge/calibration purposes."""
    assert fbp._to_market_dict(_question(freeze_datetime_value="0.0"), 1.0, None) is None
    assert fbp._to_market_dict(_question(freeze_datetime_value="1.0"), 1.0, None) is None
    assert fbp._to_market_dict(_question(freeze_datetime_value="1.5"), 1.0, None) is None


def test_to_market_dict_ground_truth_coerced_to_float():
    m = fbp._to_market_dict(_question(), 0, None)
    assert m["_ground_truth"] == 0.0
    assert isinstance(m["_ground_truth"], float)


# ─── grade ───────────────────────────────────────────────────────────────────

def _market(ticker, mid_price, ground_truth):
    return {"ticker": ticker, "title": f"title-{ticker}", "mid_price": mid_price,
            "_ground_truth": ground_truth}


def test_grade_computes_brier_correctly_for_yes_call():
    markets = [_market("A", 0.5, 1.0)]
    scored = {"A": {"direction": "YES", "confidence": "MED", "our_estimate": 0.8}}
    result = fbp.grade(markets, scored)
    assert result["n"] == 1
    assert result["mean_brier"] == pytest.approx((0.8 - 1.0) ** 2)


def test_grade_computes_brier_correctly_for_no_call():
    """A NO call's Brier still compares our_estimate (P(YES)) against the
    raw ground truth directly -- NOT flipped -- matching core.logger.
    brier_component()'s own derivation (see grade()'s docstring)."""
    markets = [_market("A", 0.5, 0.0)]
    scored = {"A": {"direction": "NO", "confidence": "MED", "our_estimate": 0.2}}
    result = fbp.grade(markets, scored)
    assert result["mean_brier"] == pytest.approx((0.2 - 0.0) ** 2)


def test_grade_excludes_pass_calls():
    markets = [_market("A", 0.5, 1.0), _market("B", 0.5, 0.0)]
    scored = {
        "A": {"direction": "PASS", "confidence": "LOW", "our_estimate": 0.5},
        "B": {"direction": "YES", "confidence": "MED", "our_estimate": 0.6},
    }
    result = fbp.grade(markets, scored)
    assert result["n"] == 1
    assert result["n_pass"] == 1
    assert result["graded"][0]["ticker"] == "B"
    assert result["passed"][0]["ticker"] == "A"
    assert result["passed"][0]["our_estimate"] == 0.5


def test_grade_excludes_missing_scores():
    """A market never scored (dropped by the CLI response, a parse
    hiccup) must not crash -- treated the same as a PASS."""
    markets = [_market("A", 0.5, 1.0)]
    result = fbp.grade(markets, {})
    assert result["n"] == 0
    assert result["n_pass"] == 1
    assert result["mean_brier"] is None
    assert result["passed"][0]["direction"] == "(no score)"


def test_grade_computes_market_baseline_brier():
    """The market-price baseline Brier uses mid_price directly, not
    our_estimate -- same shape as core.logger.get_market_baseline_brier_score()."""
    markets = [_market("A", 0.9, 1.0)]
    scored = {"A": {"direction": "YES", "confidence": "MED", "our_estimate": 0.5}}
    result = fbp.grade(markets, scored)
    assert result["mean_market_brier"] == pytest.approx((0.9 - 1.0) ** 2)


def test_grade_mean_brier_averages_across_multiple_markets():
    markets = [_market("A", 0.5, 1.0), _market("B", 0.5, 0.0)]
    scored = {
        "A": {"direction": "YES", "confidence": "MED", "our_estimate": 0.8},
        "B": {"direction": "NO", "confidence": "MED", "our_estimate": 0.3},
    }
    result = fbp.grade(markets, scored)
    expected = ((0.8 - 1.0) ** 2 + (0.3 - 0.0) ** 2) / 2
    assert result["mean_brier"] == pytest.approx(expected)


def test_grade_all_estimate_brier_includes_pass_calls():
    """Regression for the 2026-09-15 blind pilot: when almost every call
    is PASS, mean_brier (YES/NO only) goes empty and hides the actual
    calibration signal, which lives in the raw our_estimate values --
    the all-estimate stats must include PASS calls that still reported
    an our_estimate, comparing against the market price over the SAME
    population (not the YES/NO-only mean_market_brier)."""
    markets = [_market("A", 0.6, 0.0), _market("B", 0.9, 1.0)]
    scored = {
        "A": {"direction": "PASS", "confidence": "LOW", "our_estimate": 0.55},
        "B": {"direction": "PASS", "confidence": "LOW", "our_estimate": 0.85},
    }
    result = fbp.grade(markets, scored)
    assert result["n"] == 0  # no YES/NO calls
    assert result["n_all_scored"] == 2
    expected_est = ((0.55 - 0.0) ** 2 + (0.85 - 1.0) ** 2) / 2
    expected_mkt = ((0.6 - 0.0) ** 2 + (0.9 - 1.0) ** 2) / 2
    assert result["mean_all_estimate_brier"] == pytest.approx(expected_est)
    assert result["mean_all_market_brier"] == pytest.approx(expected_mkt)


def test_grade_all_estimate_brier_excludes_never_scored_markets():
    """A market with no score at all (missing from scored_by_ticker)
    has no our_estimate to compare -- must be excluded from the
    all-estimate stats, not treated as a 0.0 or crash."""
    markets = [_market("A", 0.6, 0.0)]
    result = fbp.grade(markets, {})
    assert result["n_all_scored"] == 0
    assert result["mean_all_estimate_brier"] is None
    assert result["mean_all_market_brier"] is None


# ─── render_report ──────────────────────────────────────────────────────────

def test_render_report_includes_contamination_caveat():
    """The look-ahead-contamination caveat must always appear in the
    written report (default mode="search") -- this is the exact thing
    that made KalshiBench-v2 unusable, and a future reader must not
    mistake this pilot's Brier for a clean out-of-sample number."""
    markets = [_market("A", 0.5, 1.0)]
    result = fbp.grade(markets, {"A": {"direction": "YES", "confidence": "MED", "our_estimate": 0.8}})
    report = fbp.render_report("2026-08-30", markets, result, {"cost_usd": 0.01})
    assert "contamination" in report.lower()
    assert "2026-08-30" in report
    assert "Mode: search" in report


def test_render_report_blind_mode_caveat_differs_from_search():
    """mode="blind" must render a distinct caveat explaining WebSearch
    was disabled -- conflating the two modes' caveats would misrepresent
    which run actually had search access."""
    markets = [_market("A", 0.5, 1.0)]
    result = fbp.grade(markets, {"A": {"direction": "YES", "confidence": "MED", "our_estimate": 0.8}})
    report = fbp.render_report("2026-08-30", markets, result, {"cost_usd": 0.01}, mode="blind")
    assert "Mode: blind" in report
    assert "WebSearch disabled" in report
