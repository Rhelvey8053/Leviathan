"""
Offline tests for scorer.py build_prompt().

No network calls, no Claude CLI invocations.
Run: python -m pytest -q
"""

import pytest

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


def test_empty_markets_returns_empty_prompt():
    prompt = scorer.build_prompt([])
    # Should not crash, just return the header
    assert isinstance(prompt, str)
