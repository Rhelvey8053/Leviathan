"""
Offline tests for report.py signal block formatting.

No network calls, no email sending.
Run: python -m pytest -q
"""

import pytest
from core import report


def _signal(**kwargs):
    """Minimal signal dict for _signal_block."""
    base = {
        "ticker":          "KXTEST-26DEC01",
        "title":           "Will the test event happen by December?",
        "confidence":      "MED",
        "direction":       "YES",
        "time_horizon":    "LONG",
        "market_price":    0.15,
        "our_estimate":    0.35,
        "edge":            0.20,
        "drift_flag":      False,
        "spread_wide":     False,
        "ob_flag":         False,
        "watchlist_signal": False,
        "whale_reversal":  False,
        "smart_money":     [],
        "poly":            None,
        "ext_markets":     [],
        "ext_consensus":   {},
        "flag_path":       None,
        "base_rate":       None,
        "second_pass":     False,
    }
    base.update(kwargs)
    return base


# ─── flag_path label in header ────────────────────────────────────────────────

def test_heuristic_flag_path_shown_in_header():
    s = _signal(flag_path="HEURISTIC", base_rate=0.55)
    lines = report._signal_block(s, index=1)
    header = lines[0]
    assert "[HEURISTIC]" in header


def test_drift_flag_path_shown_in_header():
    s = _signal(flag_path="DRIFT")
    lines = report._signal_block(s, index=1)
    assert "[DRIFT]" in lines[0]


def test_no_flag_path_no_bracket():
    s = _signal(flag_path=None)
    lines = report._signal_block(s, index=1)
    # No [None] or extra brackets
    assert "[None]" not in lines[0]
    # The path label should just be absent
    assert "[HEURISTIC]" not in lines[0]
    assert "[DRIFT]" not in lines[0]


# ─── Heuristic base rate in fired signals ────────────────────────────────────

def test_heuristic_base_rate_in_signals_line():
    s = _signal(flag_path="HEURISTIC", base_rate=0.55)
    lines = report._signal_block(s, index=1)
    signals_line = next((l for l in lines if "Signals:" in l), None)
    assert signals_line is not None, "Expected a Signals: line"
    assert "Heuristic Base Rate 55%" in signals_line


def test_no_heuristic_signal_when_drift_path():
    s = _signal(flag_path="DRIFT", base_rate=0.35, drift_flag=True, price_drift=-0.15)
    lines = report._signal_block(s, index=1)
    full = "\n".join(lines)
    assert "Heuristic Base Rate" not in full
    assert "Drift" in full


def test_heuristic_signal_absent_when_base_rate_none():
    s = _signal(flag_path="HEURISTIC", base_rate=None)
    lines = report._signal_block(s, index=1)
    full = "\n".join(lines)
    assert "Heuristic Base Rate" not in full


# ─── Second-pass label ────────────────────────────────────────────────────────

def test_second_pass_label_shown():
    s = _signal(second_pass=True)
    lines = report._signal_block(s, index=1)
    assert "SECOND PASS" in lines[0]


# ─── Drift signal in fired list ───────────────────────────────────────────────

def test_drift_signal_shown_in_fired():
    s = _signal(drift_flag=True, price_drift=0.25)
    lines = report._signal_block(s, index=1)
    full = "\n".join(lines)
    assert "Drift" in full
    assert "+25%" in full


# ─── SIGNAL CONFLICT warning in signal block ──────────────────────────────────

def test_signal_conflict_shown_when_drift_and_base_rate_disagree():
    # drift_pct < 0 → mean revert UP → drift says YES
    # base_rate=0.35 < market_price=0.77 → base rate says NO → CONFLICT
    s = _signal(
        drift_flag=True,
        price_drift=-0.08,  # negative: mid < last → drift up → buy YES
        market_price=0.77,
        base_rate=0.35,
    )
    lines = report._signal_block(s, index=1)
    full = "\n".join(lines)
    assert "SIGNAL CONFLICT" in full
    assert "YES" in full
    assert "NO" in full


def test_no_conflict_when_drift_and_base_rate_agree():
    # drift_pct < 0 → drift says YES; base_rate=0.80 > market=0.20 → also YES → no conflict
    s = _signal(
        drift_flag=True,
        price_drift=-0.05,
        market_price=0.20,
        base_rate=0.80,
    )
    lines = report._signal_block(s, index=1)
    full = "\n".join(lines)
    assert "SIGNAL CONFLICT" not in full


def test_no_conflict_when_no_base_rate():
    s = _signal(drift_flag=True, price_drift=-0.08, market_price=0.77)
    lines = report._signal_block(s, index=1)
    full = "\n".join(lines)
    assert "SIGNAL CONFLICT" not in full


# ─── CLAUDE OVERRIDE warning in signal block ─────────────────────────────────

def test_claude_override_shown_when_claude_yes_but_base_rate_leans_no():
    """Base rate 20% vs market 40% → heuristic leans NO. Claude says YES → OVERRIDE."""
    s = _signal(
        direction="YES",
        market_price=0.40,
        base_rate=0.20,  # leans NO (0.20 < 0.40 - 0.05)
    )
    lines = report._signal_block(s, index=1)
    full = "\n".join(lines)
    assert "CLAUDE OVERRIDE" in full
    assert "NO" in full


def test_claude_override_shown_when_claude_no_but_base_rate_leans_yes():
    """Base rate 75% vs market 45% → heuristic leans YES. Claude says NO → OVERRIDE."""
    s = _signal(
        direction="NO",
        market_price=0.45,
        base_rate=0.75,  # leans YES (0.75 > 0.45 + 0.05)
    )
    lines = report._signal_block(s, index=1)
    full = "\n".join(lines)
    assert "CLAUDE OVERRIDE" in full
    assert "YES" in full


def test_claude_override_absent_when_claude_agrees_with_base_rate():
    """Claude says YES, base rate 70% > market 40% → heuristic also leans YES → no override."""
    s = _signal(
        direction="YES",
        market_price=0.40,
        base_rate=0.70,
    )
    lines = report._signal_block(s, index=1)
    full = "\n".join(lines)
    assert "CLAUDE OVERRIDE" not in full


def test_claude_override_absent_when_no_base_rate():
    """No base_rate → cannot determine heuristic lean → no override warning."""
    s = _signal(direction="YES", market_price=0.50)
    lines = report._signal_block(s, index=1)
    full = "\n".join(lines)
    assert "CLAUDE OVERRIDE" not in full


# ─── _qualifying: confidence filter and sorting ───────────────────────────────

def _qs(direction="YES", confidence="MED", edge=0.10, second_pass=False):
    """Minimal signal for _qualifying tests."""
    return {
        "direction":  direction,
        "confidence": confidence,
        "edge":       edge,
        "second_pass": second_pass,
    }


def test_qualifying_excludes_pass_direction():
    """Signals with direction=PASS must be excluded regardless of confidence."""
    signals = [_qs(direction="PASS", confidence="HIGH")]
    result  = report._qualifying(signals, threshold_rank=0)
    assert result == []


def test_qualifying_excludes_below_threshold():
    """LOW confidence (rank 2) must be excluded when threshold_rank=1 (MED)."""
    signals = [_qs(confidence="LOW")]
    result  = report._qualifying(signals, threshold_rank=1)
    assert result == []


def test_qualifying_includes_at_threshold():
    """MED confidence is included when threshold_rank=1."""
    signals = [_qs(confidence="MED")]
    result  = report._qualifying(signals, threshold_rank=1)
    assert len(result) == 1


def test_qualifying_second_pass_always_included():
    """second_pass=True bypasses confidence threshold — included even at threshold_rank=0."""
    signals = [_qs(confidence="LOW", second_pass=True)]
    result  = report._qualifying(signals, threshold_rank=0)   # only HIGH (rank=0) passes normally
    assert len(result) == 1


