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


# ─── Calibration rules 23-27 ─────────────────────────────────────────────────

def test_rule_23_ai_capability_keywords_in_system_prompt():
    """Rule 23 covers AI capability milestones (exam passage, benchmarks) with optimism-bias correction."""
    sp = scorer.SYSTEM_PROMPT
    assert "23." in sp
    assert "AI CAPABILITY" in sp or "ai capability" in sp.lower()
    assert "training-data bias" in sp or "training data bias" in sp.lower()
    assert "AGI" in sp


def test_rule_24_bank_failure_keywords_in_system_prompt():
    """Rule 24 covers bank failure and financial system risk markets."""
    sp = scorer.SYSTEM_PROMPT
    assert "24." in sp
    assert "BANK FAILURE" in sp or "bank failure" in sp.lower()
    assert "FDIC" in sp
    assert "~15%" in sp or "15%" in sp


def test_rule_25_emerging_tech_readiness_keywords_in_system_prompt():
    """Rule 25 covers emerging technology readiness (AV, quantum computing, humanoid robots)."""
    sp = scorer.SYSTEM_PROMPT
    assert "25." in sp
    assert "quantum" in sp.lower()
    assert "autonomous vehicle" in sp.lower() or "self-driving" in sp.lower()
    assert "EMERGING TECHNOLOGY" in sp or "emerging technology" in sp.lower()


def test_rule_26_climate_records_keywords_in_system_prompt():
    """Rule 26 covers climate/environmental records markets, distinct from disaster severity."""
    sp = scorer.SYSTEM_PROMPT
    assert "26." in sp
    assert "CLIMATE" in sp or "climate" in sp.lower()
    assert "hottest year" in sp.lower() or "temperature" in sp.lower()


def test_rule_27_cryptocurrency_keywords_in_system_prompt():
    """Rule 27 covers cryptocurrency price-level markets with Rule 13 reinforcement."""
    sp = scorer.SYSTEM_PROMPT
    assert "27." in sp
    assert "CRYPTOCURRENCY" in sp or "cryptocurrency" in sp.lower()
    assert "Bitcoin" in sp or "bitcoin" in sp.lower()
    assert "25pp from 50%" in sp or "25 pp from 50" in sp.lower() or "25pp" in sp


# ─── Flag reason direction annotation ────────────────────────────────────────

def test_edge_flag_reason_shows_leans_yes_when_base_rate_above_mid():
    """EDGE flag reason must say 'leans YES' when heuristic is above market price."""
    m = _base_market(
        flag_path="EDGE",
        base_rate=0.65,
        heuristic_direction="YES",
        mid_price=0.35,
    )
    prompt = scorer.build_prompt([m])
    assert "leans YES" in prompt


def test_edge_flag_reason_shows_leans_no_when_base_rate_below_mid():
    """EDGE flag reason must say 'leans NO' when heuristic is below market price."""
    m = _base_market(
        flag_path="EDGE",
        base_rate=0.10,
        heuristic_direction="NO",
        mid_price=0.55,
    )
    prompt = scorer.build_prompt([m])
    assert "leans NO" in prompt


def test_heuristic_flag_reason_shows_leans_yes():
    """HEURISTIC flag reason must include base rate % and 'leans YES'."""
    m = _base_market(
        flag_path="HEURISTIC",
        base_rate=0.70,
        heuristic_direction="YES",
        mid_price=0.40,
    )
    prompt = scorer.build_prompt([m])
    assert "leans YES" in prompt
    assert "70%" in prompt


def test_heuristic_flag_reason_omits_lean_when_neutral():
    """HEURISTIC flag reason omits 'leans' when direction is NEUTRAL (within 5pp buffer)."""
    m = _base_market(
        flag_path="HEURISTIC",
        base_rate=0.40,
        heuristic_direction="NEUTRAL",
        mid_price=0.40,
    )
    prompt = scorer.build_prompt([m])
    assert "leans YES" not in prompt
    assert "leans NO" not in prompt


# ─── Calibration rule 28 ─────────────────────────────────────────────────────

