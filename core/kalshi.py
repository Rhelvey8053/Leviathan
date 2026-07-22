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
    Returns recent trades for a market.
    Each trade: {price, count, size, created_time, taker_side}
    """
    base_url = _get_base_url(config)
    path = f"/markets/{ticker}/trades"
    lookback = limit or config.get("whales", {}).get("lookback_trades", 100)
    resp = requests.get(
        f"{base_url}{path}",
        headers=_auth_headers("GET", _vpath(path)),
        params={"limit": lookback},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("trades", [])


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
    """
    base_url = _get_base_url(config)
    path     = f"/markets/{ticker}/orderbook"
    resp = requests.get(
        f"{base_url}{path}",
        headers=_auth_headers("GET", _vpath(path)),
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("orderbook", data)


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


def fetch_market_history(config: dict, ticker: str, period_seconds: int = 86400) -> list[dict]:
    """Returns price history for the last N seconds."""
    base_url = _get_base_url(config)
    path = f"/markets/{ticker}/history"
    resp = requests.get(
        f"{base_url}{path}",
        headers=_auth_headers("GET", _vpath(path)),
        params={"period_seconds": period_seconds},
        timeout=10,
    )
    if resp.status_code == 404:
        return []  # market exists but has no history yet (new/niche market types)
    resp.raise_for_status()
    return resp.json().get("history", [])


def kalshi_market_url(event_ticker: str | None) -> str | None:
    """
    Returns a kalshi.com market-page URL for a given event_ticker, or None
    if no confirmed-working URL pattern exists.

    STATUS: NO CONFIRMED PATTERN — always returns None right now.

    kalshi.com's market pages are a client-rendered Next.js app behind a
    catch-all route (X-Matched-Path: /markets/[...slug]). Empirically
    verified (2026-07-22) across 3 real event tickers plus a fabricated
    one: https://kalshi.com/markets/{event_ticker} returns HTTP 200 with
    no redirect for EVERY input tried, including the fake ticker —
    identical status code, identical response headers, and near-identical
    (~148KB) HTML body with no server-rendered market title or content for
    either the real or fake case. The distinguishing "not found" state is
    rendered client-side by JS after the page loads, which a plain HTTP
    request cannot see. This means HTTP status/redirect-based resolve-
    testing (the required proof method — see docs/PROGRESS.md) cannot
    distinguish a real market page from a fabricated one, so no pattern is
    considered confirmed. Mirrors sources/accounts.py's POLY_URL pattern
    in spirit (a single URL-builder for one platform) but intentionally
    withholds construction until a verifiable pattern exists — see
    docs/PROGRESS.md for the full investigation and next-step options
    (most likely: capture a slug at scan time, as accounts.py already does
    for Polymarket via eventSlug, if Kalshi's API ever exposes one).

    Never emits a guessed URL and never reintroduces the confirmed-404
    kalshi.com/markets/{market_ticker} form. Callers must treat a None
    return as "render the bare ticker, no href" — never href="".
    """
    return None
