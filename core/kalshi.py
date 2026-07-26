import base64
import os
import time
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from dotenv import load_dotenv

load_dotenv()

DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
PROD_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


def _get_base_url(config: dict) -> str:
    env = config.get("environment", "demo")
    return PROD_BASE_URL if env == "prod" else DEMO_BASE_URL


def _load_private_key():
    raw = os.getenv("KALSHI_PRIVATE_KEY", "")
    if not raw:
        raise ValueError("KALSHI_PRIVATE_KEY not set in environment")
    pem = raw.replace("\\n", "\n")
    if "-----BEGIN" not in pem:
        # Raw base64 body without PEM headers — wrap it
        pem = f"-----BEGIN RSA PRIVATE KEY-----\n{pem.strip()}\n-----END RSA PRIVATE KEY-----"
    return serialization.load_pem_private_key(pem.encode(), password=None)


def _auth_headers(method: str, path: str) -> dict:
    """
    Generates Kalshi RSA signing auth headers.
    path: full API path e.g. '/trade-api/v2/portfolio/balance'
    """
    key_id = os.getenv("KALSHI_KEY_ID")
    if not key_id:
        raise ValueError("KALSHI_KEY_ID not set in environment")

    ts_ms = str(int(time.time() * 1000))
    private_key = _load_private_key()

    msg = f"{ts_ms}{method.upper()}{path}".encode("utf-8")
    pss = asym_padding.PSS(mgf=asym_padding.MGF1(hashes.SHA256()), salt_length=asym_padding.PSS.MAX_LENGTH)
    sig = private_key.sign(msg, pss, hashes.SHA256())
    sig_b64 = base64.b64encode(sig).decode()

    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        "KALSHI-ACCESS-SIGNATURE": sig_b64,
        "Content-Type": "application/json",
    }


def _vpath(path: str) -> str:
    """Returns versioned path for signature: /trade-api/v2/..."""
    return f"/trade-api/v2{path}"


def authenticate(config: dict) -> dict:
    """
    Validates credentials by hitting /portfolio/balance.
    Fails loudly if auth is invalid — don't proceed with bad auth.
    """
    base_url = _get_base_url(config)
    path = "/portfolio/balance"
    resp = requests.get(
        f"{base_url}{path}",
        headers=_auth_headers("GET", _vpath(path)),
        timeout=10,
    )
    if resp.status_code == 401:
        raise RuntimeError(
            f"Kalshi authentication failed (401). Body: {resp.text}. "
            f"Environment: {config.get('environment', 'demo')}"
        )
    resp.raise_for_status()
    data = resp.json()
    print(f"  [kalshi] Authenticated. Balance: {data}")
    return data


