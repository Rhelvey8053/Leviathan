"""
tests/test_kalshi_url.py — Tests for core.kalshi.kalshi_market_url.

CONFIRMED PATTERN (2026-07-23, superseding the 2026-07-22 "no pattern
confirmed" finding): kalshi_market_url(series_ticker, event_ticker) builds
https://kalshi.com/markets/{series_ticker}/{event_ticker} (lowercased).
See core/kalshi.py's docstring and docs/PROGRESS.md for the full
evidence trail (Kalshi's own AGENTS.md, sitemap-markets.xml, and a
genuine server-side 308 redirect chain from /events/{ticker} that
resolves real tickers into this exact shape but does NOT resolve fake
ones the same way).

Offline unit tests assert the pure construction/fallback logic. The
@pytest.mark.network test performs the live check that actually backs
the confirmation: it hits kalshi.com's /events/{ticker} redirect for
real tickers AND a fabricated one, and shows the redirect resolves the
real ticker into a genuine series/ticker structure while leaving the
fake one unresolved — the server-side signal that distinguishes them
(the earlier /markets/{ticker} catch-all route could NOT distinguish
real from fake by design; this is a different, load-bearing mechanism).
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.kalshi import kalshi_market_url


# ─── offline: helper behavior ─────────────────────────────────────────────────

def test_kalshi_market_url_constructs_confirmed_pattern():
    """series_ticker + event_ticker -> https://kalshi.com/markets/{series}/{event}, lowercased."""
    url = kalshi_market_url("KXCABLEAVE", "KXCABLEAVE-26MAY22")
    assert url == "https://kalshi.com/markets/kxcableave/kxcableave-26may22"


def test_kalshi_market_url_lowercases_mixed_case_input():
    url = kalshi_market_url("KxIsrNormCount", "KXISRNORMCOUNT-27Dec31")
    assert url == "https://kalshi.com/markets/kxisrnormcount/kxisrnormcount-27dec31"


def test_kalshi_market_url_none_when_series_ticker_missing():
    assert kalshi_market_url("", "KXCABLEAVE-26MAY22") is None
    assert kalshi_market_url(None, "KXCABLEAVE-26MAY22") is None


def test_kalshi_market_url_none_when_event_ticker_missing():
    assert kalshi_market_url("KXCABLEAVE", "") is None
    assert kalshi_market_url("KXCABLEAVE") is None  # event_ticker omitted entirely


def test_kalshi_market_url_none_when_both_missing():
    assert kalshi_market_url("", "") is None
    assert kalshi_market_url(None, None) is None


def test_kalshi_market_url_never_returns_the_confirmed_404_bare_ticker_form():
    """Regression guard: must never emit the bare market-ticker (no series) 404-prone form."""
    result = kalshi_market_url("KXSOMESERIES", "KXSOMEEVENT-26AUG01")
    assert result == "https://kalshi.com/markets/kxsomeseries/kxsomeevent-26aug01"
    assert result != "https://kalshi.com/markets/KXSOMEEVENT-26AUG01"
    assert result != "https://kalshi.com/markets/kxsomeevent-26aug01"  # no series segment at all


def test_signal_with_missing_series_ticker_renders_bare_ticker_no_href():
    """
    Simulates the report-rendering contract: a caller gets a falsy url back
    (e.g. because series_ticker wasn't captured for this older row) and
    must render plain ticker text, never href="".
    """
    ticker = "KXNOSERIESTICKER-26JAN01"
    url = kalshi_market_url("", "KXNOSERIESTICKER-26JAN01")
    rendered = f'<a href="{url}">{ticker}</a>' if url else ticker
    assert rendered == ticker
    assert "href=" not in rendered


# ─── live resolve-check (the confirmation mechanism) ─────────────────────────

@pytest.mark.network
def test_live_events_redirect_distinguishes_real_from_fake_tickers():
    """
    Hits kalshi.com's /events/{ticker} endpoint (server-side 308 redirects,
    NOT the client-rendered /markets/[...slug] catch-all this project
    previously ruled out) for >= 2 real, currently-listed Kalshi event
    tickers, plus one fabricated ticker. Asserts the real tickers redirect
    into a genuine two-segment series/ticker structure — the SAME shape
    kalshi_market_url constructs — while the fake ticker's redirect does
    NOT resolve into that structure, confirming this is a real server-side
    lookup, not path rewriting that would apply to any input.

    Requires live Kalshi API credentials (config.json / .env) and network
    access — skipped automatically if unavailable so offline CI is
    unaffected. Run explicitly with: pytest tests/test_kalshi_url.py -m network -v -s
    """
    requests = pytest.importorskip("requests")

    try:
        from core import kalshi as kalshi_client
        cfg_path = ROOT / "config.json"
        if not cfg_path.exists():
            pytest.skip("config.json not present — cannot authenticate with Kalshi")
        with open(cfg_path, encoding="utf-8") as f:
            config = json.load(f)
        kalshi_client.authenticate(config)
        events = kalshi_client.fetch_events(config)
    except Exception as e:
        pytest.skip(f"Live Kalshi API unavailable in this environment: {e}")

    real = [(e["event_ticker"], e["series_ticker"]) for e in events[:2]
            if e.get("event_ticker") and e.get("series_ticker")]
    assert len(real) >= 2, "Need at least 2 real (event_ticker, series_ticker) pairs to test against"

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def _redirect_target(event_ticker: str) -> str:
        url = f"https://kalshi.com/events/{event_ticker}"
        r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        return r.url.replace("https://kalshi.com", "").strip("/")

    print("\n=== Live /events/ redirect resolve-check ===")
    for event_ticker, series_ticker in real:
        final_path = _redirect_target(event_ticker)  # e.g. "markets/kxhooda/kxhooda-28janfunded"
        segments = final_path.split("/")
        # Real tickers resolve into markets/{series}/{ticker} — three
        # segments where segments[1] matches the KNOWN series_ticker
        # (lowercased) — a genuine DB lookup, confirmed against data
        # independently fetched from the live trading API, not assumed.
        resolved_to_series = (
            len(segments) >= 3 and segments[0] == "markets"
            and segments[1] == series_ticker.lower()
        )
        print(f"  REAL  {event_ticker:<30} series={series_ticker:<20} "
              f"-> /{final_path}  resolved_to_series={resolved_to_series}")
        assert resolved_to_series, (
            f"{event_ticker} did not redirect into its known series {series_ticker!r} "
            f"(got /{final_path})"
        )

    fake_ticker = "ZZZZNOTAREALTICKER99999"
    fake_path = _redirect_target(fake_ticker)
    fake_segments = fake_path.split("/")
    # A fabricated ticker has no real series to resolve to — the redirect
    # must NOT produce the same markets/{series}/{ticker} three-segment
    # structure a real ticker does (it stays a bare markets/{ticker}).
    fake_looks_resolved = (
        len(fake_segments) >= 3 and fake_segments[0] == "markets"
        and fake_segments[1] != fake_ticker.lower()
    )
    print(f"  FAKE  {fake_ticker:<30} -> /{fake_path}  looks_resolved={fake_looks_resolved}")
    assert not fake_looks_resolved, (
        "Fabricated ticker unexpectedly resolved into a series/ticker structure — "
        "the distinguishing signal no longer holds and kalshi_market_url's "
        "confirmation should be re-examined"
    )
