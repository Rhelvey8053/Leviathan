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


def test_flag_reason_edge_shown():
    m = _base_market(flag_path="EDGE")
    prompt = scorer.build_prompt([m])
    assert "FLAG REASON: EDGE" in prompt


def test_flag_reason_cross_market_shown():
    m = _base_market(
        flag_path="CROSS_MARKET",
        poly={"price_gap": 0.18, "poly_price": 0.68, "poly_question": "Will X happen?", "match_score": 0.72},
        mid_price=0.50,
    )
    prompt = scorer.build_prompt([m])
    assert "FLAG REASON: CROSS_MARKET" in prompt
    assert "18%" in prompt
    assert "higher" in prompt


def test_flag_reason_cross_market_lower():
    m = _base_market(
        flag_path="CROSS_MARKET",
        poly={"price_gap": -0.20, "poly_price": 0.30, "poly_question": "Will X happen?", "match_score": 0.65},
        mid_price=0.50,
    )
    prompt = scorer.build_prompt([m])
    assert "FLAG REASON: CROSS_MARKET" in prompt
    assert "lower" in prompt


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


def test_system_prompt_has_cross_market_rule():
    assert "CROSS-MARKET DIVERGENCE" in scorer.SYSTEM_PROMPT
    assert "Polymarket" in scorer.SYSTEM_PROMPT


def test_system_prompt_has_entertainment_rule():
    assert "ENTERTAINMENT" in scorer.SYSTEM_PROMPT
    assert "MUST be below 15%" in scorer.SYSTEM_PROMPT


def test_system_prompt_has_legislative_rule():
    assert "LEGISLATIVE MARKETS" in scorer.SYSTEM_PROMPT
    assert "35%" in scorer.SYSTEM_PROMPT
    assert "cloture" in scorer.SYSTEM_PROMPT.lower()


def test_system_prompt_has_price_level_rule():
    assert "PRICE/LEVEL MARKETS" in scorer.SYSTEM_PROMPT
    assert "50/50" in scorer.SYSTEM_PROMPT


def test_system_prompt_has_earnings_rule():
    assert "EARNINGS BEAT/MISS" in scorer.SYSTEM_PROMPT
    assert "50%" in scorer.SYSTEM_PROMPT


def test_system_prompt_has_diplomatic_summit_rule():
    assert "DIPLOMATIC SUMMIT" in scorer.SYSTEM_PROMPT
    assert "40%" in scorer.SYSTEM_PROMPT


def test_system_prompt_has_reelection_rule():
    assert "REELECTION MARKETS" in scorer.SYSTEM_PROMPT
    assert "52%" in scorer.SYSTEM_PROMPT


def test_system_prompt_has_corporate_leadership_rule():
    assert "CORPORATE LEADERSHIP" in scorer.SYSTEM_PROMPT
    assert "8-K" in scorer.SYSTEM_PROMPT


def test_system_prompt_has_unsc_rule():
    assert "UN SECURITY COUNCIL" in scorer.SYSTEM_PROMPT
    assert "15%" in scorer.SYSTEM_PROMPT


def test_system_prompt_has_legal_proceedings_rule():
    """Rule 19: legal/criminal proceedings (pardon 35%, plea deal 45%, acquittal 35%)."""
    assert "LEGAL/CRIMINAL PROCEEDINGS" in scorer.SYSTEM_PROMPT
    assert "45%" in scorer.SYSTEM_PROMPT   # plea deal base rate


def test_system_prompt_has_government_funding_rule():
    """Rule 20: government shutdown / debt ceiling with bifurcated base rates."""
    assert "GOVERNMENT FUNDING" in scorer.SYSTEM_PROMPT
    assert "85%" in scorer.SYSTEM_PROMPT   # shutdown averted rate
    assert "70%" in scorer.SYSTEM_PROMPT   # debt ceiling raised rate


# ─── Liquidity context ───────────────────────────────────────────────────────

def test_liquidity_shown_when_volume_present():
    m = _base_market(volume_fp=5000)
    prompt = scorer.build_prompt([m])
    assert "Liquidity" in prompt
    assert "5000" in prompt


