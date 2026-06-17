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