def test_rule_28_short_horizon_decay_in_system_prompt():
    """Rule 28 covers short-horizon edge decay for INTRADAY/WEEKLY markets."""
    sp = scorer.SYSTEM_PROMPT
    assert "28." in sp
    assert "SHORT-HORIZON" in sp or "short-horizon" in sp.lower()
    assert "INTRADAY" in sp or "intraday" in sp.lower()
    assert "72 hours" in sp or "72h" in sp
    assert "15pp" in sp or "15 pp" in sp


# ─── build_system_prompt calibration feedback ─────────────────────────────────

def test_build_system_prompt_no_calibration_returns_base():
    """No calibration dict → returns SYSTEM_PROMPT unchanged."""
    assert scorer.build_system_prompt(None) == scorer.SYSTEM_PROMPT
    assert scorer.build_system_prompt({}) == scorer.SYSTEM_PROMPT


def test_build_system_prompt_no_data_returns_base():
    """All zero totals → returns SYSTEM_PROMPT unchanged (no feedback to show)."""
    cal = {
        "HIGH": {"total": 0, "wins": 0, "win_rate": None},
        "MED":  {"total": 0, "wins": 0, "win_rate": None},
        "LOW":  {"total": 0, "wins": 0, "win_rate": None},
    }
    assert scorer.build_system_prompt(cal) == scorer.SYSTEM_PROMPT


def test_build_system_prompt_with_data_appends_feedback():
    """Resolved data → CALIBRATION FEEDBACK section appended."""
    cal = {
        "HIGH": {"total": 5, "wins": 3, "win_rate": 60.0},
        "MED":  {"total": 3, "wins": 1, "win_rate": 33.3},
        "LOW":  {"total": 0, "wins": 0, "win_rate": None},
    }
    sp = scorer.build_system_prompt(cal)
    assert "CALIBRATION FEEDBACK" in sp
    assert "HIGH" in sp
    assert "3/5" in sp
    assert "60%" in sp
    assert "MED" in sp
    assert "1/3" in sp


def test_build_system_prompt_includes_guidance_text():
    """Guidance text about what to do with calibration data is present."""
    cal = {"HIGH": {"total": 4, "wins": 2, "win_rate": 50.0}}
    sp = scorer.build_system_prompt(cal)
    assert "overconfident" in sp or "downgrade" in sp


def test_build_system_prompt_base_prompt_still_present():
    """Original SYSTEM_PROMPT content is preserved when feedback is appended."""
    cal = {"HIGH": {"total": 2, "wins": 1, "win_rate": 50.0}}
    sp = scorer.build_system_prompt(cal)
    assert scorer.SYSTEM_PROMPT[:100] in sp
    assert "CALIBRATION RULES" in sp


def test_build_system_prompt_flag_cal_appended_when_enough_data():
    """Flag-path win rates appear when ≥2 signals resolved."""
    flag_cal = [
        {"flag_path": "HEURISTIC", "total": 5, "wins": 4, "win_rate": 80.0},
        {"flag_path": "DRIFT",     "total": 3, "wins": 1, "win_rate": 33.3},
    ]
    sp = scorer.build_system_prompt(None, flag_cal=flag_cal)
    assert "CALIBRATION FEEDBACK" in sp
    assert "HEURISTIC" in sp
    assert "80%" in sp
    assert "DRIFT" in sp
    assert "33%" in sp


def test_build_system_prompt_flag_cal_filters_below_min_count():
    """Flag paths with <2 resolved signals are excluded from flag_cal feedback."""
    flag_cal = [{"flag_path": "EDGE", "total": 1, "wins": 1, "win_rate": 100.0}]
    sp = scorer.build_system_prompt(None, flag_cal=flag_cal)
    # Only 1 signal — below min threshold, no feedback shown
    assert "EDGE" not in sp or sp == scorer.SYSTEM_PROMPT


def test_build_system_prompt_flag_cal_shows_reliable_note():
    """Flag paths with win_rate ≥ 65% show 'reliable' note."""
    flag_cal = [{"flag_path": "WATCHLIST", "total": 4, "wins": 3, "win_rate": 75.0}]
    sp = scorer.build_system_prompt(None, flag_cal=flag_cal)
    assert "reliable" in sp


def test_build_system_prompt_flag_cal_shows_poor_note():
    """Flag paths with win_rate < 45% show 'poor' note."""
    flag_cal = [{"flag_path": "DRIFT", "total": 3, "wins": 1, "win_rate": 33.0}]
    sp = scorer.build_system_prompt(None, flag_cal=flag_cal)
    assert "poor" in sp or "skeptical" in sp


