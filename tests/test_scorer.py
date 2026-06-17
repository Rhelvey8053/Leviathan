"""
Offline tests for scorer.py build_prompt().

No network calls, no Claude CLI invocations.
Run: python -m pytest -q
"""

import pytest
from datetime import datetime, timezone, timedelta

import scorer


def _base_market(**kwargs):
    """Minimal market dict that build_prompt can consume without errors."""
    return {
        "ticker":          "KXTEST-26DEC01",
        "title":           "Will the test event happen by December?",
        "mid_price":       0.25,
        "close_time":      "2026-12-01T00:00:00Z",
        "time_horizon":    "LONG",
        "drift_flag":      False,
        "spread_wide":     False,
        "ob_flag":         False,
        "watchlist_signal": False,
        **kwargs,
    }


# ─── Volume spike signal ──────────────────────────────────────────────────────

def test_volume_spike_shown_when_24h_pct_over_20():
    m = _base_market(volume_fp=1000, volume_24h_fp=300)
    prompt = scorer.build_prompt([m])
    assert "VOLUME SPIKE" in prompt
    assert "30%" in prompt or "30" in prompt


def test_volume_spike_not_shown_when_24h_pct_under_20():
    m = _base_market(volume_fp=1000, volume_24h_fp=50)
    prompt = scorer.build_prompt([m])
    assert "VOLUME SPIKE" not in prompt


def test_volume_spike_not_shown_when_no_24h_data():
    m = _base_market(volume_fp=1000)  # no volume_24h_fp
    prompt = scorer.build_prompt([m])
    assert "VOLUME SPIKE" not in prompt


def test_volume_spike_not_shown_when_total_zero():
    m = _base_market(volume_fp=0, volume_24h_fp=100)
    prompt = scorer.build_prompt([m])
    assert "VOLUME SPIKE" not in prompt


def test_volume_spike_boundary_exactly_20_pct():
    # 200/1000 = 20% — should trigger
    m = _base_market(volume_fp=1000, volume_24h_fp=200)
    prompt = scorer.build_prompt([m])
    assert "VOLUME SPIKE" in prompt


# ─── Price jump signal ────────────────────────────────────────────────────────

def test_price_jump_up_shown_when_large_move():
    m = _base_market(previous_price_dollars="0.1000", last_price_dollars="0.1500")
    prompt = scorer.build_prompt([m])
    assert "PRICE JUMP" in prompt
    assert "UP" in prompt


def test_price_jump_down_shown_when_large_move():
    m = _base_market(previous_price_dollars="0.2000", last_price_dollars="0.1200")
    prompt = scorer.build_prompt([m])
    assert "PRICE JUMP" in prompt
    assert "DOWN" in prompt


def test_price_jump_not_shown_when_small_move():
    # 10% move — below 20% threshold
    m = _base_market(previous_price_dollars="0.1000", last_price_dollars="0.1100")
    prompt = scorer.build_prompt([m])
    assert "PRICE JUMP" not in prompt


def test_price_jump_not_shown_when_no_previous():
    m = _base_market(last_price_dollars="0.1500")  # no previous_price_dollars
    prompt = scorer.build_prompt([m])
    assert "PRICE JUMP" not in prompt


def test_price_jump_not_shown_when_previous_zero():
    m = _base_market(previous_price_dollars="0.0000", last_price_dollars="0.1500")
    prompt = scorer.build_prompt([m])
    assert "PRICE JUMP" not in prompt


# ─── General prompt structure ─────────────────────────────────────────────────

def test_prompt_contains_ticker():
    m = _base_market()
    prompt = scorer.build_prompt([m])
    assert "KXTEST-26DEC01" in prompt


def test_prompt_contains_market_price():
    m = _base_market(mid_price=0.35)
    prompt = scorer.build_prompt([m])
    assert "35.0%" in prompt


def test_prompt_unknown_price_when_mid_none():
    m = _base_market(mid_price=None)
    prompt = scorer.build_prompt([m])
    assert "unknown" in prompt


