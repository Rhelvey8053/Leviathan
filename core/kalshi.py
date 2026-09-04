import os
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

from kalshi import KalshiClient
from kalshi.auth import KalshiAuth
from kalshi.config import KalshiConfig
from kalshi.errors import KalshiAuthError, KalshiNotFoundError

load_dotenv()

DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
PROD_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# kalshi-sdk-migration-implementation: pinned in requirements.txt. _sdk_json()
# below reaches into KalshiClient._transport (a private attribute, not part of
# the SDK's public contract) so a future SDK upgrade can silently change or
# remove it -- the pin means that only happens on a deliberate version bump
# with re-verification, not an unattended `pip install -U`.
_clients: dict[str, KalshiClient] = {}


def _get_base_url(config: dict) -> str:
    env = config.get("environment", "demo")
    return PROD_BASE_URL if env == "prod" else DEMO_BASE_URL


def _load_auth() -> KalshiAuth:
    key_id = os.getenv("KALSHI_KEY_ID")
    if not key_id:
        raise ValueError("KALSHI_KEY_ID not set in environment")
    raw = os.getenv("KALSHI_PRIVATE_KEY", "")
    if not raw:
        raise ValueError("KALSHI_PRIVATE_KEY not set in environment")
    pem = raw.replace("\\n", "\n")
    if "-----BEGIN" not in pem:
        # Raw base64 body without PEM headers — wrap it
        pem = f"-----BEGIN RSA PRIVATE KEY-----\n{pem.strip()}\n-----END RSA PRIVATE KEY-----"
    return KalshiAuth.from_pem(key_id, pem)


def _get_client(config: dict) -> KalshiClient:
    """
    Returns a cached, authenticated KalshiClient for this config's
    environment (demo/prod), constructing it on first use. Caching is safe
    here (unlike computing auth headers per-request, which the old
    requests-based implementation did): KALSHI_KEY_ID/KALSHI_PRIVATE_KEY are
    loaded once via load_dotenv() at import time and never change for the
    life of a single `python main.py` run, and the SDK signs each request
    fresh at call time regardless (KalshiAuth.sign_request() reads a
    timestamp per call) -- only the parsed RSA key object and the httpx
    connection pool are reused, which is a real efficiency win over
    re-parsing the PEM on every single API call.
    """
    env = config.get("environment", "demo")
    client = _clients.get(env)
    if client is None:
        kconfig = KalshiConfig.production() if env == "prod" else KalshiConfig.demo()
        client = KalshiClient(auth=_load_auth(), config=kconfig)
        _clients[env] = client
    return client


def _sdk_json(config: dict, path: str, *, params: dict | None = None) -> dict:
    """
    Issues a GET request through kalshi-sdk's transport and returns the raw
    JSON dict — deliberately NOT going through the SDK's typed resource
    methods (client.markets.list(), client.portfolio.balance(), etc.), which
    parse responses into Pydantic models with renamed/retyped fields
    (kalshi-sdk-evaluation-2026-08 found e.g. our "no_ask_dollars" (string)
    becomes the model's "no_ask" (Decimal), across ~10 distinct response
    shapes). Every caller in this codebase (main.py, analysis/*, scripts/*,
    and their tests) expects the original dict-shaped/string-typed Kalshi API
    contract, so this goes through the same transport (RSA-PSS auth signing,
    exponential-backoff retry on transient errors, host validation, response
    size caps — all real hardening the old hand-rolled requests.get() calls
    never had) but stops short of the pydantic layer, making the returned
    JSON byte-identical to before. Verified via live dry-run diff against
    the old implementation for every function in this module — see
    BACKLOG.md, kalshi-sdk-migration-implementation.
    """
    client = _get_client(config)
    resp = client._transport.request("GET", path, params=params)
    return resp.json()


def authenticate(config: dict) -> dict:
    """
    Validates credentials by hitting /portfolio/balance.
    Fails loudly if auth is invalid — don't proceed with bad auth.
    """
    try:
        data = _sdk_json(config, "/portfolio/balance")
    except KalshiAuthError as e:
        raise RuntimeError(
            f"Kalshi authentication failed ({e.status_code}). {e}. "
            f"Environment: {config.get('environment', 'demo')}"
        ) from e
    print(f"  [kalshi] Authenticated. Balance: {data}")
    return data