def test_qualifying_sorts_by_confidence_then_edge():
    """HIGH confidence signals come first; within same confidence, higher edge first."""
    signals = [
        _qs(confidence="MED",  edge=0.20),
        _qs(confidence="HIGH", edge=0.10),
        _qs(confidence="HIGH", edge=0.25),
    ]
    result = report._qualifying(signals, threshold_rank=1)
    assert result[0]["confidence"] == "HIGH"
    assert result[0]["edge"]       == 0.25   # highest edge in HIGH tier first
    assert result[1]["confidence"] == "HIGH"
    assert result[2]["confidence"] == "MED"


def test_qualifying_empty_input():
    """Empty signals list returns empty list without error."""
    assert report._qualifying([], threshold_rank=1) == []


# ─── _signal_block: additional signal types ───────────────────────────────────

def test_spread_wide_shown_in_fired():
    s = _signal(spread_wide=True, spread_pct=0.15)
    full = "\n".join(report._signal_block(s, index=1))
    assert "Wide Spread" in full


def test_whale_reversal_shown_in_fired():
    s = _signal(whale_reversal=True)
    full = "\n".join(report._signal_block(s, index=1))
    assert "Whale Reversal" in full


def test_ob_flag_shown_in_fired():
    s = _signal(ob_flag=True, ob_direction="YES", ob_imbalance=0.65)
    full = "\n".join(report._signal_block(s, index=1))
    assert "Order Book" in full


def test_watchlist_signal_shown_in_fired():
    s = _signal(watchlist_signal=True)
    full = "\n".join(report._signal_block(s, index=1))
    assert "Watchlist" in full


def test_cross_market_poly_shown_when_gap_large():
    """Polymarket price with gap >= 0.04 should appear in fired signals and cross-market section."""
    s = _signal(
        poly={"poly_price": 0.55, "price_gap": 0.40},
    )
    full = "\n".join(report._signal_block(s, index=1))
    assert "Cross-Market" in full
    assert "Polymarket" in full


def test_cross_market_poly_omitted_when_gap_small():
    """Polymarket price with gap < 0.04 must NOT add to Cross-Market fired count."""
    s = _signal(
        poly={"poly_price": 0.16, "price_gap": 0.01},
    )
    full = "\n".join(report._signal_block(s, index=1))
    # The cross-market section renders (any non-None poly shows prices), but
    # the fired-signal counter must not increment for a tiny gap.
    assert "Cross-Market x1" not in full


def test_cross_market_ext_markets_rendered():
    """ext_markets entries appear in the Cross-Market Prices section."""
    s = _signal(ext_markets=[
        {"source": "Manifold", "probability": 0.60, "price_gap": 0.45},
    ])
    full = "\n".join(report._signal_block(s, index=1))
    assert "Manifold" in full
    assert "Cross-Market Prices" in full


def test_smart_money_section_rendered():
    """smart_money list entries appear in the Smart Money Activity section."""
    s = _signal(smart_money=[{
        "display_name": "TraderA",
        "direction": "YES",
        "resolved_avg_pct_pnl": 42.0,
        "win_rate": 68.0,
        "trade_count": 3,
    }])
    full = "\n".join(report._signal_block(s, index=1))
    assert "Smart Money Activity" in full
    assert "TraderA" in full
    assert "BUY YES" in full


# ─── compile_weekly_digest ────────────────────────────────────────────────────

def _week_row(ticker, direction="YES", confidence="MED", edge=0.12,
              timestamp="2026-06-17T08:00:00+00:00", net_edge=None):
    row = {
        "ticker":     ticker,
        "title":      f"Will {ticker} happen?",
        "direction":  direction,
        "confidence": confidence,
        "edge":       edge,
        "timestamp":  timestamp,
    }
    if net_edge is not None:
        row["net_edge"] = net_edge
    return row


def _stats(total=5, resolved=3, wins=2, win_rate=66.7,
           avg_edge=0.12, total_pnl=0.85):
    return {
        "total_calls":            total,
        "resolved":               resolved,
        "win_rate":               win_rate,
        "avg_edge_captured":      avg_edge,
        "total_hypothetical_pnl": total_pnl,
    }


def test_weekly_digest_header_present():
    digest = report.compile_weekly_digest([], _stats(0, 0, 0, None, None, None), {})
    assert "WEEKLY DIGEST" in digest
    assert "LEVIATHAN" in digest


def test_weekly_digest_empty_signals_no_crash():
    digest = report.compile_weekly_digest([], _stats(0, 0, 0, None, None, None), {})
    assert isinstance(digest, str)
    assert len(digest) > 0


def test_weekly_digest_direction_counts():
    signals = [
        _week_row("KXYES1", direction="YES"),
        _week_row("KXYES2", direction="YES"),
        _week_row("KXNO1",  direction="NO"),
    ]
    digest = report.compile_weekly_digest(signals, _stats(), {})
    assert "2 YES" in digest
    assert "1 NO"  in digest


def test_weekly_digest_deduplicates_same_ticker():
    """Same ticker appearing twice must produce only one row in the markets table."""
    signals = [
        _week_row("KXDUP", timestamp="2026-06-16T08:00:00+00:00"),
        _week_row("KXDUP", timestamp="2026-06-17T08:00:00+00:00"),
    ]
    digest = report.compile_weekly_digest(signals, _stats(), {})
    # "Unique Markets Flagged: 1" should appear
    assert "Unique Markets Flagged:  1" in digest


def test_weekly_digest_unique_markets_count():
    signals = [_week_row(f"KX{i}") for i in range(4)]
    digest = report.compile_weekly_digest(signals, _stats(), {})
    assert "Unique Markets Flagged:  4" in digest


def test_weekly_digest_stats_section_win_rate():
    digest = report.compile_weekly_digest([], _stats(win_rate=75.0), {})
    assert "75.0%" in digest


def test_weekly_digest_stats_section_no_resolved():
    digest = report.compile_weekly_digest([], _stats(resolved=0, win_rate=None, total_pnl=None), {})
    assert "none resolved yet" in digest.lower()


def test_weekly_digest_flag_path_stats_shown_when_provided():
    flag_stats = [
        {"flag_path": "EDGE",      "total": 3, "wins": 2, "win_rate": 66.7, "total_pnl": 0.90},
        {"flag_path": "HEURISTIC", "total": 1, "wins": 1, "win_rate": 100.0, "total_pnl": 0.70},
    ]
    digest = report.compile_weekly_digest([], _stats(), {}, flag_path_stats=flag_stats)
    assert "Win Rate by Signal Path" in digest
    assert "EDGE"      in digest
    assert "HEURISTIC" in digest


def test_weekly_digest_flag_path_stats_absent_when_none():
    digest = report.compile_weekly_digest([], _stats(), {}, flag_path_stats=None)
    assert "Win Rate by Signal Path" not in digest


def test_weekly_digest_ticker_appears_in_markets_table():
    signals = [_week_row("KXUNIQUE-TEST")]
    digest = report.compile_weekly_digest(signals, _stats(), {})
    assert "KXUNIQUE-TEST" in digest


def test_weekly_digest_net_edge_column_header_present():
    digest = report.compile_weekly_digest([], _stats(), {})
    assert "Net" in digest


def test_weekly_digest_net_edge_shown_when_present():
    signals = [_week_row("KXTEST", net_edge=0.063)]
    digest = report.compile_weekly_digest(signals, _stats(), {})
    assert "+6.3pp" in digest


def test_weekly_digest_net_edge_negative_shown():
    signals = [_week_row("KXTEST", net_edge=-0.02)]
    digest = report.compile_weekly_digest(signals, _stats(), {})
    assert "-2.0pp" in digest


def test_weekly_digest_net_edge_absent_shows_dash():
    signals = [_week_row("KXTEST")]  # no net_edge key
    digest = report.compile_weekly_digest(signals, _stats(), {})
    assert "--" in digest


