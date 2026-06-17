from datetime import datetime, timezone, timedelta

# Time horizon buckets: (label, min_days_inclusive, max_days_exclusive)
BUCKETS = [
    ("INTRADAY",  0,   1),
    ("WEEKLY",    1,   7),
    ("MONTHLY",   7,   30),
    ("QUARTERLY", 30,  90),
    ("LONG",      90,  366),
]

BUCKET_PRIORITY = {b[0]: i for i, b in enumerate(BUCKETS)}

# Watchlist trader set — injected by main.py at startup for priority scoring.
# Markets where a top Polymarket trader holds a position get a boost.
_WATCHLIST_TICKERS: set[str] = set()


def classify_time_horizon(close_time: datetime, now: datetime) -> str:
    """Returns the time bucket label for a market based on days until close."""
    days = (close_time - now).total_seconds() / 86400
    for label, lo, hi in BUCKETS:
        if lo <= days < hi:
            return label
    return "LONG"


def filter_markets(markets: list[dict], config: dict) -> list[dict]:
    """
    Removes markets likely already efficiently priced before any scoring.
    Filters out markets that match ANY of the following:
      1. Volume > max_volume_filter (efficiently priced by crowd)
      2. Volume < bucket min_volume
      3. Open interest < min_open_interest (ghost markets, no real participants)
      4. Title contains efficient market keyword
      5. Closing outside [min_days_to_close, max_days_to_close]
      6. Mid price outside [min_market_price, max_market_price]
    """
    cfg          = config.get("markets", {})
    global_min_vol  = cfg.get("min_volume", 500)
    max_vol         = cfg.get("max_volume_filter", 75000)
    min_days        = cfg.get("min_days_to_close", 0)
    max_days        = cfg.get("max_days_to_close", 180)
    min_price       = cfg.get("min_market_price", 0.05)
    max_price       = cfg.get("max_market_price", 0.95)
    min_oi          = cfg.get("min_open_interest", 0)
    bucket_vol      = cfg.get("bucket_min_volume", {})
    keywords        = [k.lower() for k in cfg.get("efficient_market_keywords", [])]

    now       = datetime.now(timezone.utc)
    min_close = now + timedelta(days=min_days)
    max_close = now + timedelta(days=max_days)

    filtered = []
    for m in markets:
        volume = float(m.get("volume_fp") or m.get("volume") or 0)

        if volume > max_vol:
            continue

        # Open interest floor — exclude ghost markets with no active participants
        if min_oi > 0:
            oi = float(m.get("open_interest_fp") or m.get("open_interest") or 0)
            if oi < min_oi:
                continue

        # Price bounds — exclude near-certain and tail-probability contracts
        yes_bid = float(m.get("yes_bid_dollars") or m.get("yes_bid") or 0)
        yes_ask = float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0)
        mid = (yes_bid + yes_ask) / 2 if (yes_bid + yes_ask) > 0 else None
        if mid is not None and not (min_price <= mid <= max_price):
            continue

        # Efficient market keyword check
        title = (m.get("title") or "").lower()
        if any(kw in title for kw in keywords):
            continue

        # Close time bounds
        close_time_str = m.get("close_time") or m.get("expiration_time")
        if not close_time_str:
            continue
        try:
            close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if close_time < min_close or close_time > max_close:
            continue

        # Per-bucket volume minimum (shorter horizons tolerate lower volume)
        bucket    = classify_time_horizon(close_time, now)
        min_vol   = bucket_vol.get(bucket, global_min_vol)
        if volume < min_vol:
            continue

        m["time_horizon"] = bucket
        filtered.append(m)

    return filtered


def dedup_by_event(markets: list[dict]) -> list[dict]:
    """
    When multiple markets share the same event_ticker, keep only the one with
    the highest volume. Prevents the same underlying event (e.g. 10 Prison Break
    expiry tickers) from consuming the entire Claude scoring budget.
    """
    by_event: dict[str, dict] = {}
    no_event: list[dict]      = []

    for m in markets:
        ev = m.get("event_ticker", "").strip()
        if not ev:
            no_event.append(m)
            continue
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
        existing_vol = float(
            by_event[ev].get("volume_fp") or by_event[ev].get("volume") or 0
        ) if ev in by_event else -1
        if vol > existing_vol:
            by_event[ev] = m

    return list(by_event.values()) + no_event


