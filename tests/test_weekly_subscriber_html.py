"""
tests/test_weekly_subscriber_html.py — Tests for
core.report.render_weekly_subscriber_html (leviathan-report-format-
decision.md Phase 2).

No live network, no live DB — all inputs passed in directly, matching
render_weekly_subscriber_html's own pure-view-function contract (it never
queries the DB itself, same as render_subscriber_html's resolved_recap
parameter).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import report

_FIXED_NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def _week_sig(ticker="KXTST-01", direction="YES", confidence="MED", edge=0.15,
              market_price=0.30, our_estimate=0.45, flag_path="DRIFT",
              close_time_raw="2026-08-25T00:00:00Z", timestamp="2026-08-15T00:00:00Z",
              title=None, **kwargs):
    base = {
        "ticker": ticker, "title": title or f"Will {ticker} happen?",
        "direction": direction, "confidence": confidence, "edge": edge,
        "market_price": market_price, "our_estimate": our_estimate,
        "flag_path": flag_path, "close_time_raw": close_time_raw,
        "close_time": close_time_raw, "timestamp": timestamp,
        "time_horizon": "MONTHLY", "sources": [], "event_ticker": "", "series_ticker": "",
    }
    base.update(kwargs)
    return base


def _stats(**kwargs):
    base = {"win_rate": 55.0, "avg_edge_captured": 0.10, "total_hypothetical_pnl": 12.5,
            "resolved": 13, "total_calls": 20}
    base.update(kwargs)
    return base


def _recap_item(**kwargs):
    base = {"direction": "YES", "result": "WIN", "our_estimate": 0.60,
            "title": "Resolved market", "outcome": "YES", "market_drift_pp": 3.0}
    base.update(kwargs)
    return base


def _upcoming_item(**kwargs):
    base = {"title": "Upcoming market", "close_time": "2026-08-22T00:00:00Z",
            "direction": "NO", "market_price": 0.55}
    base.update(kwargs)
    return base


# ── Empty state ────────────────────────────────────────────────────────────

class TestEmptyState:

    def test_no_signals_shows_honest_placeholder(self):
        html = report.render_weekly_subscriber_html([], _stats(resolved=0), {}, now_utc=_FIXED_NOW)
        assert "No qualifying calls this week." in html

    def test_no_recap_shows_honest_placeholder(self):
        html = report.render_weekly_subscriber_html([], _stats(resolved=0), {}, resolved_recap=None, now_utc=_FIXED_NOW)
        assert "Nothing settled this week yet." in html

    def test_no_upcoming_shows_honest_placeholder(self):
        html = report.render_weekly_subscriber_html([], _stats(resolved=0), {}, upcoming=None, now_utc=_FIXED_NOW)
        assert "Nothing scheduled to resolve in the next 7 days." in html

    def test_empty_everything_does_not_raise(self):
        html = report.render_weekly_subscriber_html([], {}, {}, now_utc=_FIXED_NOW)
        assert html.startswith("<!DOCTYPE html>")


# ── Calibration gate (docs/PREREGISTRATION.md's n=50 checkpoint) ───────────

class TestCalibrationGate:

    def test_below_gate_shows_honest_message_not_fabricated_brier(self):
        html = report.render_weekly_subscriber_html(
            [], _stats(resolved=13), {}, brier={"brier_score": 0.08, "n": 13}, now_utc=_FIXED_NOW,
        )
        assert "Not enough resolved yet to show a calibration snapshot honestly (n=13, need 50)." in html
        assert "0.0800" not in html

    def test_at_gate_shows_real_brier(self):
        html = report.render_weekly_subscriber_html(
            [], _stats(resolved=50), {}, brier={"brier_score": 0.0821, "n": 50}, now_utc=_FIXED_NOW,
        )
        assert "0.0821" in html
        assert "Not enough resolved yet" not in html

    def test_above_gate_includes_market_baseline_when_given(self):
        html = report.render_weekly_subscriber_html(
            [], _stats(resolved=55), {},
            brier={"brier_score": 0.08, "n": 55},
            market_baseline_brier={"brier_score": 0.15, "n": 55},
            now_utc=_FIXED_NOW,
        )
        assert "0.0800" in html
        assert "0.1500" in html
        assert "Market-price baseline Brier" in html

    def test_above_gate_omits_market_baseline_when_not_given(self):
        html = report.render_weekly_subscriber_html(
            [], _stats(resolved=55), {}, brier={"brier_score": 0.08, "n": 55}, now_utc=_FIXED_NOW,
        )
        assert "Market-price baseline Brier" not in html

    def test_above_gate_includes_wilson_interval(self):
        html = report.render_weekly_subscriber_html(
            [], _stats(resolved=55, win_rate=60.0), {}, brier={"brier_score": 0.08, "n": 55}, now_utc=_FIXED_NOW,
        )
        assert "95% CI" in html
        assert "n=55" in html

    def test_no_brier_at_all_shows_honest_message(self):
        html = report.render_weekly_subscriber_html([], _stats(resolved=0), {}, brier=None, now_utc=_FIXED_NOW)
        assert "Not enough resolved yet" in html
        assert "n=0, need 50" in html


# ── Digest strip numbers trace to input data ────────────────────────────────

class TestDigestStrip:

    def test_unique_markets_deduped_by_ticker(self):
        sigs = [_week_sig(ticker="KXA"), _week_sig(ticker="KXA"), _week_sig(ticker="KXB")]
        html = report.render_weekly_subscriber_html(sigs, _stats(), {}, now_utc=_FIXED_NOW)
        assert '<div class="n">2</div><div class="l">Markets flagged</div>' in html
        assert '<div class="n">3</div><div class="l">Signal instances</div>' in html

    def test_yes_no_split(self):
        sigs = [_week_sig(ticker="KXA", direction="YES"), _week_sig(ticker="KXB", direction="NO"),
                _week_sig(ticker="KXC", direction="NO")]
        html = report.render_weekly_subscriber_html(sigs, _stats(), {}, now_utc=_FIXED_NOW)
        assert '<div class="n">1/2</div><div class="l">Yes/No split</div>' in html

    def test_high_conviction_count(self):
        sigs = [_week_sig(ticker="KXA", confidence="HIGH"), _week_sig(ticker="KXB", confidence="MED")]
        html = report.render_weekly_subscriber_html(sigs, _stats(), {}, now_utc=_FIXED_NOW)
        assert '<div class="n">1</div><div class="l">High conviction</div>' in html

    def test_win_rate_and_avg_edge_and_pnl_from_stats(self):
        html = report.render_weekly_subscriber_html(
            [], _stats(win_rate=72.5, avg_edge_captured=0.083, total_hypothetical_pnl=-4.2), {}, now_utc=_FIXED_NOW,
        )
        assert '<div class="n">72%</div><div class="l">Win rate</div>' in html
        assert "+8.3pp" in html
        assert "$-4.20" in html

    def test_missing_stats_render_em_dash(self):
        html = report.render_weekly_subscriber_html([], {}, {}, now_utc=_FIXED_NOW)
        assert '<div class="n">—</div><div class="l">Win rate</div>' in html
        assert '<div class="n">—</div><div class="l">Avg edge captured</div>' in html
        assert '<div class="n">—</div><div class="l">Hypo P&amp;L</div>' in html

    def test_markets_scanned_and_resolving_next_7d(self):
        html = report.render_weekly_subscriber_html(
            [], _stats(), {}, upcoming=[_upcoming_item(), _upcoming_item(title="Other")],
            markets_scanned_week=1234, now_utc=_FIXED_NOW,
        )
        assert '<div class="n">1,234</div><div class="l">Markets scanned</div>' in html
        assert '<div class="n">2</div><div class="l">Resolving next 7d</div>' in html


# ── Notable calls (reuses the daily .pick component) ────────────────────────

class TestNotableCalls:

    def test_renders_pick_card_for_a_signal(self):
        html = report.render_weekly_subscriber_html([_week_sig()], _stats(), {}, now_utc=_FIXED_NOW)
        assert 'class="pick"' in html
        assert "Will KXTST-01 happen?" in html

    def test_caps_at_three_picks(self):
        sigs = [_week_sig(ticker=f"KX{i}", confidence="HIGH") for i in range(5)]
        html = report.render_weekly_subscriber_html(sigs, _stats(), {}, now_utc=_FIXED_NOW)
        assert html.count('class="pick"') == 3


# ── Resolved recap ───────────────────────────────────────────────────────────

class TestResolvedRecap:

    def test_renders_recap_item(self):
        html = report.render_weekly_subscriber_html(
            [], _stats(), {}, resolved_recap=[_recap_item(title="A settled call")], now_utc=_FIXED_NOW,
        )
        assert "A settled call" in html
        assert 'class="recap-item"' in html

    def test_win_and_loss_both_shown(self):
        html = report.render_weekly_subscriber_html(
            [], _stats(), {},
            resolved_recap=[_recap_item(result="WIN", title="Won one"), _recap_item(result="LOSS", title="Lost one")],
            now_utc=_FIXED_NOW,
        )
        assert "Won one" in html
        assert "Lost one" in html
        assert "WIN" in html
        assert "LOSS" in html


# ── Coming to resolve ─────────────────────────────────────────────────────────

class TestUpcoming:

    def test_renders_and_sorts_by_close_time(self):
        later  = _upcoming_item(title="Later market", close_time="2026-08-26T00:00:00Z")
        sooner = _upcoming_item(title="Sooner market", close_time="2026-08-20T00:00:00Z")
        html = report.render_weekly_subscriber_html([], _stats(), {}, upcoming=[later, sooner], now_utc=_FIXED_NOW)
        assert html.index("Sooner market") < html.index("Later market")

    def test_caps_at_seven(self):
        items = [_upcoming_item(title=f"Market {i}", close_time=f"2026-08-2{i}T00:00:00Z") for i in range(9)]
        html = report.render_weekly_subscriber_html([], _stats(), {}, upcoming=items, now_utc=_FIXED_NOW)
        assert html.count('class="watch"') == 7


# ── Shared editorial design source (Phase 1 consistency) ───────────────────

class TestSharedDesignSource:

    def test_embeds_editorial_root_css(self):
        html = report.render_weekly_subscriber_html([], _stats(), {}, now_utc=_FIXED_NOW)
        assert report._editorial_root_css() in html

    def test_title_and_masthead(self):
        html = report.render_weekly_subscriber_html([], _stats(), {}, now_utc=_FIXED_NOW)
        assert "Leviathan — Weekly Briefing" in html
        assert "WEEKLY · WEEK ENDING 19 AUG 2026" in html


# ── _weekly_calibration_html (direct unit tests) ────────────────────────────

class TestWeeklyCalibrationHtml:

    def test_n_zero_no_brier_dict(self):
        out = report._weekly_calibration_html(None, None, None, 0)
        assert "n=0, need 50" in out

    def test_below_gate(self):
        out = report._weekly_calibration_html({"brier_score": 0.1, "n": 49}, None, 50.0, 49)
        assert "n=49, need 50" in out

    def test_at_gate_no_win_rate_no_crash(self):
        out = report._weekly_calibration_html({"brier_score": 0.1, "n": 50}, None, None, 0)
        assert "0.1000" in out


# ── _render_upcoming_resolution (direct unit tests) ─────────────────────────

class TestRenderUpcomingResolution:

    def test_with_direction(self):
        out = report._render_upcoming_resolution(_upcoming_item(direction="YES", market_price=0.42))
        assert "Called YES" in out
        assert "42%" in out

    def test_pass_direction_shows_no_call_made(self):
        out = report._render_upcoming_resolution(_upcoming_item(direction="PASS"))
        assert "no call made" in out

    def test_missing_market_price_renders_em_dash(self):
        out = report._render_upcoming_resolution(_upcoming_item(direction="", market_price=None))
        assert "—" in out
