"""
tests/test_subscriber_html.py — Tests for core.report.render_subscriber_html
(GOAL_subscriber_report.md, Phase 1).

No live network, no live DB. core.kalshi.kalshi_market_url's real behavior
(returns a real link when series_ticker/event_ticker are present, None
otherwise) is exercised directly -- no mocking needed since it's a pure
string-formatting function.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import report

_CFG = {"scoring": {"confidence_threshold": "MED"}}
_FIXED_NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def _run_meta(**kwargs):
    base = {"markets_scanned": 2725}
    base.update(kwargs)
    return base


def _sig(ticker="KXTST-01", direction="YES", confidence="MED", edge=0.15,
         time_horizon="MONTHLY", market_price=0.30, our_estimate=0.45,
         flag_path="DRIFT", close_time="2026-08-15T00:00:00Z",
         event_ticker="", series_ticker="", title=None, **kwargs):
    base = {
        "ticker":          ticker,
        "event_ticker":    event_ticker,
        "series_ticker":   series_ticker,
        "title":           title or f"Will {ticker} happen?",
        "direction":       direction,
        "confidence":      confidence,
        "edge":            edge,
        "time_horizon":    time_horizon,
        "market_price":    market_price,
        "our_estimate":    our_estimate,
        "flag_path":       flag_path,
        "close_time":      close_time,
        "watchlist_signal": False,
        "smart_money":     [],
        "poly":            None,
        "ext_markets":     [],
        "is_repeat":       False,
        "repeat_count":    0,
    }
    base.update(kwargs)
    return base


# ─── calls vs watch split ──────────────────────────────────────────────────────

def test_one_card_per_qualifying_signal():
    signals = [
        _sig(ticker="KXA", direction="YES", confidence="HIGH", market_price=0.2, our_estimate=0.9),
        _sig(ticker="KXB", direction="NO",  confidence="MED",  market_price=0.6, our_estimate=0.3),
        _sig(ticker="KXC", direction="YES", confidence="MED",  market_price=0.1, our_estimate=0.4),
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert out.count('<article class="pick">') == 3
    assert '<div class="n">3</div><div class="l">Calls</div>' in out


def test_pass_direction_goes_to_watch_not_calls():
    signals = [
        _sig(ticker="KXCALL", direction="YES", confidence="HIGH"),
        _sig(ticker="KXPASS", direction="PASS", confidence="LOW"),
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert out.count('<article class="pick">') == 1
    assert out.count('<div class="watch">') == 1
    assert "Will KXCALL happen?" in out
    assert "Will KXPASS happen?" in out


def test_below_confidence_threshold_goes_to_watch_even_with_yes_no_direction():
    """Phase 1 spec: 'PASS or below confidence threshold' -> watch. A LOW
    signal with a real direction must not appear as a call when the
    configured threshold is MED."""
    signals = [
        _sig(ticker="KXLOW", direction="YES", confidence="LOW"),
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert out.count('<article class="pick">') == 0
    assert out.count('<div class="watch">') == 1


def test_watch_section_capped_and_sorted():
    signals = [_sig(ticker=f"KXW{i}", direction="PASS", confidence="LOW",
                     market_price=0.1, our_estimate=0.1 + i * 0.01)
               for i in range(5)]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert out.count('<div class="watch">') == 3
    assert '<div class="n">3</div><div class="l">Watching</div>' in out


def test_no_qualifying_calls_renders_placeholder_not_empty():
    signals = [_sig(ticker="KXW", direction="PASS", confidence="LOW")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "No qualifying calls right now." in out


def test_empty_signals_list_renders_both_placeholders():
    out = report.render_subscriber_html([], _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "No qualifying calls right now." in out
    assert "Nothing on the watch list right now." in out


# ─── no jargon ──────────────────────────────────────────────────────────────────

def test_no_jargon_tokens_in_output():
    signals = [
        _sig(ticker="KXA", direction="YES", confidence="HIGH", flag_path="HEURISTIC"),
        _sig(ticker="KXB", direction="NO", confidence="MED", flag_path="EDGE"),
        _sig(ticker="KXW", direction="PASS", confidence="LOW"),
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    for token in ("Kelly", "EV/ct", "flag_path"):
        assert token not in out, f"jargon token leaked into subscriber output: {token!r}"


# ─── gap math ───────────────────────────────────────────────────────────────────

def test_gap_computed_from_rounded_percentages():
    """22%/96% -> 74-point gap (rounded-then-subtracted, matching the
    reference design, not the raw fractional difference rounded)."""
    signals = [_sig(ticker="KXGAP", direction="YES", confidence="HIGH",
                     market_price=0.215, our_estimate=0.96)]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "22%" in out
    assert "96%" in out
    assert "74-point" in out


# ─── why-flagged mapping ───────────────────────────────────────────────────────

def test_why_flagged_known_flag_path():
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH", flag_path="DRIFT")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "Price drift." in out


def test_why_flagged_unknown_flag_path_falls_back():
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH", flag_path="SOMETHING_NEW")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "Model estimate." in out


# ─── reasoning / sources (Phase 2/3 forward-compatibility) ────────────────────

def test_real_reasoning_used_when_present():
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH",
                     reasoning="Confirmed by a primary-source filing dated this week.")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "Confirmed by a primary-source filing dated this week." in out
    assert "Full written analysis renders here" not in out


def test_placeholder_analysis_when_reasoning_absent():
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "Full written analysis renders here once reasoning is persisted per signal." in out


def test_sources_checked_freeform_never_rendered_as_link():
    """Guardrail: sources_checked (freeform, model self-report) must never
    become a clickable href -- only structured `sources` entries do."""
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH",
                     sources_checked=["https://example.com/looks-like-a-real-source"])]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "example.com" not in out
    assert "No sources cited for this call." in out


def test_structured_sources_render_as_real_links():
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH",
                     sources=[{"url": "https://reuters.com/real-article", "title": "Reuters coverage"}])]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert '<a href="https://reuters.com/real-article">Reuters coverage</a>' in out
    assert "No sources cited for this call." not in out


# ─── sources from a DB-read row (Phase 3: JSON-encoded TEXT column) ───────────

def test_sources_as_json_string_from_db_round_trip_renders_as_link():
    """core.logger persists `sources` as a JSON-encoded TEXT column -- a
    signal read back from the DB (e.g. by the harness) has `sources` as a
    *string*, not a list. render_subscriber_html must parse it, not choke."""
    import json
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH",
                     sources=json.dumps([{"url": "https://ap.org/x", "title": "AP"}]))]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert '<a href="https://ap.org/x">AP</a>' in out


def test_sources_empty_json_string_from_db_renders_placeholder():
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH", sources="[]")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "No sources cited for this call." in out


def test_sources_null_from_old_db_row_renders_placeholder_not_crash():
    """Pre-Phase-3 rows have sources=NULL -- must fall back gracefully, not raise."""
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH", sources=None)]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "No sources cited for this call." in out


def test_sources_malformed_json_string_falls_back_to_empty_not_raise():
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH", sources="not valid json{")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "No sources cited for this call." in out


# ─── escaping ───────────────────────────────────────────────────────────────────

def test_title_is_html_escaped():
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH",
                     title="Will Trump's Cabinet member resign?")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "&#x27;" in out
    assert "Trump's Cabinet" not in out  # raw apostrophe must not appear unescaped


# ─── kalshi link ────────────────────────────────────────────────────────────────

def test_kalshi_link_present_when_series_ticker_known():
    signals = [_sig(ticker="KXABC-01", direction="YES", confidence="HIGH",
                     series_ticker="KXABC", event_ticker="KXABC-01")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert 'href="https://kalshi.com/markets/kxabc/kxabc-01"' in out


def test_kalshi_link_falls_back_to_hash_when_unknown():
    signals = [_sig(ticker="KXABC-01", direction="YES", confidence="HIGH",
                     series_ticker="", event_ticker="")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert 'href="#">Trade on Kalshi' in out


# ─── digest numbers ─────────────────────────────────────────────────────────────

def test_markets_scanned_rendered():
    out = report.render_subscriber_html([], _run_meta(markets_scanned=12345), _CFG, now_utc=_FIXED_NOW)
    assert "12,345" in out


# ─── resolved-picks recap (GOAL_subscriber_report.md Phase 5) ────────────────

def _resolved(**kwargs):
    base = {
        "ticker": "KXRES-01", "title": "Did the thing happen?",
        "direction": "YES", "our_estimate": 0.62, "result": "WIN", "outcome": "YES",
        "market_drift_pp": None,
    }
    base.update(kwargs)
    return base


def test_resolved_recap_renders_win_and_loss_items():
    recap = [
        _resolved(ticker="KXW", title="A win", result="WIN", outcome="YES"),
        _resolved(ticker="KXL", title="A loss", result="LOSS", outcome="NO"),
    ]
    out = report.render_subscriber_html([], _run_meta(), _CFG, now_utc=_FIXED_NOW, resolved_recap=recap)
    assert out.count('<div class="recap-item">') == 2
    assert "A win" in out and "A loss" in out
    assert "No calls settled in the last 7 days." not in out


def test_resolved_recap_placeholder_when_none():
    out = report.render_subscriber_html([], _run_meta(), _CFG, now_utc=_FIXED_NOW, resolved_recap=None)
    assert "No calls settled in the last 7 days." in out


def test_resolved_recap_placeholder_when_empty_list():
    out = report.render_subscriber_html([], _run_meta(), _CFG, now_utc=_FIXED_NOW, resolved_recap=[])
    assert "No calls settled in the last 7 days." in out


def test_resolved_recap_shows_drift_when_present():
    recap = [_resolved(title="Drifted our way", market_drift_pp=18.4)]
    out = report.render_subscriber_html([], _run_meta(), _CFG, now_utc=_FIXED_NOW, resolved_recap=recap)
    assert "18pt toward us" in out


def test_resolved_recap_omits_drift_note_when_absent():
    recap = [_resolved(title="No drift data", market_drift_pp=None)]
    out = report.render_subscriber_html([], _run_meta(), _CFG, now_utc=_FIXED_NOW, resolved_recap=recap)
    assert "pt toward us" not in out
    assert "pt away from us" not in out


def test_resolved_recap_capped_at_five():
    recap = [_resolved(ticker=f"KXR{i}", title=f"Item {i}") for i in range(8)]
    out = report.render_subscriber_html([], _run_meta(), _CFG, now_utc=_FIXED_NOW, resolved_recap=recap)
    assert out.count('<div class="recap-item">') == 5


# ─── market movers (GOAL_subscriber_report.md Phase 5) ───────────────────────

def test_market_movers_shows_drift_flagged_non_call_signal():
    signals = [
        _sig(ticker="KXMOVE", direction="PASS", confidence="LOW", title="A mover",
             drift_flag=True, price_drift=0.15),
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert '<div class="mover">' in out
    assert "A mover" in out
    assert "Price moved 15% up recently" in out


def test_market_movers_excludes_tickers_already_shown_as_calls():
    """A ticker that's a call must never also headline as a mover, even if
    it happens to carry drift/spread/ob flags."""
    signals = [
        _sig(ticker="KXBOTH", direction="YES", confidence="HIGH", title="Both call and mover",
             drift_flag=True, price_drift=0.20, market_price=0.2, our_estimate=0.9),
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert out.count("Both call and mover") == 1  # only in the calls section
    assert '<div class="mover">' not in out


def test_market_movers_placeholder_when_none_flagged():
    signals = [_sig(ticker="KXQUIET", direction="PASS", confidence="LOW")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "No unusual market moves outside today" in out


def test_market_movers_ob_imbalance_reason():
    signals = [
        _sig(ticker="KXOB", direction="PASS", confidence="LOW", title="OB mover",
             ob_flag=True, ob_imbalance=0.72, ob_direction="YES"),
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "order book leaning 72% toward YES" in out


def test_market_movers_spread_wide_reason():
    signals = [
        _sig(ticker="KXSPREAD", direction="PASS", confidence="LOW", title="Spread mover",
             spread_wide=True, spread_pct=0.09),
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "unusually wide spread (9%)" in out


def test_market_movers_capped_at_three():
    signals = [
        _sig(ticker=f"KXM{i}", direction="PASS", confidence="LOW",
             drift_flag=True, price_drift=0.10 + i * 0.01)
        for i in range(5)
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert out.count('<div class="mover">') == 3


# ─── methodology footer (GOAL_subscriber_report.md Phase 5) ──────────────────

def test_methodology_footer_always_present():
    out = report.render_subscriber_html([], _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "Methodology" in out
    assert "Kalshi" in out and "Polymarket" in out


# ─── Track Record link (GOAL_subscriber_report.md Phase 6) ───────────────────

def test_footer_links_to_track_record_page():
    out = report.render_subscriber_html([], _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert 'href="track_record.html"' in out


# ─── base_url config (GOAL_phase2-6_decisions.md Decision 3) ─────────────────

def test_track_record_link_relative_when_base_url_empty():
    cfg = {"scoring": {"confidence_threshold": "MED"}, "report": {"base_url": ""}}
    out = report.render_subscriber_html([], _run_meta(), cfg, now_utc=_FIXED_NOW)
    assert 'href="track_record.html"' in out


def test_track_record_link_absolute_when_base_url_set():
    cfg = {"scoring": {"confidence_threshold": "MED"},
           "report": {"base_url": "https://leviathan.example.com"}}
    out = report.render_subscriber_html([], _run_meta(), cfg, now_utc=_FIXED_NOW)
    assert 'href="https://leviathan.example.com/track_record.html"' in out


def test_track_record_link_strips_trailing_slash_on_base_url():
    cfg = {"scoring": {"confidence_threshold": "MED"},
           "report": {"base_url": "https://leviathan.example.com/"}}
    out = report.render_subscriber_html([], _run_meta(), cfg, now_utc=_FIXED_NOW)
    assert 'href="https://leviathan.example.com/track_record.html"' in out
    assert "//track_record.html" not in out


# ─── determine_subscriber_shortlist (GOAL_phase2-6_decisions.md Decision 1) ───

def test_shortlist_matches_rendered_calls():
    """The tickers determine_subscriber_shortlist returns must be exactly
    the ones render_subscriber_html would publish as calls -- one
    implementation, not two that could disagree."""
    signals = [
        _sig(ticker="KXA", direction="YES", confidence="HIGH", market_price=0.2, our_estimate=0.9),
        _sig(ticker="KXB", direction="NO",  confidence="MED",  market_price=0.6, our_estimate=0.3),
        _sig(ticker="KXPASS", direction="PASS", confidence="LOW"),
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    shortlist = report.determine_subscriber_shortlist(signals, _CFG)
    tickers = {s["ticker"] for s in shortlist}
    assert tickers == {"KXA", "KXB"}
    assert out.count('<article class="pick">') == len(shortlist)


def test_shortlist_returns_original_signal_dicts_not_view_models():
    """Caller needs to mutate sig['sources'] and pass the same dict to
    logger.log_signal() later -- so this must return the real object, not
    a rendered copy."""
    original = _sig(ticker="KXA", direction="YES", confidence="HIGH", edge=0.42)
    shortlist = report.determine_subscriber_shortlist([original], _CFG)
    assert shortlist[0] is original


def test_shortlist_excludes_pass_and_below_threshold():
    signals = [
        _sig(ticker="KXLOW", direction="YES", confidence="LOW"),
        _sig(ticker="KXPASS", direction="PASS", confidence="HIGH"),
    ]
    shortlist = report.determine_subscriber_shortlist(signals, _CFG)
    assert shortlist == []


def test_shortlist_respects_n_cap():
    signals = [
        _sig(ticker=f"KXA{i}", direction="YES", confidence="HIGH",
             market_price=0.1, our_estimate=0.1 + i * 0.1)
        for i in range(5)
    ]
    shortlist = report.determine_subscriber_shortlist(signals, _CFG, n=2)
    assert len(shortlist) == 2


def test_shortlist_empty_when_no_signals():
    assert report.determine_subscriber_shortlist([], _CFG) == []


def test_output_is_well_formed_html():
    """No unclosed/mismatched tags anywhere in a realistic render."""
    from html.parser import HTMLParser

    class _Checker(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.mismatches = []

        def handle_starttag(self, tag, attrs):
            if tag not in ("meta", "link", "br", "img", "hr", "input"):
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack or self.stack[-1] != tag:
                self.mismatches.append(tag)
            else:
                self.stack.pop()

    signals = [
        _sig(ticker="KXA", direction="YES", confidence="HIGH"),
        _sig(ticker="KXB", direction="NO", confidence="MED"),
        _sig(ticker="KXW", direction="PASS", confidence="LOW",
             drift_flag=True, price_drift=0.12,
             ob_flag=True, ob_imbalance=0.7, ob_direction="YES",
             spread_wide=True, spread_pct=0.08),
    ]
    recap = [
        _resolved(ticker="KXWIN", title="Recap win", result="WIN", outcome="YES", market_drift_pp=12.0),
        _resolved(ticker="KXLOSS", title="Recap loss", result="LOSS", outcome="NO", market_drift_pp=-5.0),
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW, resolved_recap=recap)
    checker = _Checker()
    checker.feed(out)
    assert checker.stack == [], f"unclosed tags: {checker.stack}"
    assert checker.mismatches == [], f"mismatched close tags: {checker.mismatches}"