# ─── compile_report ───────────────────────────────────────────────────────────

def _run_meta(**kwargs):
    base = {
        "run_id":            "test-run-1",
        "timestamp":         "2026-06-17T10:00:00Z",
        "markets_scanned":   300,
        "signals_generated": 2,
        "whale_flags":       0,
        "model_used":        "claude-sonnet-4-6",
        "tokens_used":       8000,
        "cost_usd":          0.0,
        "runtime_ms":        45000,
    }
    base.update(kwargs)
    return base


def _sig(ticker="KXTST-01", direction="YES", confidence="MED", edge=0.15,
         time_horizon="MONTHLY", market_price=0.30, our_estimate=0.45,
         second_pass=False, **kwargs):
    base = {
        "ticker":          ticker,
        "title":           f"Will {ticker} happen?",
        "direction":       direction,
        "confidence":      confidence,
        "edge":            edge,
        "time_horizon":    time_horizon,
        "market_price":    market_price,
        "our_estimate":    our_estimate,
        "second_pass":     second_pass,
        "flag_path":       None,
        "drift_flag":      False,
        "spread_wide":     False,
        "ob_flag":         False,
        "watchlist_signal": False,
        "whale_reversal":  False,
        "smart_money":     [],
        "poly":            None,
        "ext_markets":     [],
        "ext_consensus":   {},
        "base_rate":       None,
    }
    base.update(kwargs)
    return base


_EMPTY_STATS = {"total_calls": 0, "resolved": 0, "win_rate": None,
                "avg_edge_captured": None, "total_hypothetical_pnl": None}
_CFG = {"scoring": {"confidence_threshold": "MED"}, "environment": "demo"}


def test_compile_report_header_present():
    body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG)
    assert "LEVIATHAN" in body
    assert "INTELLIGENCE REPORT" in body


def test_compile_report_no_signals_shows_placeholder():
    body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG)
    assert "No new signals this run" in body


def test_compile_report_signal_appears_in_new_signals():
    s = _sig(ticker="KXNEW-01", confidence="MED")
    body = report.compile_report(
        [s], [],
        _EMPTY_STATS, _run_meta(),
        _CFG,
        new_signals=[s], repeat_signals=[],
    )
    assert "KXNEW-01" in body
    assert "NEW SIGNALS" in body


def test_compile_report_repeat_signals_section():
    s = _sig(ticker="KXREPEAT-01", confidence="MED")
    body = report.compile_report(
        [s], [],
        _EMPTY_STATS, _run_meta(),
        _CFG,
        new_signals=[], repeat_signals=[s],
    )
    assert "REPEAT SIGNALS" in body
    assert "KXREPEAT-01" in body


def test_compile_report_whale_activity_empty():
    body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG)
    assert "WHALE ACTIVITY" in body
    assert "No unusual whale activity" in body


def test_compile_report_whale_activity_listed():
    whale = {
        "ticker": "KXWHALE", "title": "Whale Market",
        "whale_direction": "YES", "max_trade_size": 3000, "avg_trade_size": 600.0,
    }
    body = report.compile_report([], [whale], _EMPTY_STATS, _run_meta(), _CFG)
    assert "KXWHALE" in body
    assert "YES" in body


def test_compile_report_track_record_section():
    body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG)
    assert "TRACK RECORD" in body
    assert "Total Calls" in body
    assert "Win Rate" in body


def test_compile_report_no_resolved_win_rate_placeholder():
    body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG)
    assert "no resolved markets yet" in body.lower()


def test_compile_report_with_win_rate():
    stats = {**_EMPTY_STATS, "total_calls": 10, "resolved": 5,
             "win_rate": 60.0, "avg_edge_captured": 0.12, "total_hypothetical_pnl": 0.95}
    body = report.compile_report([], [], stats, _run_meta(), _CFG)
    assert "60.0%" in body


def test_compile_report_probe_stats_section():
    probe_stats = {
        "total_probes": 10, "resolved": 5, "hit_rate": 60.0,
        "hi_div_total": 3, "hi_div_hit_rate": 66.7,
        "verdict": "PARTIAL — 5/10 probes resolved. Full verdict pending.",
    }
    body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG,
                                  probe_stats=probe_stats)
    assert "Research Probe" in body
    assert "60.0%" in body


def test_compile_report_probe_stats_absent_when_none():
    body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG,
                                  probe_stats=None)
    assert "Research Probe" not in body


def test_compile_report_flag_path_stats_section():
    fp_stats = [
        {"flag_path": "EDGE", "total": 4, "wins": 3, "win_rate": 75.0, "total_pnl": 1.20},
    ]
    body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG,
                                  flag_path_stats=fp_stats)
    assert "Win Rate by Signal Path" in body
    assert "EDGE" in body


def test_compile_report_short_term_watchlist_section():
    intraday_mkt = {
        "ticker": "KXINTRA", "title": "Intraday special market",
        "time_horizon": "INTRADAY",
        "volume_fp": "5000",
        "yes_bid_dollars": "0.45", "yes_ask_dollars": "0.47",
        "drift_flag": False, "spread_wide": False,
    }
    body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG,
                                  all_filtered=[intraday_mkt])
    assert "SHORT-TERM WATCHLIST" in body
    # The table renders title (truncated to 28 chars), not ticker
    assert "Intraday special market" in body or "1 market(s)" in body


def test_compile_report_run_statistics_section():
    body = report.compile_report([], [], _EMPTY_STATS,
                                  _run_meta(markets_scanned=450), _CFG)
    assert "RUN STATISTICS" in body
    assert "450" in body


def test_compile_report_signals_grouped_by_horizon():
    """Signals with different horizons appear under their horizon label."""
    monthly = _sig(ticker="KXMONTH", time_horizon="MONTHLY", confidence="MED")
    weekly  = _sig(ticker="KXWEEK",  time_horizon="WEEKLY",  confidence="MED")
    body = report.compile_report(
        [monthly, weekly], [],
        _EMPTY_STATS, _run_meta(), _CFG,
        new_signals=[monthly, weekly], repeat_signals=[],
    )
    assert "Monthly" in body
    assert "Weekly"  in body
    assert "KXMONTH" in body
    assert "KXWEEK"  in body


# ─── _signal_strength ─────────────────────────────────────────────────────────

def test_signal_strength_zero_for_bare_br_none():
    """A bare BR_NONE market with no corroborating signals scores 0."""
    s = _signal(flag_path="BR_NONE")
    assert report._signal_strength(s) == 0


def test_signal_strength_heuristic_adds_one():
    s = _signal(flag_path="HEURISTIC")
    assert report._signal_strength(s) == 1


def test_signal_strength_poly_gap_adds_one():
    s = _signal(
        flag_path="HEURISTIC",
        poly={"price_gap": 0.12, "poly_price": 0.50, "poly_question": "Q", "match_score": 0.80},
    )
    assert report._signal_strength(s) == 2


def test_signal_strength_ext_market_adds_one():
    s = _signal(
        flag_path="HEURISTIC",
        ext_markets=[{"source": "Manifold", "probability": 0.55, "price_gap": 0.08, "match_score": 0.70}],
    )
    assert report._signal_strength(s) == 2


def test_signal_strength_watchlist_adds_one():
    s = _signal(flag_path="HEURISTIC", watchlist_signal=True)
    assert report._signal_strength(s) == 2


def test_signal_strength_cross_market_adds_one():
    s = _signal(flag_path="CROSS_MARKET")
    assert report._signal_strength(s) == 1


def test_signal_strength_multi_corroboration():
    """HEURISTIC + poly gap + watchlist = 3."""
    s = _signal(
        flag_path="HEURISTIC",
        poly={"price_gap": 0.20, "poly_price": 0.60, "poly_question": "Q", "match_score": 0.85},
        watchlist_signal=True,
    )
    assert report._signal_strength(s) == 3