def test_liquidity_shows_oi_when_present():
    m = _base_market(volume_fp=5000, open_interest_fp=2000)
    prompt = scorer.build_prompt([m])
    assert "OI 2000" in prompt


def test_liquidity_omitted_when_no_volume():
    m = _base_market()  # no volume_fp
    prompt = scorer.build_prompt([m])
    assert "Liquidity:" not in prompt


def test_liquidity_omitted_when_volume_zero():
    m = _base_market(volume_fp=0)
    prompt = scorer.build_prompt([m])
    assert "Liquidity:" not in prompt


# ─── Cross-market (ext_markets) ──────────────────────────────────────────────

def test_ext_markets_shown_in_cross_market_section():
    m = _base_market(ext_markets=[
        {"source": "Manifold", "probability": 0.60, "price_gap": 0.35, "match_score": 0.85},
    ])
    prompt = scorer.build_prompt([m])
    assert "CROSS-MARKET:" in prompt
    assert "Manifold" in prompt
    assert "60.0%" in prompt


def test_ext_markets_consensus_shown_when_present():
    m = _base_market(
        ext_markets=[
            {"source": "Manifold",   "probability": 0.60, "price_gap": 0.35, "match_score": 0.85},
            {"source": "Metaculus",  "probability": 0.65, "price_gap": 0.40, "match_score": 0.80},
        ],
        ext_consensus={
            "consensus_dir": "YES", "sources_higher": 2, "sources_lower": 0,
            "avg_ext_price": 0.625, "consensus_gap": 0.375,
        },
    )
    prompt = scorer.build_prompt([m])
    assert "Consensus" in prompt
    assert "lean YES" in prompt


def test_no_cross_market_when_no_ext_markets():
    m = _base_market(ext_markets=[])
    prompt = scorer.build_prompt([m])
    assert "CROSS-MARKET:" not in prompt


# ─── Polymarket signal ───────────────────────────────────────────────────────

def test_polymarket_shown_when_poly_present():
    m = _base_market(poly={
        "poly_price": 0.50,
        "price_gap": 0.25,
        "poly_question": "Will the test happen?",
        "match_score": 0.90,
    })
    prompt = scorer.build_prompt([m])
    assert "POLYMARKET" in prompt
    assert "50.0%" in prompt
    assert "25.0% higher" in prompt


def test_polymarket_lower_direction_shown():
    m = _base_market(poly={
        "poly_price": 0.10,
        "price_gap": -0.15,
        "poly_question": "Will the test happen?",
        "match_score": 0.88,
    })
    prompt = scorer.build_prompt([m])
    assert "lower" in prompt


def test_no_polymarket_when_poly_none():
    m = _base_market(poly=None)
    prompt = scorer.build_prompt([m])
    assert "POLYMARKET" not in prompt


# ─── Whale alert ────────────────────────────────────────────────────────────

def test_whale_alert_shown_when_detected():
    m = _base_market(whale_data={
        "whale_detected": True,
        "whale_direction": "YES",
        "max_trade_size": 2500,
        "avg_trade_size": 1200.0,
    })
    prompt = scorer.build_prompt([m])
    assert "WHALE ALERT" in prompt
    assert "YES" in prompt
    assert "2500" in prompt


def test_whale_alert_not_shown_when_not_detected():
    m = _base_market(whale_data={"whale_detected": False, "whale_direction": "YES",
                                  "max_trade_size": 100, "avg_trade_size": 50.0})
    prompt = scorer.build_prompt([m])
    assert "WHALE ALERT" not in prompt


def test_whale_reversal_shown():
    m = _base_market(
        whale_reversal=True,
        whale_data={"whale_detected": True, "whale_direction": "NO",
                    "max_trade_size": 2000, "avg_trade_size": 1000.0},
    )
    prompt = scorer.build_prompt([m])
    assert "REVERSAL SIGNAL" in prompt


# ─── Order book signal ───────────────────────────────────────────────────────