def test_days_remaining_shown_in_prompt():
    close = (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = _base_market(close_time=close)
    prompt = scorer.build_prompt([m])
    assert "45d remaining" in prompt or "44d remaining" in prompt  # allow ±1 for test timing


def test_days_remaining_zero_for_past_date():
    past = "2020-01-01T00:00:00Z"
    m = _base_market(close_time=past)
    prompt = scorer.build_prompt([m])
    assert "0d remaining" in prompt


def test_drift_signal_shown():
    m = _base_market(drift_flag=True, price_drift=0.25)
    prompt = scorer.build_prompt([m])
    assert "DRIFT SIGNAL" in prompt
    assert "25.0%" in prompt or "above" in prompt


def test_watchlist_signal_shown_with_direction():
    m = _base_market(
        watchlist_signal=True,
        watchlist_direction="NO",
        watchlist_position_val=12000.0,
        watchlist_trader_count=3,
    )
    prompt = scorer.build_prompt([m])
    assert "WATCHLIST SIGNAL" in prompt
    assert "NO" in prompt
    assert "$12,000" in prompt
    assert "3 trader" in prompt


def test_base_rate_shown_when_set():
    m = _base_market(base_rate=0.35)
    prompt = scorer.build_prompt([m])
    assert "Base rate estimate: 35.0%" in prompt


def test_flag_reason_heuristic_shown():
    m = _base_market(flag_path="HEURISTIC", base_rate=0.55)
    prompt = scorer.build_prompt([m])
    assert "FLAG REASON: HEURISTIC" in prompt
    assert "55%" in prompt


def test_flag_reason_drift_shown():
    m = _base_market(flag_path="DRIFT")
    prompt = scorer.build_prompt([m])
    assert "FLAG REASON: DRIFT" in prompt


def test_flag_reason_watchlist_shown():
    m = _base_market(flag_path="WATCHLIST")
    prompt = scorer.build_prompt([m])
    assert "FLAG REASON: WATCHLIST" in prompt


def test_flag_reason_absent_when_no_path():
    m = _base_market(flag_path=None)
    prompt = scorer.build_prompt([m])
    assert "FLAG REASON" not in prompt


# ─── DRIFT / HEURISTIC conflict warning ──────────────────────────────────────

def test_signal_conflict_shown_when_drift_and_base_rate_disagree():
    # mid=0.77, last price implied: drift_pct is negative means mid < last → drift says YES
    # base_rate=0.35 < mid=0.77 → base rate says NO → CONFLICT
    m = _base_market(drift_flag=True, price_drift=-0.08, mid_price=0.77, base_rate=0.35)
    prompt = scorer.build_prompt([m])
    assert "SIGNAL CONFLICT" in prompt
    assert "YES" in prompt
    assert "NO" in prompt
    assert "35%" in prompt


def test_signal_conflict_shows_correct_direction_for_upward_drift():
    # drift_pct positive means mid > last → drift says NO (mean revert down)
    # base_rate=0.80 > mid=0.25 → base rate says YES → CONFLICT
    m = _base_market(drift_flag=True, price_drift=0.10, mid_price=0.25, base_rate=0.80)
    prompt = scorer.build_prompt([m])
    assert "SIGNAL CONFLICT" in prompt


def test_no_conflict_when_drift_and_base_rate_agree():
    # drift_pct negative → drift says YES (mean revert up)
    # base_rate=0.80 > mid=0.20 → base rate also says YES → NO CONFLICT
    m = _base_market(drift_flag=True, price_drift=-0.05, mid_price=0.20, base_rate=0.80)
    prompt = scorer.build_prompt([m])
    assert "SIGNAL CONFLICT" not in prompt


def test_no_conflict_when_base_rate_absent():
    m = _base_market(drift_flag=True, price_drift=-0.08, mid_price=0.77)
    prompt = scorer.build_prompt([m])
    assert "SIGNAL CONFLICT" not in prompt


def test_no_conflict_when_drift_flag_false():
    m = _base_market(drift_flag=False, mid_price=0.77, base_rate=0.35)
    prompt = scorer.build_prompt([m])
    assert "SIGNAL CONFLICT" not in prompt


def test_empty_markets_returns_empty_prompt():
    prompt = scorer.build_prompt([])
    # Should not crash, just return the header
    assert isinstance(prompt, str)


# ─── Calibration rules in system prompt ──────────────────────────────────────

def test_system_prompt_has_ipo_rule():
    assert "IPO ANNOUNCEMENT" in scorer.SYSTEM_PROMPT
    assert "confidentially filed" in scorer.SYSTEM_PROMPT.lower()


def test_system_prompt_has_cabinet_rule():
    assert "CABINET" in scorer.SYSTEM_PROMPT
    assert "65%" in scorer.SYSTEM_PROMPT


def test_system_prompt_has_sports_debut_rule():
    assert "SPORTS DEBUT" in scorer.SYSTEM_PROMPT
    assert "35%" in scorer.SYSTEM_PROMPT


def test_system_prompt_has_ai_release_rule():
    assert "AI/TECH MODEL RELEASE" in scorer.SYSTEM_PROMPT
    assert "announced" in scorer.SYSTEM_PROMPT.lower()


def test_system_prompt_has_entertainment_rule():
    assert "ENTERTAINMENT" in scorer.SYSTEM_PROMPT
    assert "MUST be below 15%" in scorer.SYSTEM_PROMPT
