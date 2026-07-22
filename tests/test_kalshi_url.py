"""
tests/test_kalshi_url.py — Tests for core.kalshi.kalshi_market_url.

Offline unit tests assert the helper's current (no-confirmed-pattern)
behavior: it always returns None/falsy, for any input, so callers never
render a guessed or unverified link. See core/kalshi.py's docstring and
docs/PROGRESS.md for the full investigation.

The single @pytest.mark.network test performs the actual live resolve-
check this goal required: it hits kalshi.com for >= 2 real current
markets AND a fabricated ticker, and shows they are NOT distinguishable
by HTTP status/redirect alone — the concrete finding backing why
kalshi_market_url returns None rather than a constructed URL.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.kalshi import kalshi_market_url


# ─── offline: helper behavior ─────────────────────────────────────────────────

def test_kalshi_market_url_returns_none_for_known_event_ticker():
    """No confirmed URL pattern exists — even a real event_ticker yields None."""
    assert kalshi_market_url("KXCABLEAVE-26MAY22-26AUG") is None


def test_kalshi_market_url_returns_falsy_for_empty_string():
    assert not kalshi_market_url("")


def test_kalshi_market_url_returns_falsy_for_none():
    assert not kalshi_market_url(None)


def test_kalshi_market_url_never_returns_the_confirmed_404_form():
    """Regression guard: must never reintroduce kalshi.com/markets/{market_ticker}."""
    result = kalshi_market_url("KXSOMEEVENT-26AUG01")
    assert result is None
    # Even if a future implementation changes this, it must never be the
    # bare market-ticker form already confirmed to 404.
    assert result != "https://kalshi.com/markets/KXSOMEEVENT-26AUG01"


def test_signal_with_missing_event_ticker_renders_bare_ticker_no_href():
    """
    Simulates the report-rendering contract PART E requires: a caller gets
    a falsy url back and must render plain ticker text, never href="".
    """
    ticker = "KXNOEVENTTICKER-26JAN01"
    url = kalshi_market_url(None)
    rendered = f'<a href="{url}">{ticker}</a>' if url else ticker
    assert rendered == ticker
    assert "href=" not in rendered


# ─── live resolve-check (the actual PART C/D investigation) ──────────────────

@pytest.mark.network
def test_live_resolve_check_cannot_distinguish_real_from_fake_market():
    """
    Fetches >= 2 real, currently-open Kalshi events and constructs
    https://kalshi.com/markets/{event_ticker} for each, alongside one
    fabricated ticker. Asserts the real candidates return 200 with no
    redirect to the homepage (the narrow, literal proof bar) — AND shows
    the fabricated ticker behaves identically, which is why this pattern
    is NOT treated as confirmed by kalshi_market_url.

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

    real_tickers = [e["event_ticker"] for e in events[:2] if e.get("event_ticker")]
    assert len(real_tickers) >= 2, "Need at least 2 real event tickers to test against"

    fake_ticker = "ZZZZNOTAREALTICKER99999"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    homepage_paths = {"", "/", "/markets"}

    def _check(ticker: str) -> dict:
        url = f"https://kalshi.com/markets/{ticker}"
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        final_path = resp.url.replace("https://kalshi.com", "").rstrip("/")
        return {
            "ticker": ticker,
            "url": url,
            "status": resp.status_code,
            "final_url": resp.url,
            "redirected_to_homepage": final_path in homepage_paths,
            "body_len": len(resp.text),
        }

    real_results = [_check(t) for t in real_tickers]
    fake_result = _check(fake_ticker)

    print("\n=== Live Kalshi URL resolve-check ===")
    for r in real_results:
        print(f"  REAL  {r['ticker']:<30} status={r['status']} "
              f"final={r['final_url']} redirected_home={r['redirected_to_homepage']} "
              f"body_len={r['body_len']}")
    print(f"  FAKE  {fake_result['ticker']:<30} status={fake_result['status']} "
          f"final={fake_result['final_url']} redirected_home={fake_result['redirected_to_homepage']} "
          f"body_len={fake_result['body_len']}")

    # The literal, narrow proof bar: real markets resolve 200, no homepage redirect.
    for r in real_results:
        assert r["status"] == 200
        assert not r["redirected_to_homepage"]

    # The actual finding: a FABRICATED ticker passes the identical narrow bar,
    # so status/redirect alone cannot confirm page identity — this is why
    # kalshi_market_url withholds construction rather than shipping this URL.
    assert fake_result["status"] == 200
    assert not fake_result["redirected_to_homepage"]
    body_lens = [r["body_len"] for r in real_results] + [fake_result["body_len"]]
    print(f"  Body length spread real-vs-fake: {max(body_lens) - min(body_lens)} bytes "
          f"(near-identical HTML regardless of ticker validity)")