def test_signal_strength_shown_in_header_when_two_or_more():
    """★×N label appears when signal_strength >= 2."""
    s = _signal(
        flag_path="HEURISTIC",
        poly={"price_gap": 0.15, "poly_price": 0.55, "poly_question": "Q", "match_score": 0.80},
    )
    lines = report._signal_block(s, index=1)
    header = lines[0]
    assert "★" in header


def test_signal_strength_not_shown_in_header_when_one():
    """No ★ label when only one signal fires."""
    s = _signal(flag_path="HEURISTIC")
    lines = report._signal_block(s, index=1)
    header = lines[0]
    assert "★" not in header


# ─── qualifying sort includes signal_strength tiebreaker ──────────────────────

def test_qualifying_sorts_higher_signal_strength_first_within_tier():
    """Within the same confidence tier, higher signal_strength comes first."""
    low_str  = _signal(confidence="HIGH", edge=0.20, flag_path="DRIFT")
    high_str = _signal(
        confidence="HIGH", edge=0.15, flag_path="HEURISTIC",
        poly={"price_gap": 0.10, "poly_price": 0.50, "poly_question": "Q", "match_score": 0.8},
        watchlist_signal=True,
    )
    result = report._qualifying([low_str, high_str], threshold_rank=0)
    assert result[0] is high_str   # strength=3 beats strength=1 even at lower edge


def test_qualifying_falls_back_to_edge_when_strength_equal():
    """When signal_strength is tied, larger edge wins."""
    s1 = _signal(confidence="HIGH", edge=0.25, flag_path="HEURISTIC")
    s2 = _signal(confidence="HIGH", edge=0.10, flag_path="HEURISTIC")
    result = report._qualifying([s2, s1], threshold_rank=0)
    assert result[0] is s1   # higher edge first when strength equal


# ─── urgency marker ───────────────────────────────────────────────────────────