def estimate_base_rate(market: dict) -> float | None:
    """
    Simple heuristic pass before calling Claude (saves tokens).
    Returns a float 0.0–1.0 if a known signal applies, else None.
    scorer.py handles None markets with the full Claude call.
    """
    title = (market.get("title") or "").lower()

    # Binary yes/no events with known rough base rates.
    # Order matters — more specific patterns should come first.
    heuristics = [
        # Sports — outcomes for individual games lean slight favourite
        (["win the world series", "win world series"], 0.50),
        (["win the championship", "win the nba", "win the nfl", "win the cup",
          "win the world cup", "win the fifa", "world cup winner",
          "world series winner", "champions league", "stanley cup"], 0.50),
        (["win the super bowl", "super bowl winner"], 0.50),
        (["win the game", "win on", "win their next"], 0.52),
        # Elections — incumbents have modest advantage
        (["win the election", "win election", "wins the election",
          "win the primary", "win the runoff"], 0.52),
        (["win the presidency", "win the white house"], 0.50),
        (["win the senate race", "win the house race", "win the gubernatorial"], 0.52),
        # Weather
        (["will it rain", "chance of rain", "precipitation"], 0.40),
        # Price / market levels — mean-reversion roughly 50/50 near current levels
        (["reach $", "hits $", "exceed $", "above $",
          "surpass $", "cross $", "break $"], 0.35),
        (["below $", "under $", "fall below", "drop below",
          "dip below", "dip to $"], 0.35),
        # Corporate events — low base rate, most announcements don't complete
        (["ipo by", "ipo before", "initial public offering"], 0.30),
        (["merger", "acquisition", "acquired by", "take private",
          "buyout", "takeover"], 0.35),
        (["bankruptcy", "file for bankruptcy", "goes bankrupt"], 0.15),
        # Macroeconomic — cuts/hikes depend on market pricing already
        (["rate cut", "rate hike", "interest rate cut", "interest rate hike"], 0.50),
        (["recession", "in recession", "enters recession"], 0.25),
        (["default", "debt default", "sovereign default"], 0.10),
        # Geopolitical — low base rate for dramatic events
        (["declare war", "invade", "military strike", "launch attack"], 0.15),
        (["peace deal", "ceasefire", "peace agreement", "armistice"], 0.25),
        (["coup", "overthrow", "regime change"], 0.10),
        # Media / entertainment — very low: release dates often slip
        (["release", "released", "premieres", "premiere by",
          "season", "movie", "film", "show"], 0.25),
        # Technology
        (["fda approval", "fda approves", "fda cleared"], 0.40),
        (["launch", "launches", "launched by", "launches by"], 0.35),
        # Criminal / legal — conviction base rates are moderate
        (["convicted", "found guilty", "indicted", "charged with"], 0.40),
        (["impeach", "impeachment", "removed from office"], 0.15),
        # Generic sports/competition catch-all — must come LAST
        # " win " (with spaces) catches "Will X win [any competition]?"
        ([" win "], 0.52),
    ]
    for signals, rate in heuristics:
        if any(s in title for s in signals):
            return rate

    return None


def tag_watchlist_overlap(markets: list[dict], watchlist_tickers: set[str]) -> list[dict]:
    """
    Mark markets that overlap with smart money watchlist positions.
    Sets m['watchlist_signal'] = True on any market whose Kalshi ticker appears
    in the pre-built set of cross-referenced tickers.
    """
    for m in markets:
        m["watchlist_signal"] = m.get("ticker", "") in watchlist_tickers
    return markets


def compute_spread_signal(yes_bid: float, yes_ask: float, mid: float) -> dict:
    """
    Bid/ask spread as % of mid price.
    Wide spread (>5%) = market maker uncertainty = potential mispricing.
    This is context for Claude, not a standalone flag trigger.
    """
    if mid <= 0 or yes_bid <= 0 or yes_ask <= 0:
        return {"spread_pct": None, "spread_wide": False}
    spread_pct = (yes_ask - yes_bid) / mid
    return {"spread_pct": round(spread_pct, 4), "spread_wide": spread_pct > 0.05}


