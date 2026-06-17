"""
Offline tests for report.py signal block formatting.

No network calls, no email sending.
Run: python -m pytest -q
"""

import pytest
import report


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
        "avg_pct_pnl": 42.0,
        "win_rate": 68.0,
        "trade_count": 3,
    }])
    full = "\n".join(report._signal_block(s, index=1))
    assert "Smart Money Activity" in full
    assert "TraderA" in full
    assert "BUY YES" in full


# ─── compile_weekly_digest ────────────────────────────────────────────────────

def _week_row(ticker, direction="YES", confidence="MED", edge=0.12,
              timestamp="2026-06-17T08:00:00+00:00"):
    return {
        "ticker":     ticker,
        "title":      f"Will {ticker} happen?",
        "direction":  direction,
        "confidence": confidence,
        "edge":       edge,
        "timestamp":  timestamp,
    }


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