def fetch_markets(config: dict, status: str = "open", limit: int = 200) -> list[dict]:
    """Returns open markets, paginating up to max_fetch from config."""
    base_url = _get_base_url(config)
    max_fetch = config.get("markets", {}).get("max_fetch", limit)
    categories = config.get("markets", {}).get("categories", [])
    path = "/markets"

    markets = []
    cursor = None
    page_size = 200

    while len(markets) < max_fetch:
        params = {"status": status, "limit": page_size}
        if cursor:
            params["cursor"] = cursor
        if categories:
            params["category"] = categories[0]

        resp = requests.get(
            f"{base_url}{path}",
            headers=_auth_headers("GET", _vpath(path)),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        page = data.get("markets", [])
        if not page:
            break

        markets.extend(page)
        cursor = data.get("cursor")
        if not cursor:
            break

    return markets[:max_fetch]


def fetch_market(config: dict, ticker: str) -> dict:
    """Returns single market with full detail including order book."""
    base_url = _get_base_url(config)
    path = f"/markets/{ticker}"
    resp = requests.get(
        f"{base_url}{path}",
        headers=_auth_headers("GET", _vpath(path)),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("market", {})


def fetch_trades(config: dict, ticker: str, limit: int = 0) -> list[dict]:
    """
    Returns recent trades for a single market, newest first.

    /markets/{ticker}/trades does not exist on Kalshi's API (confirmed
    empirically) — there is exactly one trades endpoint, /markets/trades,
    filtered to one market via a `ticker` query parameter (the same path
    fetch_recent_trades() already uses for the unfiltered global feed).
    This function silently 404'd on every call before this fix, so whale
    detection always saw zero trades for every market.

    Each trade: {ticker, count_fp, yes_price_dollars, no_price_dollars,
                 taker_side, taker_outcome_side, taker_book_side,
                 created_time, is_block_trade, trade_id}
    """
    base_url = _get_base_url(config)
    path     = "/markets/trades"
    lookback = limit or config.get("whales", {}).get("lookback_trades", 100)
    trades   = []
    cursor   = None
    while len(trades) < lookback:
        params = {"ticker": ticker, "limit": min(100, lookback - len(trades))}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            f"{base_url}{path}",
            headers=_auth_headers("GET", _vpath(path)),
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        page = data.get("trades", [])
        if not page:
            break
        trades.extend(page)
        cursor = data.get("cursor")
        if not cursor:
            break
    return trades[:lookback]  # defensive cap in case a page over-delivers past the requested limit


def fetch_recent_trades(config: dict, limit: int = 500) -> list[dict]:
    """
    GET /markets/trades — global recent trade feed, not per-market.
    Returns up to `limit` trades across all markets, newest first.
    Each trade: {ticker, count_fp, yes_price_dollars, taker_side,
                 created_time, is_block_trade, trade_id}
    """
    base_url = _get_base_url(config)
    path     = "/markets/trades"
    trades   = []
    cursor   = None
    while len(trades) < limit:
        params = {"limit": min(100, limit - len(trades))}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            f"{base_url}{path}",
            headers=_auth_headers("GET", _vpath(path)),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data  = resp.json()
        page  = data.get("trades", [])
        if not page:
            break
        trades.extend(page)
        cursor = data.get("cursor")
        if not cursor:
            break
    return trades


def fetch_events(config: dict, status: str = "open") -> list[dict]:
    """
    Returns open events. Events are the parent objects of standard binary
    markets — fetching via events lets us skip past the MVE parlay flood
    that dominates the default /markets ordering.
    Sorted by last_updated_ts desc as a proxy for recent activity.
    """
    base_url = _get_base_url(config)
    path = "/events"
    max_fetch = config.get("markets", {}).get("max_events", 100)

    events = []
    cursor = None

    while len(events) < max_fetch:
        params = {"status": status, "limit": 200}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(
            f"{base_url}{path}",
            headers=_auth_headers("GET", _vpath(path)),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        page = data.get("events", [])
        if not page:
            break
        events.extend(page)
        cursor = data.get("cursor")
        if not cursor:
            break

    # Sort by last_updated_ts desc — recently active events first
    events.sort(key=lambda e: e.get("last_updated_ts", ""), reverse=True)
    return events[:max_fetch]


def fetch_event_markets(config: dict, event_ticker: str) -> list[dict]:
    """
    Returns open markets for a specific event ticker.
    Uses /markets?event_ticker= filter — events do not embed markets directly.
    """
    base_url = _get_base_url(config)
    path = "/markets"
    resp = requests.get(
        f"{base_url}{path}",
        headers=_auth_headers("GET", _vpath(path)),
        params={"status": "open", "event_ticker": event_ticker, "limit": 200},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("markets", [])


def fetch_settled_events(config: dict, max_fetch: int = 2000) -> list[dict]:
    """
    Returns settled (resolved) events (replay-settled-fetcher). Querying
    /events?status=settled excludes the KXMVE parlay flood entirely —
    confirmed empirically: 0 of 400 sampled settled events were KXMVE, vs
    999 of 1000 when querying /markets?status=settled directly (the same
    flood fetch_events()'s "open" path already routes around).

    max_fetch is independent of config.markets.max_events — that value
    bounds the live per-run scan budget; this is for backtesting corpus
    depth, so callers pulling historical data should pass something much
    larger to reach further back than the local snapshot archive
    (data/snapshots, earliest file 2026-06-16 — confirmed by directory
    listing 2026-07-25; an earlier "2026-07-08" floor claim in this
    codebase's docs was never actually checked against the files on disk
    and was off by three weeks).
    """
    base_url = _get_base_url(config)
    path = "/events"

    events = []
    cursor = None
    while len(events) < max_fetch:
        params = {"status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            f"{base_url}{path}",
            headers=_auth_headers("GET", _vpath(path)),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        page = data.get("events", [])
        if not page:
            break
        events.extend(page)
        cursor = data.get("cursor")
        if not cursor:
            break

    return events[:max_fetch]


def fetch_settled_event_markets(config: dict, event_ticker: str) -> list[dict]:
    """
    Returns settled markets for a specific event ticker — the
    settled-market counterpart to fetch_event_markets(), which is
    hardcoded to status='open' for the live scan path and is left
    unchanged so existing callers keep their current behavior.
    """
    base_url = _get_base_url(config)
    path = "/markets"
    resp = requests.get(
        f"{base_url}{path}",
        headers=_auth_headers("GET", _vpath(path)),
        params={"status": "settled", "event_ticker": event_ticker, "limit": 200},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("markets", [])


def fetch_market_candlesticks(
    config: dict, series_ticker: str, ticker: str,
    start_ts: int, end_ts: int, period_interval: int = 1440,
) -> list[dict]:
    """
    Returns Kalshi candlestick history for a market (replay-asof-reconstruction;
    also main.py's live price-trend text, migrated here from the now-deleted
    fetch_market_history(), which called /markets/{ticker}/history — an
    endpoint that does not exist on Kalshi's API. Confirmed empirically:
    every ticker tried, including high-volume open markets, returned a
    plain-text "404 page not found", not a JSON 404, and the old function's
    `if resp.status_code == 404: return []` silently swallowed that for
    every call site, live pipeline included, so it never actually returned
    data. This function uses the real endpoint:
    /series/{series_ticker}/markets/{ticker}/candlesticks, confirmed
    working and returning real OHLC + yes_bid/yes_ask + volume/open_interest
    per period.

    period_interval is in minutes; Kalshi only accepts 1, 60, or 1440
    (confirmed empirically — other values reject with a validation error).
    period_interval=1 additionally caps the requested range to 5000 minutes
    (~3.5 days) per call.

    series_ticker must come from the event/settled_markets record, never
    guessed — it does not live on the raw market object (same rule as
    fetch_settled_events()'s event/market split above).
    """
    base_url = _get_base_url(config)
    path = f"/series/{series_ticker}/markets/{ticker}/candlesticks"
    resp = requests.get(
        f"{base_url}{path}",
        headers=_auth_headers("GET", _vpath(path)),
        params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        timeout=15,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return resp.json().get("candlesticks", [])


def fetch_market_with_retry(config: dict, ticker: str) -> dict:
    """
    Fetch a single market by ticker, retrying once after a 2s delay if the title
    is missing or equals the ticker (title-scraping-fix guard).
    Returns the market dict from the API response.
    """
    base_url = _get_base_url(config)
    path = f"/markets/{ticker}"

    def _fetch_once():
        resp = requests.get(
            f"{base_url}{path}",
            headers=_auth_headers("GET", _vpath(path)),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("market", {})

    market = _fetch_once()
    title = market.get("title") or ""
    if not title or title == ticker:
        time.sleep(2)
        market = _fetch_once()
    return market


def fetch_orderbook(config: dict, ticker: str) -> dict:
    """
    Returns the full order book for a market — all bid/ask price levels.
    Used for order book imbalance signal (deeper than just best bid/ask).

    Real response shape (confirmed live 2026-07-25):
    {"orderbook_fp": {"yes_dollars": [[price, size], ...], "no_dollars": [...]}}
    — passed through as-is; core.scanner.compute_orderbook_signal() is the
    consumer that reads orderbook_fp specifically (a prior version of that
    function assumed a nonexistent "orderbook" envelope key and silently
    computed zero depth for every market).
    """
    base_url = _get_base_url(config)
    path     = f"/markets/{ticker}/orderbook"
    resp = requests.get(
        f"{base_url}{path}",
        headers=_auth_headers("GET", _vpath(path)),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_fills(config: dict) -> list[dict]:
    """
    GET /portfolio/fills — returns all real trade fills, paginated via cursor.
    Note: Kalshi only exposes recent fills; older history may not be available.
    """
    base_url = _get_base_url(config)
    path = "/portfolio/fills"
    fills = []
    cursor = None

    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            f"{base_url}{path}",
            headers=_auth_headers("GET", _vpath(path)),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        page = data.get("fills", [])
        if not page:
            break
        fills.extend(page)
        cursor = data.get("cursor")
        if not cursor:
            break

    if not fills:
        print("  [kalshi] fetch_fills: no fills returned (empty portfolio or historical cutoff)")
    return fills


def fetch_positions(config: dict) -> list[dict]:
    """
    GET /portfolio/positions — returns current open positions.
    """
    base_url = _get_base_url(config)
    path = "/portfolio/positions"
    resp = requests.get(
        f"{base_url}{path}",
        headers=_auth_headers("GET", _vpath(path)),
        params={"limit": 100},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    positions = data.get("market_positions", data.get("positions", []))
    if not positions:
        print("  [kalshi] fetch_positions: no positions returned")
    return positions


def kalshi_market_url(series_ticker: str | None, event_ticker: str | None = None) -> str | None:
    """
    Returns a kalshi.com market-page URL for a given (series_ticker,
    event_ticker) pair, or None if either is missing/empty.

    CONFIRMED PATTERN (2026-07-23, superseding the 2026-07-22 "no pattern
    confirmed" finding — see docs/PROGRESS.md for the full trail):

        https://kalshi.com/markets/{series_ticker.lower()}/{event_ticker.lower()}

    The 2026-07-22 investigation correctly found that HTTP status/redirect
    checks against kalshi.com/markets/{ticker} are meaningless — it's a
    client-rendered Next.js catch-all route (X-Matched-Path:
    /markets/[...slug]) that returns 200 for ANY path, real or fabricated.
    That blocker still holds; this is not a retest of the same method.

    What changed: Kalshi's OWN infrastructure, not a guess of ours, now
    confirms this exact two-segment shape three independent ways:
      1. Kalshi's own AGENTS.md (https://kalshi.com/AGENTS.md, published
         for AI agents citing markets) documents
         "https://kalshi.com/markets/<series-ticker>/<event-ticker>".
      2. Kalshi's own sitemap-markets.xml lists real, indexed markets at
         this same shape (with an optional cosmetic title-slug inserted
         between the two tickers, which the site's own /events/ redirect
         omits — see point 3 — so it is not required).
      3. Kalshi's own server issues a genuine 308 redirect chain (not
         client-side routing) from /events/{event_ticker} to exactly
         /markets/{series_ticker}/{event_ticker} for real tickers —
         verified against KXISRNORMCOUNT-27DEC31 and KXCABLEAVE-26MAY22.
         Tested against a fabricated ticker for contrast: the redirect
         does not resolve it into a series/ticker structure at all,
         confirming this is a real server-side lookup, not path rewriting.

    Known gap: kalshi.com/sitemap-markets.xml (Kalshi's own crawled index)
    has near-zero coverage of the niche, lower-liquidity markets this
    project actually tracks (0/14 tested from live signals), so it can't
    serve as a live per-ticker verification lookup — this function trusts
    the confirmed FORMAT (backed by points 1-3 above) rather than
    confirming each individual ticker resolves, which remains genuinely
    unverifiable for any single market via HTTP due to the client-render
    issue in the 2026-07-22 finding.

    series_ticker is captured in main.py from the EVENT object (event
    objects have it; raw market objects do not) and threaded through
    alongside event_ticker (see docs/PROGRESS.md). Rows logged before this
    existed have series_ticker='' and fall back to bare ticker text.

    Never emits a guessed URL when a required field is missing, and never
    reintroduces the confirmed-404 kalshi.com/markets/{market_ticker}
    (bare ticker, no series) form. Callers must treat a None return as
    "render the bare ticker, no href" — never href="".
    """
    if not series_ticker or not event_ticker:
        return None
    return f"https://kalshi.com/markets/{series_ticker.lower()}/{event_ticker.lower()}"