def test_order_book_shown_when_ob_flag():
    m = _base_market(ob_flag=True, ob_imbalance=0.75, ob_direction="YES")
    prompt = scorer.build_prompt([m])
    assert "ORDER BOOK" in prompt
    assert "75%" in prompt
    assert "YES" in prompt


def test_order_book_not_shown_without_flag():
    m = _base_market(ob_flag=False)
    prompt = scorer.build_prompt([m])
    assert "ORDER BOOK" not in prompt


# ─── Spread signal ───────────────────────────────────────────────────────────

def test_spread_signal_shown_when_spread_wide():
    m = _base_market(spread_wide=True, spread_pct=0.20)
    prompt = scorer.build_prompt([m])
    assert "SPREAD SIGNAL" in prompt
    assert "20.0%" in prompt


def test_spread_signal_not_shown_when_not_wide():
    m = _base_market(spread_wide=False)
    prompt = scorer.build_prompt([m])
    assert "SPREAD SIGNAL" not in prompt


# ─── Smart money ────────────────────────────────────────────────────────────

def test_smart_money_shown_in_prompt():
    m = _base_market(smart_money=[
        {"direction": "YES", "avg_pct_pnl": 85.0},
        {"direction": "YES", "avg_pct_pnl": 60.0},
    ])
    prompt = scorer.build_prompt([m])
    assert "SMART MONEY" in prompt
    assert "2 winning wallet" in prompt
    assert "YES" in prompt


def test_smart_money_no_wallets_no_section():
    m = _base_market(smart_money=[])
    prompt = scorer.build_prompt([m])
    assert "SMART MONEY" not in prompt


# ─── EDGE flag reason ────────────────────────────────────────────────────────

def test_flag_reason_edge_shown():
    m = _base_market(flag_path="EDGE")
    prompt = scorer.build_prompt([m])
    assert "FLAG REASON: EDGE" in prompt


# ─── Multiple markets in one prompt ──────────────────────────────────────────

def test_multiple_markets_all_present_in_prompt():
    m1 = _base_market(ticker="KXFIRST",  title="First market")
    m2 = _base_market(ticker="KXSECOND", title="Second market")
    m3 = _base_market(ticker="KXTHIRD",  title="Third market")
    prompt = scorer.build_prompt([m1, m2, m3])
    assert "KXFIRST"  in prompt
    assert "KXSECOND" in prompt
    assert "KXTHIRD"  in prompt


def test_multiple_markets_numbered_sequentially():
    m1 = _base_market(ticker="KXONE")
    m2 = _base_market(ticker="KXTWO")
    prompt = scorer.build_prompt([m1, m2])
    assert "1. [KXONE]" in prompt
    assert "2. [KXTWO]" in prompt


# ─── Horizon notes ───────────────────────────────────────────────────────────

def test_horizon_intraday_note_in_prompt():
    m = _base_market(time_horizon="INTRADAY")
    prompt = scorer.build_prompt([m])
    assert "closes today" in prompt


def test_horizon_weekly_note_in_prompt():
    m = _base_market(time_horizon="WEEKLY")
    prompt = scorer.build_prompt([m])
    assert "7 days" in prompt


# ─── Calibration rules 21-22 ─────────────────────────────────────────────────

def test_rule_21_geopolitical_keywords_in_system_prompt():
    """Rule 21 covers geopolitical/military escalation with base rate anchors."""
    sp = scorer.SYSTEM_PROMPT
    assert "21." in sp
    assert "NATO Article 5" in sp
    assert "Military invasion" in sp or "military invasion" in sp.lower()
    assert "GEOPOLITICAL" in sp or "geopolitical" in sp.lower()


def test_rule_22_natural_disaster_keywords_in_system_prompt():
    """Rule 22 covers natural disaster / weather severity thresholds."""
    sp = scorer.SYSTEM_PROMPT
    assert "22." in sp
    assert "Wildfire" in sp or "wildfire" in sp.lower()
    assert "hurricane" in sp.lower()
    assert "NATURAL DISASTER" in sp or "natural disaster" in sp.lower()