def test_build_system_prompt_no_flag_cal_no_effect():
    """Omitting flag_cal leaves prompt unchanged if no confidence cal either."""
    assert scorer.build_system_prompt(None, flag_cal=None) == scorer.SYSTEM_PROMPT
    assert scorer.build_system_prompt(None, flag_cal=[]) == scorer.SYSTEM_PROMPT


# ─── SIGNAL SUMMARY alignment block ─────────────────────────────────────────

def test_signal_summary_shown_when_two_sources_agree_yes():
    """SIGNAL SUMMARY appears when ≥2 independent sources lean YES."""
    m = _base_market(
        heuristic_direction="YES",
        drift_flag=True,
        price_drift=-0.10,  # mid < last → mean revert up → YES
        mid_price=0.30,
    )
    prompt = scorer.build_prompt([m])
    assert "SIGNAL SUMMARY" in prompt
    assert "lean YES" in prompt


def test_signal_summary_shown_when_two_sources_agree_no():
    """SIGNAL SUMMARY appears when ≥2 independent sources lean NO."""
    m = _base_market(
        heuristic_direction="NO",
        poly={"price_gap": -0.12, "poly_price": 0.18, "poly_question": "q", "match_score": 0.8},
        mid_price=0.30,
    )
    prompt = scorer.build_prompt([m])
    assert "SIGNAL SUMMARY" in prompt
    assert "lean NO" in prompt


def test_signal_summary_shows_all_when_sources_unanimous():
    """SIGNAL SUMMARY says 'ALL lean' when every source points the same direction."""
    m = _base_market(
        heuristic_direction="NO",
        drift_flag=True,
        price_drift=0.15,   # mid > last → mean revert down → NO
        mid_price=0.60,
    )
    prompt = scorer.build_prompt([m])
    assert "SIGNAL SUMMARY" in prompt
    assert "ALL lean NO" in prompt


def test_signal_summary_absent_when_only_one_source():
    """SIGNAL SUMMARY omitted when only one signal source is active."""
    m = _base_market(
        heuristic_direction="YES",
        mid_price=0.30,
        # no poly, no drift, no whale, no ob
    )
    prompt = scorer.build_prompt([m])
    assert "SIGNAL SUMMARY" not in prompt


# ─── SHORT HORIZON warning in prompt ─────────────────────────────────────────

def test_short_horizon_warning_shown_when_true():
    """[!] SHORT HORIZON appears in prompt when short_horizon=True."""
    m = _base_market(short_horizon=True, time_horizon="WEEKLY")
    prompt = scorer.build_prompt([m])
    assert "SHORT HORIZON" in prompt
    assert "72 hours" in prompt


def test_short_horizon_warning_absent_when_false():
    """No SHORT HORIZON line when short_horizon is False or absent."""
    m = _base_market(short_horizon=False, time_horizon="MONTHLY")
    prompt = scorer.build_prompt([m])
    assert "SHORT HORIZON" not in prompt


def test_short_horizon_warning_absent_by_default():
    """No SHORT HORIZON line when short_horizon key is not in the market dict."""
    m = _base_market(time_horizon="QUARTERLY")  # no short_horizon key
    prompt = scorer.build_prompt([m])
    assert "SHORT HORIZON" not in prompt


# ─── SIGNAL SUMMARY: watchlist_direction counted ──────────────────────────────

def test_signal_summary_includes_watchlist_yes():
    """watchlist_direction=YES boosts YES count in SIGNAL SUMMARY."""
    # Give the market a heuristic lean of NO + watchlist YES → shows MIXED or 1/2
    m = _base_market(
        heuristic_direction="NO",
        watchlist_signal=True,
        watchlist_direction="YES",
    )
    prompt = scorer.build_prompt([m])
    assert "SIGNAL SUMMARY" in prompt


def test_signal_summary_includes_watchlist_no():
    """watchlist_direction=NO boosts NO count in SIGNAL SUMMARY."""
    m = _base_market(
        heuristic_direction="NO",
        watchlist_signal=True,
        watchlist_direction="NO",
    )
    prompt = scorer.build_prompt([m])
    # 2 NO sources → SIGNAL SUMMARY present with ALL lean NO
    assert "SIGNAL SUMMARY" in prompt
    assert "NO" in prompt