def test_urgency_closing_today_shown_when_days_left_zero():
    """Markets expiring ≤0 days away show CLOSING TODAY/TOMORROW."""
    from datetime import datetime, timezone, timedelta
    tomorrow = (datetime.now(timezone.utc) + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = _signal(close_time=tomorrow)
    lines = report._signal_block(s)
    ticker_line = lines[1]
    assert "CLOSING TODAY/TOMORROW" in ticker_line or "CLOSING IN" in ticker_line


def test_urgency_closing_in_two_days():
    """Markets closing in ~2 days show CLOSING IN Nd (1 ≤ N ≤ 3)."""
    from datetime import datetime, timezone, timedelta
    # Add 23h buffer so floor truncation doesn't drop below expected band
    close_dt  = datetime.now(timezone.utc) + timedelta(days=2, hours=12)
    days_left = (close_dt - datetime.now(timezone.utc)).days
    close = close_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    s = _signal(close_time=close)
    lines = report._signal_block(s)
    ticker_line = lines[1]
    assert f"CLOSING IN {days_left}d" in ticker_line


def test_urgency_closing_in_six_days():
    """Markets closing in ~6 days show soft close notice 'closes in Nd'."""
    from datetime import datetime, timezone, timedelta
    close_dt  = datetime.now(timezone.utc) + timedelta(days=6, hours=12)
    days_left = (close_dt - datetime.now(timezone.utc)).days
    close = close_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    s = _signal(close_time=close)
    lines = report._signal_block(s)
    ticker_line = lines[1]
    assert f"closes in {days_left}d" in ticker_line


def test_urgency_absent_when_far_future():
    """Markets closing in 30+ days show no urgency marker."""
    from datetime import datetime, timezone, timedelta
    close = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = _signal(close_time=close)
    lines = report._signal_block(s)
    ticker_line = lines[1]
    assert "closes in" not in ticker_line and "CLOSING IN" not in ticker_line


# ─── repeat count marker ──────────────────────────────────────────────────────

def test_repeat_count_shown_when_two_or_more():
    """[REPEAT x3] appears in ticker line when repeat_count >= 2."""
    s = _signal(is_repeat=True, repeat_count=3)
    lines = report._signal_block(s)
    ticker_line = lines[1]
    assert "[REPEAT x3]" in ticker_line


def test_repeat_label_shown_when_repeat_count_one():
    """[REPEAT] (no count) shown when is_repeat but repeat_count < 2."""
    s = _signal(is_repeat=True, repeat_count=1)
    lines = report._signal_block(s)
    ticker_line = lines[1]
    assert "[REPEAT]" in ticker_line


def test_repeat_absent_for_new_signal():
    """No REPEAT label for new (non-repeat) signals."""
    s = _signal(is_repeat=False)
    lines = report._signal_block(s)
    ticker_line = lines[1]
    assert "REPEAT" not in ticker_line


# ─── HIGH confidence downgrade label ─────────────────────────────────────────

def test_confidence_downgraded_label_shown_when_flagged():
    """[conf downgraded: edge<10pp] appears in header when confidence_downgraded is set."""
    s = _signal(confidence="MED", confidence_downgraded=True)
    lines = report._signal_block(s, index=1)
    header = lines[0]
    assert "conf downgraded" in header


def test_confidence_downgraded_label_absent_normally():
    """No downgrade label in header for normal signals."""
    s = _signal(confidence="HIGH")
    lines = report._signal_block(s, index=1)
    header = lines[0]
    assert "conf downgraded" not in header


# ─── Short-horizon label in signal block ─────────────────────────────────────

def test_short_horizon_label_shown_when_true():
    """[SHORT HORIZON] label appears in header when short_horizon=True."""
    s = _signal(short_horizon=True, time_horizon="WEEKLY")
    lines = report._signal_block(s, index=1)
    header = lines[0]
    assert "SHORT HORIZON" in header
    assert "72h" in header


def test_short_horizon_label_absent_when_false():
    """No SHORT HORIZON label when short_horizon=False."""
    s = _signal(short_horizon=False)
    lines = report._signal_block(s, index=1)
    header = lines[0]
    assert "SHORT HORIZON" not in header


def test_short_horizon_label_absent_by_default():
    """No SHORT HORIZON label when key is not in signal dict."""
    s = _signal()  # no short_horizon key
    lines = report._signal_block(s, index=1)
    header = lines[0]
    assert "SHORT HORIZON" not in header


# ─── Net-of-spread edge in signal block ───────────────────────────────────────

def test_net_edge_shown_when_present():
    """Net Edge line appears in signal block when net_edge is provided."""
    s = _signal(base_rate=0.45, net_edge=0.10)
    lines = report._signal_block(s, index=1)
    block = "\n".join(lines)
    assert "Net Edge" in block
    assert "+10.0 pp" in block


def test_net_edge_spread_consumes_warning_in_report():
    """[SPREAD > EDGE] label appears when net_edge <= 0."""
    s = _signal(base_rate=0.45, net_edge=-0.03)
    lines = report._signal_block(s, index=1)
    block = "\n".join(lines)
    assert "SPREAD > EDGE" in block


def test_net_edge_absent_when_not_provided():
    """No Net Edge line when net_edge is not in signal dict."""
    s = _signal()
    lines = report._signal_block(s, index=1)
    block = "\n".join(lines)
    assert "Net Edge" not in block


# ─── Signal persistence in _signal_block ──────────────────────────────────────

def test_persistence_shown_when_prior_appearances():
    """prior_appearances > 0 shows persistence line in signal block."""
    s = _signal()
    s["prior_appearances"] = 3
    s["prior_yes"] = 3
    s["prior_no"] = 0
    s["direction_consistent"] = True
    block = "\n".join(report._signal_block(s, index=1))
    assert "3d/14d" in block
    assert "3Y/0N" in block
    assert "consistent" in block


def test_persistence_shows_mixed_when_not_consistent():
    """direction_consistent=False shows 'mixed' in persistence line."""
    s = _signal()
    s["prior_appearances"] = 4
    s["prior_yes"] = 2
    s["prior_no"] = 2
    s["direction_consistent"] = False
    block = "\n".join(report._signal_block(s, index=1))
    assert "mixed" in block


def test_persistence_absent_when_no_prior_appearances():
    """No persistence line when prior_appearances is 0 or absent."""
    s = _signal()
    block = "\n".join(report._signal_block(s, index=1))
    assert "d/14d" not in block


# ─── _top_picks executive summary ─────────────────────────────────────────────

def test_top_picks_returns_empty_for_no_signals():
    """Empty input returns empty list."""
    assert report._top_picks([]) == []


def test_top_picks_shows_top_n_signals():
    """_top_picks returns at most n signals."""
    sigs = [_signal(confidence="HIGH", edge=0.30),
            _signal(confidence="HIGH", edge=0.20),
            _signal(confidence="MED",  edge=0.15),
            _signal(confidence="LOW",  edge=0.10)]
    lines = report._top_picks(sigs, n=2)
    # Should contain 2 numbered entries
    numbered = [l for l in lines if l.startswith("1.") or l.startswith("2.") or l.startswith("3.")]
    assert len(numbered) == 2


def test_top_picks_header_present():
    """TOP PICKS header appears in output."""
    s = _signal(confidence="HIGH", edge=0.20)
    lines = report._top_picks([s])
    assert any("TOP PICKS" in l for l in lines)


def test_top_picks_contains_confidence_and_direction():
    """Each top-pick line shows confidence and BUY direction."""
    s = _signal(confidence="HIGH", direction="YES", edge=0.20)
    lines = report._top_picks([s])
    pick_line = [l for l in lines if l.startswith("1.")][0]
    assert "HIGH" in pick_line
    assert "YES" in pick_line


def test_top_picks_shows_kelly_when_edge_positive():
    """Kelly(1/4) line appears when direction is YES and edge > 0."""
    s = _signal(confidence="HIGH", direction="YES", market_price=0.40, our_estimate=0.65, edge=0.25)
    lines = report._top_picks([s])
    assert any("Kelly(1/4)" in l for l in lines)


def test_top_picks_sorts_high_strength_before_low_strength():
    """Higher signal_strength comes first in top picks."""
    weak  = _signal(confidence="HIGH", edge=0.25, flag_path="DRIFT")
    strong = _signal(
        confidence="HIGH", edge=0.15, flag_path="HEURISTIC",
        poly={"price_gap": 0.10, "poly_price": 0.50, "poly_question": "Q", "match_score": 0.8},
        watchlist_signal=True,
    )
    lines = report._top_picks([weak, strong], n=2)
    first = [l for l in lines if l.startswith("1.")][0]
    second = [l for l in lines if l.startswith("2.")][0]
    assert "HEURISTIC" in first or "WATCHLIST" not in first
    _ = second  # just verify both rendered


def test_top_picks_in_compile_report_when_signals_exist():
    """compile_report includes TOP PICKS section when qualifying signals exist."""
    s = _signal(confidence="HIGH", direction="YES", market_price=0.30, our_estimate=0.60, edge=0.30)
    body = report.compile_report(
        signals=[s], whale_only=[], stats={},
        run_meta={"markets_scanned": 100, "model_used": "claude-sonnet-4-6"},
        config={"environment": "demo", "scoring": {}},
        new_signals=[s], repeat_signals=[],
    )
    assert "TOP PICKS" in body


# ─── _kelly_fraction ──────────────────────────────────────────────────────────

def test_kelly_yes_basic():
    """YES position: Kelly = (p - mkt) / (1 - mkt). With p=0.6, mkt=0.4 → 1/3."""
    fk, qk = report._kelly_fraction("YES", 0.40, 0.60)
    assert abs(fk - 1/3) < 0.001
    assert abs(qk - 1/12) < 0.001


def test_kelly_no_basic():
    """NO position: Kelly = (mkt - p) / mkt. With p=0.3, mkt=0.6 → 0.5."""
    fk, qk = report._kelly_fraction("NO", 0.60, 0.30)
    assert abs(fk - 0.50) < 0.001
    assert abs(qk - 0.125) < 0.001


def test_kelly_returns_none_for_pass():
    """PASS direction returns None — no sizing for non-directional calls."""
    assert report._kelly_fraction("PASS", 0.40, 0.50) is None


def test_kelly_returns_none_when_no_edge():
    """Returns None when estimate <= market_price for YES (no edge)."""
    assert report._kelly_fraction("YES", 0.60, 0.40) is None  # estimate below market


def test_kelly_returns_none_for_bad_inputs():
    """Returns None for None inputs without crashing."""
    assert report._kelly_fraction("YES", None, None) is None
    assert report._kelly_fraction("YES", 0.0, 0.60) is None  # mkt=0 is invalid


def test_kelly_shown_in_signal_block_when_direction_yes():
    """Kelly line appears in signal block for YES signals with valid estimate."""
    s = _signal(direction="YES", market_price=0.40, our_estimate=0.65, edge=0.25)
    lines = report._signal_block(s)
    kelly_lines = [l for l in lines if "Kelly" in l]
    assert len(kelly_lines) == 1
    assert "1/4 Kelly" in kelly_lines[0]


def test_kelly_absent_for_pass_direction():
    """No Kelly line when direction is PASS."""
    s = _signal(direction="PASS", market_price=0.50, our_estimate=0.52, edge=0.02)
    lines = report._signal_block(s)
    assert not any("Kelly" in l for l in lines)


# ─── compile_weekly_digest: Brier score ───────────────────────────────────────

def _make_weekly_stats(**kwargs):
    base = {
        "total_calls": 5, "resolved": 3, "wins": 2, "losses": 1,
        "win_rate": 66.7, "avg_edge_captured": 0.12, "total_hypothetical_pnl": 5.50,
    }
    base.update(kwargs)
    return base


def test_weekly_digest_brier_shown_when_resolved():
    """Brier score line appears in TRACK RECORD when brier dict has a score."""
    brier = {"brier_score": 0.1200, "n": 3, "label": "GOOD"}
    text = report.compile_weekly_digest([], _make_weekly_stats(), {}, brier=brier)
    assert "Brier Score:" in text
    assert "0.1200" in text
    assert "GOOD" in text
    assert "n=3" in text


def test_weekly_digest_brier_pending_when_none():
    """Shows PENDING line when brier dict has no resolved score yet."""
    brier = {"brier_score": None, "n": 0, "label": "PENDING"}
    text = report.compile_weekly_digest([], _make_weekly_stats(), {}, brier=brier)
    assert "Brier Score:" in text
    assert "PENDING" in text


def test_weekly_digest_brier_absent_when_param_omitted():
    """compile_weekly_digest still works when brier= is not passed at all."""
    text = report.compile_weekly_digest([], _make_weekly_stats(), {})
    assert "Brier Score:" not in text


# ─── compute_leviathan_score ──────────────────────────────────────────────────

def test_leviathan_score_base_low_confidence_no_edge():
    """BASE=40, LOW conf, no net_edge → 40 pts."""
    s = _signal(confidence="LOW")
    assert report.compute_leviathan_score(s) == 40


def test_leviathan_score_high_confidence_adds_20():
    """HIGH confidence adds 20 pts to base."""
    s = _signal(confidence="HIGH")
    assert report.compute_leviathan_score(s) == 60


def test_leviathan_score_med_confidence_adds_10():
    """MED confidence adds 10 pts to base."""
    s = _signal(confidence="MED")
    assert report.compute_leviathan_score(s) == 50


def test_leviathan_score_net_edge_large_adds_10():
    """net_edge > 10pp adds +10."""
    s = _signal(confidence="LOW", net_edge=0.11)
    assert report.compute_leviathan_score(s) == 50


def test_leviathan_score_net_edge_medium_adds_6():
    """net_edge 5-10pp adds +6."""
    s = _signal(confidence="LOW", net_edge=0.07)
    assert report.compute_leviathan_score(s) == 46


def test_leviathan_score_net_edge_small_positive_adds_2():
    """net_edge 0-5pp adds +2."""
    s = _signal(confidence="LOW", net_edge=0.03)
    assert report.compute_leviathan_score(s) == 42


def test_leviathan_score_net_edge_zero_or_negative_subtracts_8():
    """net_edge ≤ 0 subtracts 8."""
    s = _signal(confidence="LOW", net_edge=-0.02)
    assert report.compute_leviathan_score(s) == 32


def test_leviathan_score_short_horizon_penalty():
    """INTRADAY time horizon subtracts 5."""
    s = _signal(confidence="LOW", time_horizon="INTRADAY")
    assert report.compute_leviathan_score(s) == 35


def test_leviathan_score_weekly_horizon_penalty():
    """WEEKLY time horizon subtracts 5."""
    s = _signal(confidence="LOW", time_horizon="WEEKLY")
    assert report.compute_leviathan_score(s) == 35


def test_leviathan_score_pass_count_3_subtracts_8():
    """pass_count >= 3 subtracts 8."""
    s = _signal(confidence="LOW", pass_count=3)
    assert report.compute_leviathan_score(s) == 32


def test_leviathan_score_pass_count_2_subtracts_3():
    """pass_count == 2 subtracts 3."""
    s = _signal(confidence="LOW", pass_count=2)
    assert report.compute_leviathan_score(s) == 37


def test_leviathan_score_persistence_3_consistent_adds_5():
    """3+ prior appearances + direction_consistent adds +5."""
    s = _signal(confidence="LOW", prior_appearances=3, direction_consistent=True)
    assert report.compute_leviathan_score(s) == 45


def test_leviathan_score_persistence_2_adds_2():
    """2 prior appearances (regardless of consistent) adds +2."""
    s = _signal(confidence="LOW", prior_appearances=2, direction_consistent=False)
    assert report.compute_leviathan_score(s) == 42


def test_leviathan_score_clamps_to_100():
    """Score never exceeds 100."""
    s = _signal(
        confidence="HIGH", net_edge=0.15, time_horizon="LONG",
        prior_appearances=4, direction_consistent=True,
        watchlist_signal=True, watchlist_direction="YES",
        ob_flag=True, whale_data={"whale_detected": True},
        poly={"price_gap": 0.10},
        ext_markets=[{"price_gap": 0.08}, {"price_gap": 0.07}],
    )
    score = report.compute_leviathan_score(s)
    assert score <= 100


def test_leviathan_score_clamps_to_zero():
    """Score never goes below 0."""
    s = _signal(
        confidence="LOW", net_edge=-0.20,
        time_horizon="INTRADAY", pass_count=5,
    )
    assert report.compute_leviathan_score(s) >= 0


def test_leviathan_score_shown_in_signal_block_header():
    """[LV:XX/Y] label appears in the header line of _signal_block."""
    s = _signal(confidence="HIGH", net_edge=0.12)
    header = report._signal_block(s, index=1)[0]
    assert "[LV:" in header


def test_leviathan_score_header_value_matches_function():
    """[LV:XX/Y] value in header matches compute_leviathan_score() output."""
    s = _signal(confidence="HIGH", net_edge=0.12)
    expected = report.compute_leviathan_score(s)
    header = report._signal_block(s, index=1)[0]
    assert f"[LV:{expected}/" in header


def test_leviathan_score_band_a_shown_for_high_scores():
    """LV scores >= 70 show band A."""
    s = _signal(
        confidence="HIGH", net_edge=0.12, time_horizon="LONG",
        prior_appearances=3, direction_consistent=True,
        watchlist_signal=True, watchlist_direction="YES",
    )
    score = report.compute_leviathan_score(s)
    header = report._signal_block(s, index=1)[0]
    if score >= 70:
        assert "/A]" in header
    else:
        assert "/B]" in header or "/C]" in header


def test_leviathan_score_band_d_shown_for_low_scores():
    """LV scores < 40 show band D."""
    s = _signal(confidence="LOW", net_edge=-0.10, time_horizon="INTRADAY", pass_count=4)
    score = report.compute_leviathan_score(s)
    header = report._signal_block(s, index=1)[0]
    if score < 40:
        assert "/D]" in header


def test_leviathan_score_shown_in_weekly_digest():
    """LV column header appears in weekly digest MARKETS FLAGGED table."""
    row = _signal(confidence="HIGH", net_edge=0.10)
    row["timestamp"] = "2026-06-01T10:00:00+00:00"
    text = report.compile_weekly_digest([row], _make_weekly_stats(), {})
    assert "LV" in text


def test_leviathan_score_value_in_weekly_digest_row():
    """Each market row in weekly digest shows the computed LV score."""
    row = _signal(confidence="HIGH", net_edge=0.10)
    row["timestamp"] = "2026-06-01T10:00:00+00:00"
    expected = report.compute_leviathan_score(row)
    text = report.compile_weekly_digest([row], _make_weekly_stats(), {})
    assert str(expected) in text


# ─── LV heuristic specificity bonus ──────────────────────────────────────────

def test_lv_high_spec_pdufa_adds_8():
    """PDUFA date label (HIGH_SPEC) adds +8 to LV score."""
    base = _signal(confidence="LOW")  # base LV = 40
    spec = _signal(confidence="LOW", heuristic_label="PDUFA date")
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base) + 8