def compute_drift_signal(
    mid: float,
    market: dict,
    drift_min_abs: float = 0.0,
    drift_min_pct: float = 0.05,
) -> dict:
    """
    Drift between current order-book mid and the last traded price.
    Requires BOTH a minimum absolute move AND a minimum percentage move to flag,
    preventing tiny cent-level moves at very low prices from triggering on pct alone.
    Thresholds come from config (markets.drift_min_abs / markets.drift_min_pct).
    """
    last = float(market.get("last_price_dollars") or 0)
    if not last or mid is None:
        return {"price_drift": None, "price_drift_abs": None, "drift_flag": False}
    abs_drift = abs(mid - last)
    pct_drift = abs_drift / last
    drift_flag = abs_drift > drift_min_abs and pct_drift > drift_min_pct
    return {
        "price_drift":     round((mid - last) / last, 4),
        "price_drift_abs": round(abs_drift, 4),
        "drift_flag":      drift_flag,
    }


def compute_whale_reversal(market: dict, whale: dict | None) -> bool:
    """
    True when whale trade direction opposes the recent price trend.
    Informed money trading against momentum = strong contrarian signal.
    Uses previous_price_dollars vs current mid for the trend direction.
    """
    if not whale or not whale.get("whale_detected"):
        return False
    whale_dir = whale.get("whale_direction")
    if not whale_dir:
        return False

    yes_bid = float(market.get("yes_bid_dollars") or 0)
    yes_ask = float(market.get("yes_ask_dollars") or 0)
    prev = float(market.get("previous_price_dollars") or 0)
    if not prev or not (yes_bid + yes_ask):
        return False

    mid = (yes_bid + yes_ask) / 2
    trend_up = mid > prev
    whale_bullish = whale_dir == "YES"
    return whale_bullish != trend_up  # opposite direction = reversal


def compute_orderbook_signal(orderbook: dict) -> dict:
    """
    Computes bid/ask depth imbalance from the full order book.

    Imbalance = bid_depth / (bid_depth + ask_depth)
    > 0.65 → more buyers → YES may be underpriced
    < 0.35 → more sellers → YES may be overpriced

    Handles multiple Kalshi orderbook response shapes defensively.
    """
    empty = {"ob_bid_depth": None, "ob_ask_depth": None,
             "ob_imbalance": None, "ob_flag": False, "ob_direction": None}

    if not orderbook:
        return empty

    def _extract_levels(data) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("levels") or data.get("orders") or []
        return []

    def _sum_sizes(levels) -> float:
        total = 0.0
        for lvl in levels:
            if isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                total += float(lvl[1])
            elif isinstance(lvl, dict):
                total += float(lvl.get("size") or lvl.get("quantity") or 0)
        return total

    # Kalshi may nest under "yes" key or at top level
    yes_book = orderbook.get("yes") or orderbook
    bids = _extract_levels(yes_book.get("bids") or yes_book.get("bid") or [])
    asks = _extract_levels(yes_book.get("asks") or yes_book.get("ask") or [])

    bid_depth = _sum_sizes(bids)
    ask_depth = _sum_sizes(asks)
    total     = bid_depth + ask_depth

    if total == 0:
        return empty

    imbalance = bid_depth / total
    ob_flag   = imbalance > 0.65 or imbalance < 0.35
    direction = "YES" if imbalance > 0.65 else ("NO" if imbalance < 0.35 else None)

    return {
        "ob_bid_depth": round(bid_depth, 2),
        "ob_ask_depth": round(ask_depth, 2),
        "ob_imbalance": round(imbalance, 3),
        "ob_flag":      ob_flag,
        "ob_direction": direction,
    }


