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
    assert "We don't have a saved write-up for this one yet" not in out


def test_placeholder_analysis_when_reasoning_absent():
    """
    backlog: subscriber-report-rework-2026-08. Was "Full written analysis
    renders here once reasoning is persisted per signal" -- an internal
    implementation note (referencing the DB persistence mechanism) leaking
    into subscriber-facing copy. Still says nothing was saved for this call,
    but reads like a product, not a TODO.
    """
    signals = [_sig(ticker="KXA", direction="YES", confidence="HIGH")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert "We don't have a saved write-up for this one yet" in out
    assert "renders here once reasoning is persisted" not in out


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


def test_next_resolve_only_considers_actually_published_picks():
    """
    Regression guard: with more than 3 qualifying calls, "Next to resolve"
    must reflect the top-3 ranked picks that actually get published (same
    ranking _rank_top_picks uses), never an earlier-in-list-but-unranked
    call that didn't make the cut. KXSOON below still qualifies as a call
    (MED confidence, at the threshold) but ranks 4th on edge -- the
    weakest of the batch -- so it does NOT make the published top-3. It's
    listed FIRST in the input (scan order) but closes soonest -- a naive
    calls[:3] slice would wrongly pick it up and surface its close date.
    """
    signals = [
        _sig(ticker="KXSOON", direction="YES", confidence="MED", edge=0.05, close_time="2026-08-01T00:00:00Z"),
        _sig(ticker="KXA", direction="YES", confidence="HIGH", edge=0.30, close_time="2026-09-01T00:00:00Z"),
        _sig(ticker="KXB", direction="NO",  confidence="HIGH", edge=0.25, close_time="2026-09-05T00:00:00Z"),
        _sig(ticker="KXC", direction="YES", confidence="MED",  edge=0.20, close_time="2026-09-10T00:00:00Z"),
    ]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert '<div class="n">Sep 1</div><div class="l">Next to resolve</div>' in out
    assert "Aug 1" not in out


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


# ─── Whale / smart-money corroboration (subscriber-report-rework-2026-08) ──────
#
# whale_detected/whale_direction/whale_max_trade_size/smart_money_count/
# smart_money_dir are all computed on every signal in main.py, but
# _rank_top_picks never carried them into the pick view model at all --
# same class of gap as main.py's own heuristic_label omission fixed the
# same day (db-audit-2026-08). Watch items bypass _rank_top_picks entirely
# (raw signal dicts), so they never needed the carry-through fix, just the
# rendering.

def test_corroboration_note_agrees_with_call_direction():
    note = report._subscriber_corroboration_note(
        call_direction="YES", whale_detected=True, whale_direction="YES",
        smart_money_count=0, smart_money_dir=None,
    )
    assert note is not None
    assert "same side as this call" in note["text"]


def test_corroboration_note_conflicts_with_call_direction():
    note = report._subscriber_corroboration_note(
        call_direction="YES", whale_detected=True, whale_direction="NO",
        smart_money_count=0, smart_money_dir=None,
    )
    assert note is not None
    assert "opposite side of this call" in note["text"]


def test_corroboration_note_prefers_whale_over_smart_money_when_both_present():
    note = report._subscriber_corroboration_note(
        call_direction="YES", whale_detected=True, whale_direction="YES",
        smart_money_count=3, smart_money_dir="YES",
    )
    assert note is not None
    assert "large trader" in note["text"]
    assert "historically sharp" not in note["text"]


def test_corroboration_note_falls_back_to_smart_money_when_no_whale():
    note = report._subscriber_corroboration_note(
        call_direction="YES", whale_detected=False, whale_direction=None,
        smart_money_count=2, smart_money_dir="YES",
    )
    assert note is not None
    assert "2 historically sharp traders" in note["text"]
    assert "same side as this call" in note["text"]


def test_corroboration_note_singular_trader_grammar():
    note = report._subscriber_corroboration_note(
        call_direction="NO", whale_detected=False, whale_direction=None,
        smart_money_count=1, smart_money_dir="NO",
    )
    assert note is not None
    assert "1 historically sharp trader " in note["text"]  # not "traders"


def test_corroboration_note_none_when_no_signal_at_all():
    note = report._subscriber_corroboration_note(
        call_direction="YES", whale_detected=False, whale_direction=None,
        smart_money_count=0, smart_money_dir=None,
    )
    assert note is None


def test_corroboration_note_watch_item_has_no_agree_conflict_framing():
    """call_direction=None (a watch item, no call made) states the fact
    plainly -- there's nothing to agree or conflict with."""
    note = report._subscriber_corroboration_note(
        call_direction=None, whale_detected=True, whale_direction="YES",
        smart_money_count=0, smart_money_dir=None,
    )
    assert note is not None
    assert "same side" not in note["text"]
    assert "opposite side" not in note["text"]
    assert "hasn't cleared our own bar to call yet" in note["text"]


def test_pick_renders_whale_pill_and_band_when_corroborated():
    signals = [_sig(ticker="KXWHALE", direction="YES", confidence="HIGH",
                     whale_detected=True, whale_direction="YES")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert 'tag-whale">Smart money</span>' in out
    assert "same side as this call" in out


def test_pick_omits_whale_pill_when_not_corroborated():
    # "tag-whale" alone would also match the CSS class definition in the
    # <style> block, which is always present -- check for the actual
    # rendered pill markup, not just the class name existing anywhere.
    signals = [_sig(ticker="KXPLAIN", direction="YES", confidence="HIGH")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert 'tag tag-whale' not in out


def test_watch_item_renders_whale_pill_when_corroborated():
    signals = [_sig(ticker="KXWATCHWHALE", direction="PASS", confidence="LOW",
                     whale_detected=True, whale_direction="NO")]
    out = report.render_subscriber_html(signals, _run_meta(), _CFG, now_utc=_FIXED_NOW)
    assert 'tag-whale">Smart money</span>' in out
    assert "hasn't cleared our own bar to call yet" in out


# ─── Heuristic-label-specific "Why flagged" copy ───────────────────────────────

def test_why_flagged_uses_heuristic_label_when_present():
    label, text = report._subscriber_why_flagged("HEURISTIC", "IPO announcement")
    assert "IPO timing questions" in text


def test_why_flagged_falls_back_to_bare_label_when_not_in_gloss():
    """A heuristic_label with no entry in _SUBSCRIBER_HEURISTIC_GLOSS still
    produces a specific sentence (using the bare label text, already fairly
    plain English), not silence or a crash."""
    label, text = report._subscriber_why_flagged("HEURISTIC", "government shutdown avoided")
    assert "government shutdown avoided" in text


def test_why_flagged_generic_when_no_heuristic_label():
    """No heuristic_label at all (e.g. an older signal from before
    db-audit-2026-08's main.py fix) falls back to the original generic
    sentence -- never crashes, never blank."""
    label, text = report._subscriber_why_flagged("HEURISTIC", None)
    assert text == report.SUBSCRIBER_WHY_FLAGGED["HEURISTIC"][1]


def test_why_flagged_non_heuristic_flag_path_unaffected():
    """DRIFT/EDGE/etc. never had per-label specificity and don't gain any --
    heuristic_label is only meaningful for HEURISTIC-flagged picks."""
    label, text = report._subscriber_why_flagged("DRIFT", "some heuristic label")
    assert text == report.SUBSCRIBER_WHY_FLAGGED["DRIFT"][1]