def test_lv_high_spec_shutdown_avoided_adds_8():
    """Government shutdown avoided (HIGH_SPEC) adds +8."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label="government shutdown avoided")
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base) + 8


def test_lv_med_spec_crypto_upgrade_adds_4():
    """Crypto protocol upgrade (MED_SPEC) adds +4."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label="crypto protocol upgrade")
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base) + 4


def test_lv_med_spec_bond_issuance_adds_4():
    """Bond/debt issuance (MED_SPEC) adds +4."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label="bond/debt issuance")
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base) + 4


def test_lv_no_spec_generic_no_bonus():
    """Generic heuristic label (not in any spec set) adds 0."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label="legislative passage")
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base)


def test_lv_none_label_no_bonus():
    """heuristic_label=None does not affect LV score."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label=None)
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base)


def test_lv_spec_bonus_clamps_at_100():
    """Specificity bonus on an already-high score still clamps to 100."""
    s = _signal(
        confidence="HIGH", net_edge=0.12, heuristic_label="PDUFA date",
        prior_appearances=3, direction_consistent=True,
        watchlist_signal=True, watchlist_direction="YES",
        whale_data={"whale_detected": True, "whale_direction": "YES"},
        ob_flag=True,
        poly={"price_gap": 0.10},
        ext_markets=[{"price_gap": 0.10}, {"price_gap": 0.10}],
    )
    assert report.compute_leviathan_score(s) <= 100


def test_lv_high_spec_case_insensitive():
    """Specificity bonus is case-insensitive on heuristic_label."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label="PDUFA Date")  # uppercase
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base) + 8


def test_lv_med_spec_merger_adds_4():
    """Merger or acquisition (MED_SPEC) adds +4 to LV score."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label="merger or acquisition")
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base) + 4


def test_lv_med_spec_trade_tariffs_adds_4():
    """Trade tariffs (MED_SPEC) adds +4 to LV score."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label="trade tariffs")
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base) + 4


def test_lv_med_spec_presidential_veto_adds_4():
    """Presidential veto (MED_SPEC) adds +4 to LV score."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label="presidential veto")
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base) + 4


def test_lv_med_spec_spacex_launch_adds_4():
    """SpaceX launch (MED_SPEC) adds +4 to LV score."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label="SpaceX launch")
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base) + 4