def score_market(market: dict, config: dict) -> dict:
    """
    Scores a single market for mispricing.

    Returns the market enriched with mid_price, base_rate, raw_edge, flag,
    flag_path, spread_wide, spread_pct, price_drift, and drift_flag.

    Flag behaviour is controlled by config.markets.flag_mode (default "passthrough"):

      "passthrough" (default / baseline)
        flag if: raw_edge > threshold  OR  base_rate is None  OR  drift
        This is the original behaviour — every priced market without a
        matching heuristic is automatically a candidate.

      "strict_anomaly_only"
        flag ONLY if: drift_flag is True
        (whale_detected would also trigger here, but whale detection runs in
        main.py step 5, *after* score_market runs in step 3, so whale state
        is unavailable at this point. main.py applies whale_reversal and
        ob_flag post-hoc to set flag=True for whale markets.)
        base_rate and raw_edge are still computed and returned for Claude
        context, but do not trigger the flag under this mode.

      "strict_with_heuristic"
        flag if: drift_flag  OR  (base_rate is not None AND raw_edge > threshold)
        Adds back the heuristic base-rate edge as a trigger on top of
        strict_anomaly_only.  A market whose heuristic estimate disagrees
        meaningfully with the current price is included; pure BR_NONE markets
        (no matching heuristic) are still excluded.

    whale_reversal is merged into flag by main.py after step 5 regardless of mode.
    """
    mkt_cfg        = config.get("markets", {})
    edge_threshold = mkt_cfg.get("edge_threshold", 0.08)
    flag_mode      = mkt_cfg.get("flag_mode", "passthrough")
    drift_min_abs  = mkt_cfg.get("drift_min_abs", 0.0)
    drift_min_pct  = mkt_cfg.get("drift_min_pct", 0.05)

    yes_bid = float(market.get("yes_bid_dollars") or market.get("yes_bid") or 0)
    yes_ask = float(market.get("yes_ask_dollars") or market.get("yes_ask") or 0)

    mid_price = (yes_bid + yes_ask) / 2 if (yes_bid + yes_ask) > 0 else None

    base_rate = estimate_base_rate(market)

    if mid_price is not None and base_rate is not None:
        raw_edge = abs(base_rate - mid_price)
    else:
        raw_edge = None

    spread = compute_spread_signal(yes_bid, yes_ask, mid_price or 0)
    drift  = compute_drift_signal(mid_price or 0, market, drift_min_abs, drift_min_pct)

    # All signals computed independently of flag_mode — truthful regardless of branch order.
    has_edge    = raw_edge is not None and raw_edge > edge_threshold
    has_drift   = drift["drift_flag"]
    has_br_none = base_rate is None and mid_price is not None

    flag      = False
    flag_path = None   # "EDGE" | "BR_NONE" | "DRIFT" | "HEURISTIC" | None

    if flag_mode == "passthrough":
        if has_edge:
            flag, flag_path = True, "EDGE"
        elif base_rate is None and mid_price is not None:
            flag, flag_path = True, "BR_NONE"
        elif has_drift:
            flag, flag_path = True, "DRIFT"

    elif flag_mode == "strict_anomaly_only":
        if has_drift:
            flag, flag_path = True, "DRIFT"

    elif flag_mode == "strict_with_heuristic":
        if has_drift:
            flag, flag_path = True, "DRIFT"
        elif base_rate is not None and has_edge:
            flag, flag_path = True, "HEURISTIC"

    else:
        raise ValueError(
            f"Unknown flag_mode {flag_mode!r}. "
            "Expected: passthrough | strict_anomaly_only | strict_with_heuristic"
        )

    return {
        **market,
        "mid_price":     mid_price,
        "base_rate":     base_rate,
        "raw_edge":      raw_edge,
        "flag":          flag,
        "flag_path":     flag_path,
        "flag_mode":     flag_mode,
        # Per-signal presence — always set, independent of mode and branch order.
        "sig_edge":      has_edge,
        "sig_drift":     has_drift,
        "sig_br_none":   has_br_none,
        "time_horizon":  market.get("time_horizon", "MONTHLY"),
        **spread,
        **drift,
    }


def score_markets(markets: list[dict], config: dict) -> list[dict]:
    """Scores all filtered markets and returns them sorted by priority."""
    scored = [score_market(m, config) for m in markets]
    # Sort: watchlist-overlap first, then flagged, then by edge desc
    scored.sort(key=lambda m: (
        not m.get("watchlist_signal", False),
        not m.get("flag", False),
        -(m.get("raw_edge") or 0),
    ))
    return scored
