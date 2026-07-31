"""
tests/test_track_record_html.py — Tests for core.report.render_track_record_html
(GOAL_subscriber_report.md, Phase 6).

Unlike render_subscriber_html, this function queries the DB directly (same
precedent as _betting_queue_data), so tests point core.logger.DB_PATH at a
throwaway file rather than passing signals in as a parameter.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import logger, report

_FIXED_NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(logger, "DB_PATH", db_file)
    logger._init_db()
    return db_file


def _insert_resolved(call_id, ticker, direction, market_price, our_estimate,
                      result, outcome, pnl, drift=None, confidence="MED",
                      timestamp="2026-07-25T00:00:00Z", source="paper",
                      signal_call_id=None):
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, title, market_price, our_estimate,
             direction, confidence, result, outcome, pnl_if_traded,
             market_drift_pp, source, signal_call_id, run_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id, timestamp, ticker, f"Will {ticker} happen?",
            market_price, our_estimate, direction, confidence,
            result, outcome, pnl, drift, source, signal_call_id, "test",
        ))


def test_renders_with_no_data_at_all(tmp_db):
    """Empty DB must still render a well-formed page with honest n=0 everywhere,
    never a crash or a fabricated number."""
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "n=0" in out
    assert "No resolved signals yet." in out


def test_every_metric_card_shows_its_sample_size(tmp_db):
    """Guardrail: never print an accuracy/win-rate/drift number without N."""
    _insert_resolved("t1", "KXA", "YES", 0.30, 0.55, "WIN", "YES", 0.70, drift=15.0)
    _insert_resolved("t2", "KXB", "NO", 0.60, 0.40, "LOSS", "YES", -0.60, drift=-8.0)
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert out.count("n=") >= 4  # one per metric card, at minimum


def test_hit_rate_and_roi_computed_from_resolved_signals(tmp_db):
    _insert_resolved("t3", "KXC", "YES", 0.30, 0.55, "WIN", "YES", 0.70)
    _insert_resolved("t4", "KXD", "NO", 0.60, 0.40, "LOSS", "YES", -0.60)
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "50%" in out  # 1 win / 2 resolved


def test_market_drift_card_shows_avg_and_positive_pct(tmp_db):
    _insert_resolved("t5", "KXE", "YES", 0.30, 0.55, "WIN", "YES", 0.70, drift=20.0)
    _insert_resolved("t6", "KXF", "NO", 0.60, 0.40, "LOSS", "YES", -0.60, drift=-10.0)
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "+5.0pt" in out  # avg of +20 and -10
    assert "50% of picks drifted our way" in out


# ─── drift as hero metric (GOAL_phase2-6_decisions.md Choice A) ──────────────

def test_drift_card_is_hero_variant(tmp_db):
    _insert_resolved("th1", "KXH1", "YES", 0.30, 0.55, "WIN", "YES", 0.70, drift=20.0)
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert '<div class="mcard mcard-hero">' in out


def test_roi_card_is_secondary_variant_never_hero(tmp_db):
    """Guardrail: ROI must not move into the primary/hero position."""
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    idx_roi = out.find("Edge realized")
    idx_secondary_open = out.rfind('<div class="mcard mcard-secondary">', 0, idx_roi)
    idx_any_card_open = out.rfind('<div class="mcard', 0, idx_roi)
    assert idx_secondary_open == idx_any_card_open  # nearest preceding card-open is the secondary one


def test_hero_card_appears_before_secondary_cards_in_dom_order(tmp_db):
    _insert_resolved("th2", "KXH2", "YES", 0.30, 0.55, "WIN", "YES", 0.70, drift=20.0)
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    idx_hero  = out.find('<div class="mcard mcard-hero">')
    idx_roi   = out.find("Edge realized")
    assert idx_hero != -1 and idx_roi != -1
    assert idx_hero < idx_roi


def test_equity_curve_renders_sparkline_when_enough_points(tmp_db):
    _insert_resolved("t7", "KXG", "YES", 0.30, 0.55, "WIN", "YES", 0.70,
                      timestamp="2026-07-01T00:00:00Z")
    _insert_resolved("t8", "KXH", "NO", 0.60, 0.40, "LOSS", "YES", -0.60,
                      timestamp="2026-07-02T00:00:00Z")
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "<svg" in out
    assert "<polyline" in out


def test_equity_curve_no_sparkline_with_fewer_than_two_points(tmp_db):
    _insert_resolved("t9", "KXI", "YES", 0.30, 0.55, "WIN", "YES", 0.70)
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "<svg" not in out


def test_equity_curve_real_fill_reflected_in_final_value(tmp_db):
    _insert_resolved("t10", "KXJ", "YES", 0.30, 0.55, "WIN", "YES", 0.70)
    _insert_resolved("t10-fill", "KXJ", "YES", 0.30, 0.55, "WIN", "YES", 0.90,
                      source="real_fill", signal_call_id="t10",
                      timestamp="2026-07-25T01:00:00Z")
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "+0.90" in out  # real fill's pnl wins over the paper 0.70


# ─── real vs paper equity distinction (GOAL_phase2-6_decisions.md Choice B) ──

def test_equity_curve_footnote_shows_real_and_paper_counts(tmp_db):
    _insert_resolved("t19", "KXK", "YES", 0.30, 0.55, "WIN", "YES", 0.70,
                      timestamp="2026-07-01T00:00:00Z")
    _insert_resolved("t20", "KXL", "YES", 0.30, 0.55, "WIN", "YES", 0.60,
                      timestamp="2026-07-02T00:00:00Z")
    _insert_resolved("t20-fill", "KXL", "YES", 0.30, 0.55, "WIN", "YES", 0.85,
                      source="real_fill", signal_call_id="t20",
                      timestamp="2026-07-02T01:00:00Z")
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "1 real-money point (solid marker)" in out
    assert "1 paper (hypothetical)" in out


def test_equity_curve_real_point_gets_solid_marker(tmp_db):
    _insert_resolved("t21", "KXM", "YES", 0.30, 0.55, "WIN", "YES", 0.70,
                      timestamp="2026-07-01T00:00:00Z")
    _insert_resolved("t22", "KXN", "YES", 0.30, 0.55, "WIN", "YES", 0.60,
                      timestamp="2026-07-02T00:00:00Z")
    _insert_resolved("t22-fill", "KXN", "YES", 0.30, 0.55, "WIN", "YES", 0.85,
                      source="real_fill", signal_call_id="t22",
                      timestamp="2026-07-02T01:00:00Z")
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "<circle" in out


def test_equity_curve_no_marker_or_stray_footnote_when_all_paper(tmp_db):
    _insert_resolved("t23", "KXO", "YES", 0.30, 0.55, "WIN", "YES", 0.70,
                      timestamp="2026-07-01T00:00:00Z")
    _insert_resolved("t24", "KXP", "YES", 0.30, 0.55, "WIN", "YES", 0.60,
                      timestamp="2026-07-02T00:00:00Z")
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "<circle" not in out
    assert "0 real-money point" in out
    assert "2 paper (hypothetical)" in out


def test_full_log_shows_wins_and_losses_not_curated(tmp_db):
    """Publish the full signal log -- losses must appear alongside wins, not
    be filtered out."""
    _insert_resolved("t11", "KXWIN", "YES", 0.30, 0.55, "WIN", "YES", 0.70)
    _insert_resolved("t12", "KXLOSS", "NO", 0.60, 0.40, "LOSS", "YES", -0.60)
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "KXWIN" in out or "Will KXWIN happen?" in out
    assert "KXLOSS" in out or "Will KXLOSS happen?" in out
    assert "Full signal log (2 resolved)" in out


def test_full_log_excludes_unresolved_and_pass_rows(tmp_db):
    _insert_resolved("t13", "KXRES", "YES", 0.30, 0.55, "WIN", "YES", 0.70)
    with logger._db() as conn:
        conn.execute(
            "INSERT INTO signals (call_id,timestamp,ticker,direction,result,outcome,source) "
            "VALUES ('t14',datetime('now'),'KXOPEN','YES','','','paper')"
        )
        conn.execute(
            "INSERT INTO signals (call_id,timestamp,ticker,direction,result,outcome,source) "
            "VALUES ('t15',datetime('now'),'KXPASS','PASS','WIN','YES','paper')"
        )
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "Full signal log (1 resolved)" in out
    assert "KXOPEN" not in out
    assert "KXPASS" not in out


def test_output_is_well_formed_html(tmp_db):
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

    _insert_resolved("t16", "KXA", "YES", 0.30, 0.55, "WIN", "YES", 0.70, drift=10.0,
                      timestamp="2026-07-01T00:00:00Z")
    _insert_resolved("t17", "KXB", "NO", 0.60, 0.40, "LOSS", "YES", -0.60, drift=-5.0,
                      timestamp="2026-07-02T00:00:00Z")
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    checker = _Checker()
    checker.feed(out)
    assert checker.stack == [], f"unclosed tags: {checker.stack}"
    assert checker.mismatches == [], f"mismatched close tags: {checker.mismatches}"


def test_process_signal_framing_present(tmp_db):
    """Doc guardrail: frame drift as a process signal, not a guarantee."""
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "process signal, not a guarantee" in out


def test_title_escaped_in_log_row(tmp_db):
    _insert_resolved("t18", "KXQ", "YES", 0.30, 0.55, "WIN", "YES", 0.70)
    with logger._db() as conn:
        conn.execute("UPDATE signals SET title=? WHERE call_id='t18'",
                     ("Will Trump's Cabinet resign?",))
    out = report.render_track_record_html(now_utc=_FIXED_NOW)
    assert "&#x27;" in out
    assert "Trump's Cabinet" not in out
