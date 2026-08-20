"""
tests/test_report_html.py — Tests for the email-safe HTML renderer
(core.report.render_html) and the multipart/alternative send path
(core.report.send_report).

No live SMTP: smtplib.SMTP is monkeypatched throughout. No live network:
core.kalshi.kalshi_market_url is monkeypatched where a resolved link is
needed to test the "has href" case — its real (goal_1) behavior of
always returning None is exercised directly in the "no href" tests.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import report


# ─── helpers (mirrors tests/test_report.py's conventions) ────────────────────

def _run_meta(**kwargs):
    base = {
        "run_id":            "test-run-1",
        "timestamp":         "2026-06-17T10:00:00Z",
        "markets_scanned":   2583,
        "signals_generated": 1,
        "whale_flags":       0,
        "model_used":        "claude-sonnet-4-6",
        "tokens_used":       8000,
        "cost_usd":          0.0,
        "runtime_ms":        939000,
        "high_price_filtered": 0,
    }
    base.update(kwargs)
    return base


def _sig(ticker="KXTST-01", direction="YES", confidence="MED", edge=0.15,
         time_horizon="MONTHLY", market_price=0.30, our_estimate=0.45,
         event_ticker="", series_ticker="", **kwargs):
    base = {
        "ticker":          ticker,
        "event_ticker":    event_ticker,
        "series_ticker":   series_ticker,
        "title":           f"Will {ticker} happen?",
        "direction":       direction,
        "confidence":      confidence,
        "edge":            edge,
        "time_horizon":    time_horizon,
        "market_price":    market_price,
        "our_estimate":    our_estimate,
        "flag_path":       None,
        "watchlist_signal": False,
        "smart_money":     [],
        "poly":            None,
        "ext_markets":     [],
        "is_repeat":       False,
        "repeat_count":    0,
    }
    base.update(kwargs)
    return base


_EMPTY_STATS = {"total_calls": 0, "resolved": 0, "win_rate": None,
                "avg_edge_captured": None, "total_hypothetical_pnl": None}
_CFG = {"scoring": {"confidence_threshold": "MED"}, "environment": "demo",
        "betting": {"unit_size": 10, "min_ev_pct_of_unit": 0.25}}

_FIXED_NOW = datetime(2026, 7, 19, 6, 16, tzinfo=timezone.utc)


# ─── shared values: text and html cannot diverge ─────────────────────────────

def test_render_html_matches_text_renderer_edge_value():
    """The same signal's edge must appear identically in both bodies."""
    s = _sig(ticker="KXSHARED-01", edge=0.195, confidence="MED", direction="NO")
    text_body = report.compile_report([s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                      new_signals=[], repeat_signals=[s],
                                      now_utc=_FIXED_NOW)
    html_body = report.render_html([s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                   new_signals=[], repeat_signals=[s],
                                   now_utc=_FIXED_NOW)
    assert "Edge: +19.5 pp" in text_body
    assert "+19.5" in html_body


def test_render_html_matches_text_renderer_header_counts():
    """New/Repeat/Whale counts must be identical between bodies."""
    new_s = _sig(ticker="KXNEW-01")
    rep_s = _sig(ticker="KXREP-01")
    text_body = report.compile_report([new_s, rep_s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                      new_signals=[new_s], repeat_signals=[rep_s],
                                      now_utc=_FIXED_NOW)
    html_body = report.render_html([new_s, rep_s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                   new_signals=[new_s], repeat_signals=[rep_s],
                                   now_utc=_FIXED_NOW)
    assert "New Signals:    1" in text_body
    assert "Repeat Signals: 1" in text_body
    # HTML summary tiles show the same two counts as bare numbers
    assert ">1</div>" in html_body  # both New and Repeat tiles show "1"


def test_render_html_market_est_kelly_match_text_for_top_pick():
    """Top-pick stat row (Market/Est/Kelly) must use the SAME computed values."""
    s = _sig(ticker="KXKELLY-01", direction="YES", market_price=0.30, our_estimate=0.60, edge=0.30)
    text_body = report.compile_report([s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                      new_signals=[s], repeat_signals=[],
                                      now_utc=_FIXED_NOW)
    html_body = report.render_html([s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                   new_signals=[s], repeat_signals=[],
                                   now_utc=_FIXED_NOW)
    assert "Market: 30.0%" in text_body
    assert "30.0%" in html_body
    assert "Est: 60.0%" in text_body
    assert "60.0%" in html_body


def test_render_html_and_text_use_same_betting_queue_query(tmp_path):
    """Both renderers must show identical betting-queue contents for one DB."""
    import sqlite3
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE signals (
            call_id TEXT, ticker TEXT, direction TEXT, market_price REAL,
            our_estimate REAL, edge REAL, close_time TEXT,
            confidence TEXT, result TEXT, source TEXT, timestamp TEXT, title TEXT,
            event_ticker TEXT, series_ticker TEXT
        );
    """)
    conn.execute(
        "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("bq1", "KXBQSHARED-01", "YES", 0.30, 0.60, 0.30,
         "2026-12-31T00:00:00Z", "HIGH", "", "paper",
         "2026-06-20T00:00:00Z", "Shared queue title", "KXBQSHARED-01-EVT", "KXBQSHARED"),
    )
    conn.commit()
    conn.close()

    text_body = report.compile_report([], [], _EMPTY_STATS, _run_meta(), _CFG,
                                      new_signals=[], repeat_signals=[],
                                      db_path=str(db), now_utc=_FIXED_NOW)
    html_body = report.render_html([], [], _EMPTY_STATS, _run_meta(), _CFG,
                                   new_signals=[], repeat_signals=[],
                                   db_path=str(db), now_utc=_FIXED_NOW)
    assert "KXBQSHARED-01" in text_body
    assert "KXBQSHARED-01" in html_body
    assert "Shared queue title" in text_body
    assert "Shared queue title" in html_body


# ─── Kalshi links (PART C, upgraded 2026-07-23 — pattern now confirmed) ──────

def test_html_row_with_series_and_event_ticker_gets_real_href():
    """
    With BOTH series_ticker and event_ticker present, the REAL (unmocked)
    kalshi_market_url now constructs a working link — no mock needed,
    since the pattern is confirmed (see core/kalshi.py, docs/PROGRESS.md).
    """
    s = _sig(ticker="KXLINKED-01", series_ticker="KXLINKED", event_ticker="KXLINKED-01-EVT")
    html_body = report.render_html([s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                   new_signals=[s], repeat_signals=[],
                                   now_utc=_FIXED_NOW)
    assert 'href="https://kalshi.com/markets/kxlinked/kxlinked-01-evt"' in html_body


def test_html_row_with_mocked_resolver_gets_href():
    """The render-layer wiring itself (independent of kalshi_market_url's
    specific logic) correctly turns a resolved URL into an <a href>."""
    s = _sig(ticker="KXLINKED-02", series_ticker="KXLINKED2", event_ticker="KXLINKED-02-EVT")
    with patch.object(report, "kalshi_market_url",
                      side_effect=lambda st, et=None: f"https://kalshi.com/markets/{st}/{et}" if st and et else None):
        html_body = report.render_html([s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                       new_signals=[s], repeat_signals=[],
                                       now_utc=_FIXED_NOW)
    assert 'href="https://kalshi.com/markets/KXLINKED2/KXLINKED-02-EVT"' in html_body


def test_html_row_with_empty_event_ticker_has_no_href(tmp_path):
    """A row missing event_ticker (series_ticker present or not) shows the
    bare ticker with NO href anywhere for it."""
    s = _sig(ticker="KXBARE-01", series_ticker="KXBARE", event_ticker="")
    # db_path pointed at a not-yet-existing file: render_html's betting-queue
    # section (_betting_queue_data) otherwise falls back to core.logger's
    # real DB_PATH, which can legitimately contain unrelated real signals
    # with valid hrefs (e.g. after a live pipeline run) and make this
    # single-signal assertion depend on the local machine's live data.
    html_body = report.render_html([s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                   new_signals=[s], repeat_signals=[],
                                   db_path=str(tmp_path / "empty.db"), now_utc=_FIXED_NOW)
    assert "KXBARE-01" in html_body
    assert 'href=""' not in html_body
    # event_ticker missing -> kalshi_market_url (real, unmocked) returns
    # None, so no <a href> should appear anywhere in this single-signal render.
    assert "<a href" not in html_body


def test_html_row_with_empty_series_ticker_has_no_href(tmp_path):
    """A row missing series_ticker (the field a pre-upgrade row would lack)
    shows the bare ticker with NO href, even with a valid event_ticker."""
    s = _sig(ticker="KXNOSERIES-01", series_ticker="", event_ticker="KXNOSERIES-01-EVT")
    html_body = report.render_html([s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                   new_signals=[s], repeat_signals=[],
                                   db_path=str(tmp_path / "empty.db"), now_utc=_FIXED_NOW)
    assert "KXNOSERIES-01" in html_body
    assert 'href=""' not in html_body
    assert "<a href" not in html_body


def test_html_never_reintroduces_confirmed_404_form():
    """Regression: must never emit the bare market-ticker (no series
    segment) kalshi.com/markets/{ticker} 404-prone form as a real href,
    even when a link IS legitimately resolved for this signal."""
    s = _sig(ticker="KXNOFORM-01", series_ticker="KXNOFORM", event_ticker="KXNOFORM-01")
    html_body = report.render_html([s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                   new_signals=[s], repeat_signals=[],
                                   now_utc=_FIXED_NOW)
    assert 'href="https://kalshi.com/markets/KXNOFORM-01"' not in html_body
    assert 'href="https://kalshi.com/markets/kxnoform-01"' not in html_body
    # The real, confirmed two-segment form IS expected to appear instead.
    assert 'href="https://kalshi.com/markets/kxnoform/kxnoform-01"' in html_body


# ─── multipart/alternative send (PART D) ─────────────────────────────────────

def _cfg_with_report():
    return {"report": {"email_to": "owner@example.com", "email_from": "owner@example.com",
                       "smtp_host": "smtp.example.com", "smtp_port": 587}}


@pytest.fixture(autouse=True)
def _gmail_password(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "fake-password")


def test_send_report_with_html_body_is_multipart_alternative():
    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    captured = {}

    def _fake_sendmail(from_addr, to_addr, msg_string):
        captured["msg_string"] = msg_string

    mock_smtp.sendmail.side_effect = _fake_sendmail

    with patch.object(report, "smtplib") as mock_smtplib, \
         patch("core.subscribers.get_active_subscribers", return_value=[]):
        mock_smtplib.SMTP.return_value = mock_smtp
        report.send_report("plain text body", [], 0, _cfg_with_report(),
                           html_body="<html><body>hi</body></html>")

    assert "msg_string" in captured
    raw = captured["msg_string"]
    assert "multipart/alternative" in raw
    assert "Content-Type: text/plain" in raw
    assert "Content-Type: text/html" in raw
    # text part must be non-empty (fallback body, never dropped)
    assert "plain text body" in raw or "cGxhaW4gdGV4dCBib2R5" in raw  # allow base64 encoding


def test_send_report_without_html_body_stays_single_part():
    """Existing callers (e.g. weekly digest) that never pass html_body must
    keep getting a plain single-part message — behavior unchanged."""
    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    captured = {}
    mock_smtp.sendmail.side_effect = lambda f, t, m: captured.update(msg_string=m)

    with patch.object(report, "smtplib") as mock_smtplib, \
         patch("core.subscribers.get_active_subscribers", return_value=[]):
        mock_smtplib.SMTP.return_value = mock_smtp
        report.send_report("plain only", [], 0, _cfg_with_report())

    raw = captured["msg_string"]
    assert "multipart" not in raw


def test_send_report_preserves_subject_and_recipients():
    mock_smtp = MagicMock()
    mock_smtp.__enter__ = MagicMock(return_value=mock_smtp)
    mock_smtp.__exit__ = MagicMock(return_value=False)

    sent_to = []
    mock_smtp.sendmail.side_effect = lambda f, t, m: sent_to.append(t)

    with patch.object(report, "smtplib") as mock_smtplib, \
         patch("core.subscribers.get_active_subscribers", return_value=[]):
        mock_smtplib.SMTP.return_value = mock_smtp
        report.send_report("body", [], 0, _cfg_with_report(),
                           subject_override="Custom Subject",
                           html_body="<html></html>")

    assert sent_to == ["owner@example.com"]


# ─── Track Record guard ───────────────────────────────────────────────────────

def test_html_never_contains_track_record():
    """Track Record is deliberately dropped from the HTML — lives in Power BI."""
    s = _sig(ticker="KXTR-01")
    html_body = report.render_html([s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                   new_signals=[s], repeat_signals=[],
                                   now_utc=_FIXED_NOW)
    assert "Track Record" not in html_body
    assert "TRACK RECORD" not in html_body


def test_text_report_still_contains_track_record():
    """Confirms Track Record was ONLY removed from HTML, not from text."""
    s = _sig(ticker="KXTR-02")
    text_body = report.compile_report([s], [], _EMPTY_STATS, _run_meta(), _CFG,
                                      new_signals=[s], repeat_signals=[],
                                      now_utc=_FIXED_NOW)
    assert "TRACK RECORD" in text_body


# ─── dry-run CLI (PART D) ─────────────────────────────────────────────────────

def test_dry_run_writes_html_file_and_no_smtp(tmp_path):
    out_path = str(tmp_path / "dry_run.html")
    with patch.object(report, "smtplib") as mock_smtplib:
        report._dry_run(out_path)
        mock_smtplib.SMTP.assert_not_called()

    assert os.path.exists(out_path)
    with open(out_path, encoding="utf-8") as f:
        content = f.read()
    assert content.startswith("<!DOCTYPE html>")
    assert "Track Record" not in content


def test_dry_run_prints_shared_values_check(tmp_path, capsys):
    out_path = str(tmp_path / "dry_run.html")
    report._dry_run(out_path)
    captured = capsys.readouterr()
    assert "SHARED VALUES CHECK" in captured.out
    assert "No SMTP call made" in captured.out


# ─── Editorial design system (leviathan-report-format-decision.md Phase 1) ───
#
# _editorial_root_css / _flatten_editorial_vars are the single source the
# subscriber, track-record, and (Phase 2) weekly editorial templates all
# draw their design tokens from. Byte-stability of _editorial_root_css's
# default output is a real regression guard, not busywork: the whole point
# of Phase 1 was that the subscriber template's rendered HTML must stay
# byte-for-byte identical to the pre-extraction version.

class TestEditorialDesignSource:

    def test_editorial_root_css_byte_stable_output(self):
        """
        Locks the exact string every editorial template's .format() call
        embeds -- a regression guard against ACCIDENTAL drift, not a freeze
        on the design. Deliberate design changes (like ink-faint's
        2026-08-20 contrast fix, #949AA5 -> #646B77) update this test
        intentionally; an unexpected diff here means something changed the
        shared tokens without meaning to.
        """
        expected = (
            ":root{\n"
            "    --paper:#FBFAF7; --ink:#15181E; --ink-soft:#525A67; --ink-faint:#646B77;\n"
            "    --line:#E7E4DC; --line-soft:#EFEDE7; --slate:#1C2A3A;\n"
            "    --edge:#0B6E52; --edge-soft:#E7F0EB; --amber:#9A5A12; --amber-soft:#F4ECDE;\n"
            "    --whale:#2F4C8C; --whale-soft:#E8ECF5;\n"
            "    --serif:\"Newsreader\",Georgia,serif; --sans:\"Inter\",-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;\n"
            "    --mono:\"IBM Plex Mono\",ui-monospace,Consolas,Menlo,monospace;\n"
            "    --sp-3:24px; --sp-6:48px;\n"
            "  }"
        )
        assert report._editorial_root_css() == expected

    def test_editorial_root_css_uses_passed_tokens_not_default(self):
        custom = {"paper": "#FFFFFF"}
        # Only tokens present in the passed dict that also appear in the
        # line-group layout are rendered -- passing a minimal dict missing
        # most keys should raise, proving the function doesn't silently
        # fall back to the default for missing entries.
        with pytest.raises(KeyError):
            report._editorial_root_css(custom)

    def test_flatten_editorial_vars_substitutes_known_tokens(self):
        css = "body{background:var(--paper); color:var(--ink);}"
        out = report._flatten_editorial_vars(css)
        assert out == "body{background:#FBFAF7; color:#15181E;}"
        assert "var(" not in out

    def test_flatten_editorial_vars_unknown_token_raises(self):
        with pytest.raises(KeyError, match="unknown-token"):
            report._flatten_editorial_vars("a{color:var(--unknown-token);}")

    def test_flatten_editorial_vars_no_vars_passthrough(self):
        css = "a{color:#000;}"
        assert report._flatten_editorial_vars(css) == css

    def test_flatten_editorial_vars_multiline_and_repeated(self):
        css = "a{color:var(--edge);}\nb{color:var(--edge); background:var(--edge-soft);}"
        out = report._flatten_editorial_vars(css)
        assert out == "a{color:#0B6E52;}\nb{color:#0B6E52; background:#E7F0EB;}"

    def test_subscriber_template_embeds_editorial_root_css(self):
        html = report.render_subscriber_html([], {"markets_scanned": 0}, {}, now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert report._editorial_root_css() in html

    def test_track_record_template_embeds_editorial_root_css(self, tmp_path, monkeypatch):
        from core import logger as _logger
        db_file = str(tmp_path / "test_editorial.db")
        monkeypatch.setattr(_logger, "DB_PATH", db_file)
        _logger._init_db()
        html = report.render_track_record_html(now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert report._editorial_root_css() in html
        # Track record's pre-extraction :root block never had these tokens
        # (documented, intentional reconciliation, not drift) -- confirms
        # the shared source actually reached this template.
        assert "--whale:#2F4C8C" in html
        assert "--sp-3:24px" in html