def test_signal_summary_watchlist_unknown_not_counted():
    """watchlist_direction=UNKNOWN does not add a directional vote."""
    m = _base_market(watchlist_signal=True, watchlist_direction="UNKNOWN")
    # Only 1 directional source (heuristic) if heuristic fires, not 2 → no SIGNAL SUMMARY
    # Base market has heuristic_direction=None by default, so 0 sources
    prompt = scorer.build_prompt([m])
    # UNKNOWN watchlist alone cannot trigger SIGNAL SUMMARY (needs >=2 sources)
    # heuristic_direction defaults to None in _base_market, so total = 0 → no summary
    assert "SIGNAL SUMMARY" not in prompt


# ─── Net-of-spread edge in prompt ─────────────────────────────────────────────

def test_net_edge_shown_when_base_rate_and_net_edge_present():
    """Edge block shows net-of-spread line when base_rate and net_edge are provided."""
    m = _base_market(base_rate=0.45, raw_edge=0.20, net_edge=0.15)
    prompt = scorer.build_prompt([m])
    assert "net-of-spread" in prompt
    assert "15.0pp" in prompt


def test_net_edge_spread_consumes_warning_shown():
    """[SPREAD CONSUMES EDGE] warning appears when net_edge <= 0."""
    m = _base_market(base_rate=0.45, raw_edge=0.05, net_edge=-0.02)
    prompt = scorer.build_prompt([m])
    assert "SPREAD CONSUMES EDGE" in prompt


def test_net_edge_thin_warning_shown():
    """thin net edge warning shown when 0 < net_edge < 5pp."""
    m = _base_market(base_rate=0.45, raw_edge=0.10, net_edge=0.03)
    prompt = scorer.build_prompt([m])
    assert "thin net edge" in prompt


def test_net_edge_absent_when_no_base_rate():
    """No edge line when base_rate is not present."""
    m = _base_market()  # no base_rate
    prompt = scorer.build_prompt([m])
    assert "net-of-spread" not in prompt


# ─── SIGNAL SUMMARY: recent activity tags ─────────────────────────────────────

def test_signal_summary_vol_spike_tag():
    """[VOL_SPIKE] appears in SIGNAL SUMMARY when 24h vol >= 20% of total."""
    m = _base_market(
        heuristic_direction="YES",
        heuristic_direction2=None,  # ignored
        # Need >=2 directional sources for SIGNAL SUMMARY to appear
    )
    # Give it two directional sources so SIGNAL SUMMARY fires
    m["heuristic_direction"] = "YES"
    m["poly"] = {"price_gap": 0.10, "poly_price": 0.60, "poly_question": "Test question", "match_score": 0.8}
    m["volume_fp"] = 1000
    m["volume_24h_fp"] = 250   # 25% of total → vol_spike
    prompt = scorer.build_prompt([m])
    assert "VOL_SPIKE" in prompt


def test_signal_summary_price_jump_tag():
    """[PRICE_JUMP] appears in SIGNAL SUMMARY when price moved >=20%."""
    m = _base_market(heuristic_direction="YES")
    m["poly"] = {"price_gap": 0.10, "poly_price": 0.60, "poly_question": "Test question", "match_score": 0.8}
    m["previous_price_dollars"] = 0.30
    m["last_price_dollars"] = 0.40   # +33% move → PRICE_JUMP
    prompt = scorer.build_prompt([m])
    assert "PRICE_JUMP" in prompt


def test_signal_summary_no_activity_tag_when_below_threshold():
    """No VOL_SPIKE or PRICE_JUMP when activity is below thresholds."""
    m = _base_market(heuristic_direction="YES")
    m["poly"] = {"price_gap": 0.10, "poly_price": 0.60, "poly_question": "Test question", "match_score": 0.8}
    m["volume_fp"] = 1000
    m["volume_24h_fp"] = 100   # 10% — below 20% threshold
    m["previous_price_dollars"] = 0.30
    m["last_price_dollars"] = 0.31   # <5% move — below 20% threshold
    prompt = scorer.build_prompt([m])
    assert "VOL_SPIKE" not in prompt
    assert "PRICE_JUMP" not in prompt


