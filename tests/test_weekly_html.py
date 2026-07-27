"""
tests/test_weekly_html.py — Tests for core.report._week_whale_rows() and
core.report.render_weekly_html() (the weekly digest's HTML renderer,
matching the same visual system as render_html()).

No live SMTP, no network. Mirrors tests/test_report_html.py's conventions.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import report


def _stats(total=5, resolved=3, win_rate=66.7, avg_edge=0.12, total_pnl=0.85):
    return {
        "total_calls":            total,
        "resolved":               resolved,
        "win_rate":               win_rate,
        "avg_edge_captured":      avg_edge,
        "total_hypothetical_pnl": total_pnl,
    }


def _week_sig(ticker="KXTST-01", direction="YES", confidence="MED",
              whale_detected=False, whale_direction=None, whale_max_trade_size=None,
              market_price=0.30, our_estimate=0.45, timestamp="2026-07-20T10:00:00Z",
              **kwargs):
    base = {
        "ticker": ticker, "title": f"Will {ticker} happen?",
        "direction": direction, "confidence": confidence,
        "market_price": market_price, "our_estimate": our_estimate,
        "whale_detected": whale_detected, "whale_direction": whale_direction,
        "whale_max_trade_size": whale_max_trade_size,
        "timestamp": timestamp, "edge": 0.15, "net_edge": 0.10,
    }
    base.update(kwargs)
    return base


# ─── _week_whale_rows ──────────────────────────────────────────────────────

def test_week_whale_rows_empty_when_none_flagged():
    rows = report._week_whale_rows([_week_sig(whale_detected=False)])
    assert rows == []


def test_week_whale_rows_includes_only_whale_detected():
    sigs = [
        _week_sig(ticker="T1", whale_detected=True, whale_direction="YES"),
        _week_sig(ticker="T2", whale_detected=False),
    ]
    rows = report._week_whale_rows(sigs)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "T1"


def test_week_whale_rows_dedups_by_ticker_keeping_latest():
    sigs = [
        _week_sig(ticker="T1", whale_detected=True, whale_direction="NO",
                  timestamp="2026-07-18T00:00:00Z"),
        _week_sig(ticker="T1", whale_detected=True, whale_direction="YES",
                  timestamp="2026-07-20T00:00:00Z"),
    ]
    rows = report._week_whale_rows(sigs)
    assert len(rows) == 1
    assert rows[0]["whale_dir"] == "YES"


def test_week_whale_rows_ev_uses_whale_direction_not_claude_call():
    """
    Most whale-flagged markets end in a Claude PASS -- EV computed from
    Claude's own direction would be empty for nearly every row. EV must
    instead assume the whale's own direction, using the same real
    market_price/our_estimate already scored.
    """
    sigs = [_week_sig(ticker="T1", direction="PASS", whale_detected=True,
                      whale_direction="YES", market_price=0.30, our_estimate=0.50)]
    rows = report._week_whale_rows(sigs)
    assert rows[0]["claude_call"] == "PASS"
    assert rows[0]["ev"] is not None  # would be None if computed from "PASS"


def test_week_whale_rows_position_none_when_not_tracked():
    """Rows logged before whale position tracking existed show None, not 0."""
    sigs = [_week_sig(ticker="T1", whale_detected=True, whale_direction="YES",
                      whale_max_trade_size=None)]
    rows = report._week_whale_rows(sigs)
    assert rows[0]["position"] is None


def test_week_whale_rows_position_present_when_tracked():
    sigs = [_week_sig(ticker="T1", whale_detected=True, whale_direction="YES",
                      whale_max_trade_size=620.0)]
    rows = report._week_whale_rows(sigs)
    assert rows[0]["position"] == 620.0


def test_week_whale_rows_sorted_by_position_descending():
    sigs = [
        _week_sig(ticker="SMALL", whale_detected=True, whale_direction="YES",
                  whale_max_trade_size=100.0),
        _week_sig(ticker="BIG", whale_detected=True, whale_direction="YES",
                  whale_max_trade_size=900.0),
    ]
    rows = report._week_whale_rows(sigs)
    assert [r["ticker"] for r in rows] == ["BIG", "SMALL"]


# ─── render_weekly_html ────────────────────────────────────────────────────

def test_render_weekly_html_basic_structure():
    html = report.render_weekly_html([], _stats(), {})
    assert html.startswith("<!DOCTYPE html>")
    assert "LEVIATHAN" in html
    assert "Weekly&nbsp;Digest" in html
    assert "Whale Activity This Week" in html
    assert "Markets Flagged This Week" in html
    assert "Track Record" in html


def test_render_weekly_html_empty_whale_section_message():
    html = report.render_weekly_html([_week_sig(whale_detected=False)], _stats(), {})
    assert "No whale-flagged markets this week." in html


def test_render_weekly_html_whale_row_rendered():
    sigs = [_week_sig(ticker="KXWHALE-01", whale_detected=True,
                      whale_direction="YES", whale_max_trade_size=740.0)]
    html = report.render_weekly_html(sigs, _stats(), {})
    assert "KXWHALE-01" in html
    assert "740" in html


def test_render_weekly_html_market_row_rendered():
    sigs = [_week_sig(ticker="KXMARKET-01", direction="NO", confidence="HIGH")]
    html = report.render_weekly_html(sigs, _stats(), {})
    assert "KXMARKET-01" in html


def test_render_weekly_html_no_signals_no_crash():
    html = report.render_weekly_html([], _stats(total=0, resolved=0, win_rate=None,
                                              avg_edge=None, total_pnl=None), {})
    assert "No markets flagged this week." in html
    assert "No whale-flagged markets this week." in html


def test_render_weekly_html_flag_path_section_present_when_data_given():
    flag_stats = [{"flag_path": "EDGE", "total": 5, "wins": 3, "win_rate": 60.0, "total_pnl": 1.25}]
    html = report.render_weekly_html([], _stats(), {}, flag_path_stats=flag_stats)
    assert "Win Rate by Signal Path" in html
    assert "EDGE" in html


def test_render_weekly_html_flag_path_section_absent_when_no_data():
    html = report.render_weekly_html([], _stats(), {}, flag_path_stats=None)
    assert "Win Rate by Signal Path" not in html


def test_render_weekly_html_brier_score_rendered():
    brier = {"brier_score": 0.0578, "label": "EXCELLENT", "n": 8}
    html = report.render_weekly_html([], _stats(), {}, brier=brier)
    assert "0.0578" in html
    assert "EXCELLENT" in html


def test_render_weekly_html_brier_pending_when_absent():
    html = report.render_weekly_html([], _stats(), {}, brier=None)
    assert "PENDING" in html


def test_render_weekly_html_escapes_ticker_and_title():
    sigs = [_week_sig(ticker="KX<script>alert(1)</script>")]
    html = report.render_weekly_html(sigs, _stats(), {})
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