def fetch_markets(config: dict, status: str = "open", limit: int = 200) -> list[dict]:
    """Returns open markets, paginating up to max_fetch from config."""
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

        data = _sdk_json(config, path, params=params)

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
    path = f"/markets/{ticker}"
    data = _sdk_json(config, path)
    return data.get("market", {})


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
    path     = "/markets/trades"
    lookback = limit or config.get("whales", {}).get("lookback_trades", 100)
    trades   = []
    cursor   = None
    while len(trades) < lookback:
        params = {"ticker": ticker, "limit": min(100, lookback - len(trades))}
        if cursor:
            params["cursor"] = cursor
        data = _sdk_json(config, path, params=params)
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
    path     = "/markets/trades"
    trades   = []
    cursor   = None
    while len(trades) < limit:
        params = {"limit": min(100, limit - len(trades))}
        if cursor:
            params["cursor"] = cursor
        data  = _sdk_json(config, path, params=params)
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
    path = "/events"
    max_fetch = config.get("markets", {}).get("max_events", 100)

    events = []
    cursor = None

    while len(events) < max_fetch:
        params = {"status": status, "limit": 200}
        if cursor:
            params["cursor"] = cursor

        data = _sdk_json(config, path, params=params)

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
    path = "/markets"
    data = _sdk_json(
        config, path,
        params={"status": "open", "event_ticker": event_ticker, "limit": 200},
    )
    return data.get("markets", [])


def fetch_event_detail(config: dict, event_ticker: str) -> dict:
    """
    Returns a single event's detail (series_ticker, category, etc.) via
    /events/{event_ticker} — used to backfill those two fields onto
    fetch_near_dated_markets()'s results, since the /markets object itself
    doesn't carry them (confirmed empirically) the way fetch_events()'s
    per-event loop already attaches them from the event object.
    """
    path = f"/events/{event_ticker}"
    data = _sdk_json(config, path)
    return data.get("event", {})


def attach_event_category_metadata(config: dict, markets: list[dict]) -> list[dict]:
    """
    Backfills series_ticker/category onto each market via one
    fetch_event_detail() call per UNIQUE event_ticker (many markets share
    one event_ticker, so this is far cheaper than one call per market).
    For any /markets-shaped result set that didn't come from fetch_events()
    (whose event objects already carry these fields) -- currently
    fetch_near_dated_markets() and main.py's fetch_markets() fallback path.
    """
    event_tickers = {m.get("event_ticker") for m in markets if m.get("event_ticker")}
    event_meta: dict[str, tuple[str, str]] = {}
    for et in event_tickers:
        try:
            ev = fetch_event_detail(config, et)
            event_meta[et] = (ev.get("series_ticker", ""), ev.get("category", ""))
        except Exception as _e:
            print(f"      [warn] fetch_event_detail({et}): {_e}")
            event_meta[et] = ("", "")
    for m in markets:
        series_ticker, category = event_meta.get(m.get("event_ticker"), ("", ""))
        m["series_ticker"] = series_ticker
        m["category"]      = category
    return markets