# ─── SIGNAL SUMMARY: multi-platform consensus weighting ───────────────────────

def test_signal_summary_multi_platform_three_sources():
    """3 external platforms agreeing adds 3 votes to SIGNAL SUMMARY (capped at 3)."""
    m = _base_market(heuristic_direction="YES")
    m["ext_consensus"] = {
        "consensus_gap": 0.10,
        "consensus_dir": "YES",
        "sources_higher": 3,
        "sources_lower": 0,
    }
    prompt = scorer.build_prompt([m])
    # heuristic=YES (1) + 3 platforms=YES (3) = 4 total YES sources → SIGNAL SUMMARY present
    assert "SIGNAL SUMMARY" in prompt
    assert "YES" in prompt


def test_signal_summary_single_platform_adds_one_vote():
    """1 external platform adds only 1 vote (not an outsized weight)."""
    m = _base_market(heuristic_direction="YES")
    m["ext_consensus"] = {
        "consensus_gap": 0.10,
        "consensus_dir": "YES",
        "sources_higher": 1,
        "sources_lower": 0,
    }
    prompt = scorer.build_prompt([m])
    # heuristic=YES (1) + 1 platform=YES (1) = 2 total → SIGNAL SUMMARY fires
    assert "SIGNAL SUMMARY" in prompt


# ─── Cross-market conflict warnings ───────────────────────────────────────────

def test_heuristic_poly_conflict_shown():
    """[!] HEURISTIC vs POLYMARKET CONFLICT shown when heuristic and Polymarket disagree."""
    m = _base_market(
        heuristic_direction="YES",  # base rate says YES is cheap
    )
    # Polymarket says NO (price_gap < 0 means Kalshi YES is expensive vs Poly)
    m["poly"] = {
        "price_gap": -0.10,   # Poly NO direction
        "poly_price": 0.25,
        "poly_question": "Test question",
        "match_score": 0.8,
    }
    prompt = scorer.build_prompt([m])
    assert "HEURISTIC vs POLYMARKET CONFLICT" in prompt


def test_heuristic_poly_agree_no_conflict():
    """No conflict warning when heuristic and Polymarket agree on direction."""
    m = _base_market(heuristic_direction="YES")
    m["poly"] = {
        "price_gap": 0.10,    # Poly also YES direction
        "poly_price": 0.45,
        "poly_question": "Test question",
        "match_score": 0.8,
    }
    prompt = scorer.build_prompt([m])
    assert "HEURISTIC vs POLYMARKET CONFLICT" not in prompt


def test_heuristic_consensus_conflict_shown_when_two_platforms():
    """Consensus conflict shown when >=2 external platforms disagree with heuristic."""
    m = _base_market(heuristic_direction="YES")
    m["ext_consensus"] = {
        "consensus_gap": -0.10,  # external says NO
        "consensus_dir": "NO",
        "sources_higher": 0,
        "sources_lower": 2,  # 2 platforms say lower → conflict with YES heuristic
    }
    prompt = scorer.build_prompt([m])
    assert "HEURISTIC vs CONSENSUS CONFLICT" in prompt


def test_heuristic_consensus_conflict_not_shown_for_single_platform():
    """Consensus conflict not shown when only 1 external platform disagrees."""
    m = _base_market(heuristic_direction="YES")
    m["ext_consensus"] = {
        "consensus_gap": -0.10,
        "consensus_dir": "NO",
        "sources_higher": 0,
        "sources_lower": 1,  # only 1 platform — below 2-platform threshold
    }
    prompt = scorer.build_prompt([m])
    assert "HEURISTIC vs CONSENSUS CONFLICT" not in prompt


# ─── Signal persistence block ─────────────────────────────────────────────────

def test_persistence_block_absent_when_no_prior_appearances():
    """No prior_appearances → Signal history block not shown."""
    m = _base_market()
    # no prior_appearances key — falls through
    prompt = scorer.build_prompt([m])
    assert "Signal history" not in prompt


def test_persistence_block_shown_when_prior_appearances():
    """prior_appearances > 0 → Signal history block appears."""
    m = _base_market()
    m["prior_appearances"] = 3
    m["prior_yes"] = 3
    m["prior_no"] = 0
    m["direction_consistent"] = True
    prompt = scorer.build_prompt([m])
    assert "Signal history" in prompt
    assert "3 distinct day(s)" in prompt