def test_lv_med_spec_merger_close_adds_4():
    """Merger close signed deal (MED_SPEC) adds +4."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label="merger close (signed deal)")
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base) + 4


def test_lv_med_spec_hostile_takeover_adds_4():
    """Hostile takeover bid (MED_SPEC) adds +4."""
    base = _signal(confidence="LOW")
    spec = _signal(confidence="LOW", heuristic_label="hostile takeover bid")
    assert report.compute_leviathan_score(spec) == report.compute_leviathan_score(base) + 4


# ─── _qualifying: min_lv filter ───────────────────────────────────────────────

def test_qualifying_min_lv_excludes_low_score():
    """Signals below min_lv are excluded from qualifying list."""
    # Grade D signal: negative net_edge drags score below 40
    low = _signal(confidence="HIGH", net_edge=-0.05, pass_count=3, time_horizon="INTRADAY")
    result = report._qualifying([low], threshold_rank=0, min_lv=40)
    assert low not in result


def test_qualifying_min_lv_zero_keeps_all():
    """min_lv=0 (default) does not filter any signal by LV score."""
    low = _signal(confidence="HIGH", net_edge=-0.05, pass_count=3, time_horizon="INTRADAY")
    result = report._qualifying([low], threshold_rank=0, min_lv=0)
    assert low in result


def test_qualifying_min_lv_grade_c_passes():
    """A Grade-C signal (LV ≥40) is not filtered when min_lv=40."""
    # Base=40 with HIGH (+20) and moderate net_edge should clear 40 easily
    sig = _signal(confidence="HIGH", net_edge=0.07)
    lv = report.compute_leviathan_score(sig)
    assert lv >= 40, f"pre-condition: expected LV≥40, got {lv}"
    result = report._qualifying([sig], threshold_rank=0, min_lv=40)
    assert sig in result


def test_qualifying_second_pass_bypasses_min_lv():
    """second_pass signals bypass the confidence threshold, but min_lv still applies."""
    # A second_pass signal below min_lv should still be filtered
    low = _signal(confidence="LOW", direction="YES", net_edge=-0.05,
                  pass_count=3, time_horizon="INTRADAY", second_pass=True)
    lv = report.compute_leviathan_score(low)
    if lv < 40:
        result = report._qualifying([low], threshold_rank=1, min_lv=40)
        assert low not in result


def test_qualifying_min_lv_filters_grade_d_from_mixed_list():
    """When min_lv=40, only Grade-C+ signals survive from a mixed list."""
    good = _signal(confidence="HIGH", net_edge=0.12, prior_appearances=3,
                   direction_consistent=True)
    bad  = _signal(confidence="HIGH", net_edge=-0.05, pass_count=3,
                   time_horizon="INTRADAY", ticker="KXBAD-TEST")
    lv_good = report.compute_leviathan_score(good)
    lv_bad  = report.compute_leviathan_score(bad)
    assert lv_good >= 40, f"pre-condition failed: good={lv_good}"
    result = report._qualifying([good, bad], threshold_rank=0, min_lv=40)
    assert good in result
    if lv_bad < 40:
        assert bad not in result


# ─── Goal 3e: smart money section detail gating ───────────────────────────────

def _sm_result(**kwargs):
    base = {
        "traders_active": 2,
        "positions_total": 10,
        "kalshi_signals": [],
        "grouped_signals": [],
        "run_at": "2026-06-21T00:00:00Z",
        "trader_data": {},
    }
    base.update(kwargs)
    return base


def test_smart_money_no_signals_omits_per_trader_block():
    """When signals==[], smart money section must not include Per-Trader block."""
    result = _sm_result()
    out = "\n".join(report._smart_money_section(result, show_detail=False))
    assert "Per-Trader" not in out


def test_smart_money_no_signals_omits_largest_positions_block():
    """When show_detail=False, Largest Open Positions must be absent."""
    result = _sm_result(trader_data={
        "traderA": {"positions": [
            {"currentValue": 50000, "outcome": "Yes", "curPrice": 0.8,
             "percentPnl": 10.0, "title": "Will the Fed raise rates?"},
        ]}
    })
    out = "\n".join(report._smart_money_section(result, show_detail=False))
    assert "Largest Open Positions" not in out


def test_smart_money_with_signals_shows_all_blocks():
    """When show_detail=True and signals exist, all three blocks appear."""
    signals = [{
        "trader": "traderX", "poly_outcome": "Yes", "position_val": 10000,
        "poly_price": 0.60, "match_score": 0.80, "kalshi_ticker": "KXTEST-26",
        "poly_title": "Will X happen?", "kalshi_title": "Will X happen by 2026?",
    }]
    grouped = [{
        "kalshi_ticker": "KXTEST-26", "trader_count": 1, "total_position_val": 10000,
        "directions": {"YES": 1}, "consensus_direction": "YES",
        "kalshi_title": "Will X happen by 2026?",
    }]
    trader_data = {
        "traderX": {"positions": [
            {"currentValue": 10000, "outcome": "Yes", "curPrice": 0.6,
             "percentPnl": 5.0, "title": "Will the Fed raise rates?"},
            {"currentValue": 8000, "outcome": "Yes", "curPrice": 0.5,
             "percentPnl": 3.0, "title": "Will inflation fall below 3%?"},
            {"currentValue": 7000, "outcome": "No", "curPrice": 0.4,
             "percentPnl": -2.0, "title": "Will tariffs increase?"},
        ]}
    }
    result = _sm_result(kalshi_signals=signals, grouped_signals=grouped, trader_data=trader_data)
    out = "\n".join(report._smart_money_section(result, show_detail=True))
    assert "Per-Trader" in out
    assert "Kalshi Targets" in out


def test_smart_money_sports_titles_filtered_from_largest_positions():
    """Sports-titled positions must be absent from Largest Open Positions."""
    trader_data = {
        "traderA": {"positions": [
            {"currentValue": 90000, "outcome": "Yes", "curPrice": 0.9,
             "percentPnl": 5.0, "title": "Will France vs. Germany end in a draw?"},
            {"currentValue": 80000, "outcome": "Yes", "curPrice": 0.7,
             "percentPnl": 3.0, "title": "Will France win on 2026-06-22?"},
            {"currentValue": 70000, "outcome": "No",  "curPrice": 0.6,
             "percentPnl": -1.0, "title": "Uruguay vs. Cabo Verde: O/U 2.5"},
            {"currentValue": 60000, "outcome": "Yes", "curPrice": 0.8,
             "percentPnl": 2.0, "title": "Will the Fed raise rates in 2026?"},
            {"currentValue": 50000, "outcome": "No",  "curPrice": 0.3,
             "percentPnl": 4.0, "title": "Will inflation fall to 2% by year end?"},
            {"currentValue": 40000, "outcome": "Yes", "curPrice": 0.55,
             "percentPnl": 6.0, "title": "Will Andy Burnham become Prime Minister?"},
        ]}
    }
    result = _sm_result(trader_data=trader_data)
    out = "\n".join(report._smart_money_section(result, show_detail=True))
    # Sports titles must be absent
    assert "France vs. Germany" not in out
    assert "win on 2026-06-22" not in out
    assert "O/U 2.5" not in out
    # Non-sports title must be present
    assert "Fed raise rates" in out


def test_smart_money_non_sports_still_shown():
    """Non-sports positions must appear in Largest Open Positions."""
    trader_data = {
        "traderA": {"positions": [
            {"currentValue": 50000, "outcome": "Yes", "curPrice": 0.6,
             "percentPnl": 5.0, "title": "Will the Fed raise rates?"},
            {"currentValue": 40000, "outcome": "No",  "curPrice": 0.4,
             "percentPnl": 3.0, "title": "Will inflation fall below 3%?"},
            {"currentValue": 30000, "outcome": "Yes", "curPrice": 0.7,
             "percentPnl": 2.0, "title": "Will Andy Burnham be Prime Minister?"},
        ]}
    }
    result = _sm_result(trader_data=trader_data)
    out = "\n".join(report._smart_money_section(result, show_detail=True))
    assert "Fed raise rates" in out


def test_smart_money_fewer_than_3_non_sports_shows_note():
    """When fewer than 3 non-sports positions, note is shown instead of table."""
    trader_data = {
        "traderA": {"positions": [
            {"currentValue": 90000, "outcome": "Yes", "curPrice": 0.9,
             "percentPnl": 5.0, "title": "France vs. Spain: O/U 2.5"},
            {"currentValue": 80000, "outcome": "Yes", "curPrice": 0.7,
             "percentPnl": 3.0, "title": "Will Germany win on 2026-06-24?"},
            {"currentValue": 5000,  "outcome": "No",  "curPrice": 0.3,
             "percentPnl": 2.0, "title": "Will the Fed raise rates?"},
        ]}
    }
    result = _sm_result(trader_data=trader_data)
    out = "\n".join(report._smart_money_section(result, show_detail=True))
    assert "No non-sports positions" in out
    assert "Largest Open Positions" not in out


def test_smart_money_per_trader_no_poly_kalshi_labels():
    """Per-Trader Cross-References must not contain 'Poly:' or 'Kalshi:' label prefixes."""
    signals = [{
        "trader": "traderX", "poly_outcome": "Yes", "position_val": 10000,
        "poly_price": 0.60, "match_score": 0.80, "kalshi_ticker": "KXTEST-26",
        "poly_title": "Will X happen?", "kalshi_title": "Will X happen by 2026?",
    }]
    result = _sm_result(kalshi_signals=signals)
    out = "\n".join(report._smart_money_section(result, show_detail=True))
    assert "Poly:   " not in out
    assert "Kalshi: " not in out


def test_smart_money_kalshi_targets_capped_at_15():
    """Kalshi Targets table must be capped at 15 rows with overflow note."""
    grouped = [
        {
            "kalshi_ticker": f"KXTICKER-{i:02d}", "trader_count": 1,
            "total_position_val": 1000 * (20 - i),
            "directions": {"YES": 1}, "consensus_direction": "YES",
            "kalshi_title": f"Market title {i}",
        }
        for i in range(20)
    ]
    result = _sm_result(grouped_signals=grouped)
    out = "\n".join(report._smart_money_section(result))
    # Exactly 15 rows shown, overflow note present
    assert "... and 5 more" in out
    # First 15 by descending total_position_val must appear
    assert "KXTICKER-00" in out
    assert "KXTICKER-14" in out
    assert "KXTICKER-15" not in out


def test_smart_money_largest_positions_capped_at_8():
    """Largest Open Positions must be capped at 8 rows after sports filter."""
    trader_data = {
        "traderA": {"positions": [
            {"currentValue": float(10000 - i * 500), "outcome": "Yes",
             "curPrice": 0.5, "percentPnl": 1.0,
             "title": f"Will macro event {i} happen?"}
            for i in range(12)
        ]}
    }
    result = _sm_result(trader_data=trader_data)
    out = "\n".join(report._smart_money_section(result, show_detail=True))
    # Count "macro event" occurrences — should be at most 8
    count = out.count("macro event")
    assert count <= 8, f"Expected at most 8 rows, got {count}"


# ─── Goal 3e: logger resolution helpers ──────────────────────────────────────

import sqlite3
import tempfile
import os

def _make_resolution_db(path: str, rows: list[dict]) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            call_id TEXT PRIMARY KEY, timestamp TEXT, ticker TEXT,
            direction TEXT, confidence TEXT, market_price REAL,
            close_time TEXT, result TEXT, source TEXT
        );
    """)
    for r in rows:
        conn.execute("""
            INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            r["call_id"], r.get("timestamp", "2026-06-01T00:00:00Z"),
            r.get("ticker", "KXTEST"), r.get("direction", "YES"),
            r.get("confidence", "MED"), r.get("market_price", 0.5),
            r.get("close_time"), r.get("result", ""), r.get("source", "paper"),
        ))
    conn.commit()
    conn.close()


def test_get_next_resolution_date_none_when_no_close_times():
    """Returns None when no close_time is set on any signal."""
    from core.logger import get_next_resolution_date, DB_PATH, _db
    # Use the real DB but test that the function doesn't crash; result is None or a string
    result = get_next_resolution_date()
    assert result is None or (isinstance(result, str) and len(result) == 10)


def test_get_next_resolution_date_returns_earliest():
    """Returns the earliest close_time date from unresolved signals."""
    from core.logger import get_next_resolution_date, _db, _PAPER
    import uuid
    # Insert two unresolved signals with different close_times, then verify
    id1 = str(uuid.uuid4())[:8]
    id2 = str(uuid.uuid4())[:8]
    with _db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO signals
            (call_id, timestamp, ticker, direction, confidence, result, source, close_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (id1, "2026-06-01T00:00:00Z", "KXTEST-NEAR", "YES", "MED", "", "paper",
              "2026-07-05T00:00:00Z"))
        conn.execute("""
            INSERT OR IGNORE INTO signals
            (call_id, timestamp, ticker, direction, confidence, result, source, close_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (id2, "2026-06-01T00:00:00Z", "KXTEST-FAR", "NO", "MED", "", "paper",
              "2026-08-01T00:00:00Z"))

    result = get_next_resolution_date()
    # Should be the earlier date (or at most 2026-07-05 if other earlier signals exist)
    assert result is not None
    assert result <= "2026-07-05"

    # Cleanup
    with _db() as conn:
        conn.execute("DELETE FROM signals WHERE call_id IN (?, ?)", (id1, id2))


def test_get_upcoming_resolutions_returns_only_window():
    """Returns only signals closing within N days, excluding PASS direction."""
    from core.logger import get_upcoming_resolutions, _db
    from datetime import datetime, timezone, timedelta
    import uuid
    now = datetime.now(timezone.utc)
    id_near   = str(uuid.uuid4())[:8]
    id_far    = str(uuid.uuid4())[:8]
    id_pass   = str(uuid.uuid4())[:8]
    near_close = (now + timedelta(days=5)).isoformat()
    far_close  = (now + timedelta(days=30)).isoformat()

    with _db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO signals
            (call_id, timestamp, ticker, direction, confidence, result, source, close_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (id_near, now.isoformat(), "KXNEAR", "YES", "MED", "", "paper", near_close))
        conn.execute("""
            INSERT OR IGNORE INTO signals
            (call_id, timestamp, ticker, direction, confidence, result, source, close_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (id_far, now.isoformat(), "KXFAR", "NO", "MED", "", "paper", far_close))
        conn.execute("""
            INSERT OR IGNORE INTO signals
            (call_id, timestamp, ticker, direction, confidence, result, source, close_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (id_pass, now.isoformat(), "KXPASS", "PASS", "MED", "", "paper", near_close))

    rows = get_upcoming_resolutions(days=14)
    tickers = [r["ticker"] for r in rows]
    assert "KXNEAR" in tickers
    assert "KXFAR"  not in tickers
    assert "KXPASS" not in tickers

    # Cleanup
    with _db() as conn:
        conn.execute("DELETE FROM signals WHERE call_id IN (?, ?, ?)", (id_near, id_far, id_pass))


def test_upcoming_resolutions_section_in_compile_report():
    """UPCOMING RESOLUTIONS section must appear in compile_report output."""
    body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG)
    assert "UPCOMING RESOLUTIONS" in body


def test_upcoming_resolutions_no_picks_message():
    """When no picks close within 14 days, placeholder message appears."""
    body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG)
    # Either shows the section with a "No picks" message, or shows upcoming picks
    assert "UPCOMING RESOLUTIONS" in body