def fetch_near_dated_markets(
    config: dict, max_days: int = 14, target_count: int = 200, max_pages: int = 30,
) -> list[dict]:
    """
    Returns open markets closing within max_days, fetched directly via
    /markets?max_close_ts=... .

    Why this exists: fetch_events() (the events-catalog path main.py's
    primary market fetch and analysis/snapshot_markets.py both use)
    structurally never surfaces near-dated markets. The /events object
    carries no close_time field at all, and querying it with max_close_ts
    has no effect (confirmed empirically — it silently returns whatever
    events the default ordering picks, dominated by century-scale novelty
    markets like Mars colonization/next-Pope/NATO-Sec-Gen). Confirmed
    2026-08-01: of 2722 markets from a live events-catalog fetch, 0 closed
    within 30 days. resolve_first.py (built specifically to accelerate
    resolved-signal count toward the calibration gates) was consequently
    starved — 1 row logged in its entire history — despite running
    successfully every day, because its input snapshot never contained
    anything for it to select.

    /markets?max_close_ts=... DOES genuinely filter by close time
    (confirmed empirically), but /markets?status=open directly is ~98%
    dominated by the KXMVE multi-event parlay flood (confirmed: 1966 of
    2000 raw entries in one live sample) — the same flood fetch_events()
    already routes around for the long-horizon path via its own event-based
    fetch shape. Excluded here by ticker prefix.

    Queries in 1-day min_close_ts/max_close_ts sub-windows (hurricane-
    recalibration follow-up finding, 2026-08-02) rather than one flat
    max_close_ts query, because the flood's density is wildly uneven across
    the window, not evenly ~98% throughout: live-sampled at the 0-14 day
    horizon, day-buckets ranged from ~0% flood (8-9 days out: 1 flood / 199
    real) to ~99.5% flood (10-14 days out: 198-199 flood / 1-2 real, PER
    200-RESULT PAGE, not exhausted even at 200 pages / 40k markets sampled
    in that one bucket). A single unbounded max_close_ts=14d query returned
    1 real market total even at 200 pages, because within that flat query
    the flood apparently never runs out before the page cap does. Chunking
    by day bounds each day's own page budget independently, so one
    flood-saturated day can't starve every other day's real inventory the
    way a single shared budget did.

    max_pages is a hard cap on total HTTP calls across ALL day-chunks
    combined (same external contract as before chunking), split into a
    small per-chunk cap so a single bad day can't consume the whole budget.
    Stops overall once target_count non-flood markets are collected or the
    total page budget is exhausted, whichever comes first.

    series_ticker/category are backfilled via one fetch_event_detail() call
    per UNIQUE event_ticker in the batch (many near-dated markets — e.g.
    several prop bets on one baseball game — share a single event_ticker,
    so this is far cheaper than one call per market), matching the same
    two fields main.py's events-catalog loop already attaches from the
    event object it already has in hand.

    Per-day quota (kalshi-event-recency-window-misses-new-markets, 2026-09-04):
    the day-loop used to stop entirely once the GLOBAL target_count was
    reached, walking days in chronological order starting from today.
    Live-verified this let 1-2 abundant, low-flood early days (e.g. today,
    tomorrow) consume the whole budget before the loop ever reached a later
    day — confirmed against the NFL/NCAAF 2026 season: KXNFLGAME/
    KXNCAAFGAME game-winner markets, which happened to close on days 9-10 of
    the window, never appeared in a live call's output despite 0% overlap
    with fetch_events()'s output (i.e. no other path into all_markets ever
    surfaced them either). This isn't football-specific — ANY market family
    whose close dates land after an early abundant day was structurally
    unreachable, the same "recency crowds out newness" shape fetch_events()
    itself has, just keyed on day-order instead of last_updated_ts.

    Fix: each day is capped at a fair per-day quota
    (target_count // max_days, minimum 1) of "guaranteed" markets, and the
    day-loop always walks every day up to max_days (bounded only by
    max_pages, the pre-existing hard cost cap) instead of stopping once the
    global target is hit — so a market family closing on day 10 gets the
    same shot as one closing on day 0. Guaranteed picks from every day are
    combined first, so no single day's abundance can crowd another day out
    of the final list. Anything a day collects beyond its own quota (already
    fetched over the wire at zero extra cost — a single page is 200
    markets, far more than most days' quota) is kept as surplus and used to
    backfill the final list up to target_count if the guaranteed picks alone
    don't fill it, so this fix does not reduce total yield on the days that
    used to dominate — confirmed live 2026-09-04: same page count (38,
    under the existing 40-page config cap) and same final count (300) as
    before, but now with KXNFLGAME/KXNCAAFGAME markets included where
    previously there were zero, in any live run, ever.
    """
    path = "/markets"
    now = datetime.now(timezone.utc)
    exclude_prefixes = tuple(
        config.get("markets", {}).get("near_dated_exclude_prefixes", ["KXMVE"])
    )

    # Per-chunk page cap: keeps one flood-saturated day from consuming the
    # entire max_pages budget. 5 is generous relative to what live sampling
    # needed -- flood-light days surfaced real markets on page 1, and
    # flood-saturated days stayed ~100% flood well past page 5 too, so more
    # pages there wouldn't have helped.
    CHUNK_MAX_PAGES = 5

    # Fair per-day share of the final list -- see docstring. Floor of 1 so a
    # large max_days relative to target_count never zeroes every day out.
    per_day_target = max(target_count // max_days, 1)

    guaranteed: list[dict] = []
    surplus: list[dict] = []
    seen_tickers: set[str] = set()
    pages = 0
    day = 0
    while day < max_days and pages < max_pages:
        chunk_min_ts = int((now + timedelta(days=day)).timestamp())
        chunk_max_ts = int((now + timedelta(days=day + 1)).timestamp())
        day += 1

        day_bucket: list[dict] = []
        day_surplus: list[dict] = []
        cursor = None
        chunk_pages = 0
        while (chunk_pages < CHUNK_MAX_PAGES and pages < max_pages
               and len(day_bucket) < per_day_target):
            params = {
                "status": "open", "limit": 200,
                "min_close_ts": chunk_min_ts, "max_close_ts": chunk_max_ts,
            }
            if cursor:
                params["cursor"] = cursor
            data = _sdk_json(config, path, params=params)
            page = data.get("markets", [])
            pages += 1
            chunk_pages += 1
            if not page:
                break
            for m in page:
                t = m.get("ticker", "")
                if t and t not in seen_tickers and not t.startswith(exclude_prefixes):
                    seen_tickers.add(t)
                    if len(day_bucket) < per_day_target:
                        day_bucket.append(m)
                    else:
                        day_surplus.append(m)
            cursor = data.get("cursor")
            if not cursor:
                break
        guaranteed.extend(day_bucket)
        surplus.extend(day_surplus)

    collected = guaranteed
    if len(collected) < target_count:
        collected = collected + surplus[: target_count - len(collected)]
    collected = collected[:target_count]
    return attach_event_category_metadata(config, collected)


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
    path = "/events"

    events = []
    cursor = None
    while len(events) < max_fetch:
        params = {"status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = _sdk_json(config, path, params=params)

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
    path = "/markets"
    data = _sdk_json(
        config, path,
        params={"status": "settled", "event_ticker": event_ticker, "limit": 200},
    )
    return data.get("markets", [])


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
    path = f"/series/{series_ticker}/markets/{ticker}/candlesticks"
    try:
        data = _sdk_json(
            config, path,
            params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )
    except KalshiNotFoundError:
        return []
    return data.get("candlesticks", [])


def fetch_market_with_retry(config: dict, ticker: str) -> dict:
    """
    Fetch a single market by ticker, retrying once after a 2s delay if the title
    is missing or equals the ticker (title-scraping-fix guard).
    Returns the market dict from the API response.
    """
    path = f"/markets/{ticker}"

    def _fetch_once():
        data = _sdk_json(config, path)
        return data.get("market", {})

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
    path = f"/markets/{ticker}/orderbook"
    return _sdk_json(config, path)


def fetch_fills(config: dict) -> list[dict]:
    """
    GET /portfolio/fills — returns all real trade fills, paginated via cursor.
    Note: Kalshi only exposes recent fills; older history may not be available.
    """
    path = "/portfolio/fills"
    fills = []
    cursor = None

    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        data = _sdk_json(config, path, params=params)
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
    path = "/portfolio/positions"
    data = _sdk_json(config, path, params={"limit": 100})
    positions = data.get("market_positions", data.get("positions", []))
    if not positions:
        print("  [kalshi] fetch_positions: no positions returned")
    return positions


def kalshi_market_url(series_ticker: str | None, event_ticker: str | None = None) -> str | None:
    """
    Returns a kalshi.com market-page URL for a given (series_ticker,
    event_ticker) pair, or None if either is missing/empty.

    CONFIRMED PATTERN (2026-07-23, superseding the 2026-07-22 "no pattern
    confirmed" finding — see docs/PROGRESS_ARCHIVE.md for the full trail):

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
    alongside event_ticker (see docs/PROGRESS_ARCHIVE.md). Rows logged before this
    existed have series_ticker='' and fall back to bare ticker text.

    Never emits a guessed URL when a required field is missing, and never
    reintroduces the confirmed-404 kalshi.com/markets/{market_ticker}
    (bare ticker, no series) form. Callers must treat a None return as
    "render the bare ticker, no href" — never href="".
    """
    if not series_ticker or not event_ticker:
        return None
    return f"https://kalshi.com/markets/{series_ticker.lower()}/{event_ticker.lower()}"