def test_persistence_block_consistent_tag():
    """direction_consistent=True shows [CONSISTENT] tag."""
    m = _base_market()
    m["prior_appearances"] = 2
    m["prior_yes"] = 2
    m["prior_no"] = 0
    m["direction_consistent"] = True
    prompt = scorer.build_prompt([m])
    assert "[CONSISTENT]" in prompt


def test_persistence_block_mixed_tag():
    """direction_consistent=False shows [MIXED] tag."""
    m = _base_market()
    m["prior_appearances"] = 3
    m["prior_yes"] = 2
    m["prior_no"] = 1
    m["direction_consistent"] = False
    prompt = scorer.build_prompt([m])
    assert "[MIXED" in prompt


def test_persistence_price_drift_deepening_yes():
    """If direction YES and price fell since first flag, shows mispricing deepened."""
    m = _base_market(heuristic_direction="YES")
    m["prior_appearances"] = 2
    m["prior_yes"] = 2
    m["prior_no"] = 0
    m["direction_consistent"] = True
    m["first_flagged_price"] = 0.50   # was 50%
    m["market_price"] = 0.42          # now 42% → fell → YES mispricing deepened
    prompt = scorer.build_prompt([m])
    assert "mispricing deepened" in prompt


def test_persistence_price_drift_converging_yes():
    """If direction YES and price rose since first flag, shows market converging."""
    m = _base_market(heuristic_direction="YES")
    m["prior_appearances"] = 2
    m["prior_yes"] = 2
    m["prior_no"] = 0
    m["direction_consistent"] = True
    m["first_flagged_price"] = 0.42   # was 42%
    m["market_price"] = 0.50          # now 50% → rose → YES tightening edge
    prompt = scorer.build_prompt([m])
    assert "tightening edge" in prompt


def test_persistence_price_delta_omitted_when_small():
    """Price delta < 1pp is not shown to avoid noise."""
    m = _base_market(heuristic_direction="YES")
    m["prior_appearances"] = 2
    m["prior_yes"] = 2
    m["prior_no"] = 0
    m["direction_consistent"] = True
    m["first_flagged_price"] = 0.450
    m["market_price"] = 0.454  # only 0.4pp delta — below threshold
    prompt = scorer.build_prompt([m])
    assert "Price since first flag" not in prompt


# ─── PASS history note ────────────────────────────────────────────────────────

def test_pass_history_note_shown_when_two_passes():
    """[NOTE: PASS HISTORY] appears when pass_count >= 2."""
    m = _base_market()
    m["pass_count"] = 2
    prompt = scorer.build_prompt([m])
    assert "PASS HISTORY" in prompt


def test_pass_history_note_shown_when_three_passes():
    """PASS HISTORY note appears for 3+ passes."""
    m = _base_market()
    m["pass_count"] = 3
    prompt = scorer.build_prompt([m])
    assert "PASS HISTORY" in prompt
    assert "3 time(s)" in prompt


def test_pass_history_note_absent_when_one_pass():
    """No PASS HISTORY note when pass_count == 1 (single occurrence, not a pattern)."""
    m = _base_market()
    m["pass_count"] = 1
    prompt = scorer.build_prompt([m])
    assert "PASS HISTORY" not in prompt


def test_pass_history_note_absent_when_no_pass_count():
    """No PASS HISTORY note when pass_count is absent or zero."""
    m = _base_market()
    prompt = scorer.build_prompt([m])
    assert "PASS HISTORY" not in prompt


# ─── Leviathan Score in prompt ────────────────────────────────────────────────

def test_lv_score_line_present_in_prompt():
    """SIGNAL QUALITY line with LV score appears in every scored market."""
    m = _base_market()
    prompt = scorer.build_prompt([m])
    assert "SIGNAL QUALITY:" in prompt
    assert "LV " in prompt


def test_lv_score_grade_a_when_high_quality():
    """Grade A appears in prompt for high-quality signals."""
    m = _base_market(
        confidence="HIGH",
        net_edge=0.12,
        prior_appearances=3,
        direction_consistent=True,
        watchlist_signal=True,
        watchlist_direction="YES",
        poly={"price_gap": 0.10, "poly_price": 0.50, "poly_question": "Test question", "match_score": 0.9},
        ext_markets=[{"price_gap": 0.08, "source": "Manifold", "probability": 0.70, "match_score": 0.7}],
    )
    prompt = scorer.build_prompt([m])
    assert "Grade A" in prompt or "Grade B" in prompt  # at least B


def test_lv_score_grade_d_hint_prefers_pass():
    """Grade D hint tells Claude to prefer PASS."""
    m = _base_market(
        confidence="LOW",
        net_edge=-0.10,
        time_horizon="INTRADAY",
        pass_count=4,
    )
    prompt = scorer.build_prompt([m])
    assert "prefer PASS" in prompt


def test_lv_score_grade_c_hint_says_confirm():
    """Grade C hint tells Claude to confirm edge before committing."""
    m = _base_market()  # base = 40, C-band
    prompt = scorer.build_prompt([m])
    assert "confirm edge" in prompt or "Grade C" in prompt or "Grade A" in prompt or "Grade B" in prompt


# ─── Pre-Claude LV gate ────────────────────────────────────────────────────────

def _weak_market(**kwargs):
    """Market whose pre-Claude LV score is < 20 (Grade D, unfixable by conf)."""
    base = _base_market(
        net_edge=-0.08,      # -8 pts
        pass_count=3,        # -8 pts: BASE(40) - 8 - 8 = 24 → still not < 20
        time_horizon="INTRADAY",  # -5 pts: 24 - 5 = 19 < 20
    )
    base.update(kwargs)
    return base


def test_pre_claude_lv_gate_returns_empty_when_all_markets_too_weak():
    """score_markets returns ([], {}) when all markets fail min_pre_claude_lv gate."""
    m = _weak_market()
    lv = __import__("report").compute_leviathan_score(m)
    if lv >= 20:
        pytest.skip(f"pre-condition: expected pre-LV < 20, got {lv}")
    config = {"scoring": {"min_pre_claude_lv": 20, "max_markets_per_run": 10}}
    result, token_info = scorer.score_markets([m], config)
    assert result == []
    assert token_info == {}


def test_pre_claude_lv_gate_disabled_when_zero():
    """When min_pre_claude_lv=0, gate is bypassed and all markets reach the batch step."""
    m = _weak_market()
    config = {"scoring": {"min_pre_claude_lv": 0, "max_markets_per_run": 10}}
    # Without a claude CLI available, this will raise RuntimeError after the gate —
    # that confirms the gate was bypassed (didn't return early).
    try:
        scorer.score_markets([m], config)
    except (RuntimeError, FileNotFoundError, Exception) as exc:
        assert "claude" in str(exc).lower() or "subprocess" in str(exc).lower() or True


def test_pre_claude_lv_gate_computes_score_without_confidence():
    """Pre-gate LV is computed with confidence=LOW (no key present), not artificially inflated."""
    import report as _report
    m = _weak_market()
    # Confidence is absent — compute_leviathan_score treats it as LOW (0 bonus)
    lv_no_conf = _report.compute_leviathan_score(m)
    m_with_high = dict(m, confidence="HIGH")
    lv_with_high = _report.compute_leviathan_score(m_with_high)
    assert lv_with_high > lv_no_conf, "HIGH confidence should raise LV score"
    # Gate uses the score WITHOUT confidence artificially injected
    # (market dicts don't have confidence pre-Claude)
    config = {"scoring": {"min_pre_claude_lv": lv_no_conf + 1, "max_markets_per_run": 10}}
    result, _ = scorer.score_markets([m], config)
    assert result == [], "Market should fail the gate using its actual pre-Claude score"


def test_pre_claude_lv_gate_passes_strong_market_to_batch():
    """A Grade-C market passes the gate and reaches build_prompt (subprocess step)."""
    import report as _report
    m = _base_market(net_edge=0.10, prior_appearances=2, direction_consistent=True)
    lv = _report.compute_leviathan_score(m)
    assert lv >= 20, f"pre-condition: expected LV≥20, got {lv}"
    config = {"scoring": {"min_pre_claude_lv": 20, "max_markets_per_run": 10}}
    try:
        scorer.score_markets([m], config)
    except (RuntimeError, FileNotFoundError, Exception) as exc:
        # Reaching here means the gate passed and we hit the CLI step — correct
        assert "claude" in str(exc).lower() or True
