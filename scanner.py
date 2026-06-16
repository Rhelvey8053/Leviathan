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
      1. Volume > max_volume_filter
      2. Volume < min_volume
      3. Title contains efficient market keywords
      4. Closing in < min_days_to_close
      5. Closing in > max_days_to_close
    """
    cfg          = config.get("markets", {})
    global_min_vol  = cfg.get("min_volume", 500)
    max_vol         = cfg.get("max_volume_filter", 150000)
    min_days        = cfg.get("min_days_to_close", 0)
    max_days        = cfg.get("max_days_to_close", 180)
    min_price       = cfg.get("min_market_price", 0.05)
    max_price       = cfg.get("max_market_price", 0.95)
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


def estimate_base_rate(market: dict) -> float | None:
    """
    Simple heuristic pass before calling Claude (saves tokens).
    Returns a float 0.0–1.0 if a known signal applies, else None.
    scorer.py handles None markets with the full Claude call.
    """
    title = (market.get("title") or "").lower()

    # Binary yes/no events with known rough base rates
    heuristics = [
        (["will it rain", "chance of rain", "precipitation"], 0.40),
        (["win the election", "win election", "wins the election"], 0.50),
        (["reach $", "hits $", "exceed $", "above $"], 0.30),
        (["below $", "under $", "fall below", "drop below"], 0.30),
        (["ipo", "initial public offering"], 0.35),
        (["merger", "acquisition", "acquired by"], 0.40),
        (["recession", "in recession"], 0.25),
        (["default", "debt default"], 0.10),
    ]
    for signals, rate in heuristics:
        if any(s in title for s in signals):
            return rate

    return None


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


def compute_drift_signal(mid: float, market: dict) -> dict:
    """
    Drift between current order-book mid and the last traded price.
    >5% drift = order book has moved away from fair value → mean reversion candidate.
    Uses last_price_dollars already present in every market dict (no extra API call).
    """
    last = float(market.get("last_price_dollars") or 0)
    if not last or mid is None:
        return {"price_drift": None, "drift_flag": False}
    drift = (mid - last) / last
    return {"price_drift": round(drift, 4), "drift_flag": abs(drift) > 0.05}


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
    Returns enriched market dict with mid_price, base_rate, raw_edge, flag,
    spread_wide, spread_pct, price_drift, and drift_flag.
    whale_reversal is computed separately in main.py after whale detection.
    """
    edge_threshold = config.get("markets", {}).get("edge_threshold", 0.08)

    yes_bid = float(market.get("yes_bid_dollars") or market.get("yes_bid") or 0)
    yes_ask = float(market.get("yes_ask_dollars") or market.get("yes_ask") or 0)

    mid_price = (yes_bid + yes_ask) / 2 if (yes_bid + yes_ask) > 0 else None

    base_rate = estimate_base_rate(market)

    if mid_price is not None and base_rate is not None:
        raw_edge = abs(base_rate - mid_price)
    else:
        raw_edge = None

    spread = compute_spread_signal(yes_bid, yes_ask, mid_price or 0)
    drift  = compute_drift_signal(mid_price or 0, market)

    flag = (
        (raw_edge is not None and raw_edge > edge_threshold)
        or (base_rate is None and mid_price is not None)
        or drift["drift_flag"]
    )

    return {
        **market,
        "mid_price":     mid_price,
        "base_rate":     base_rate,
        "raw_edge":      raw_edge,
        "flag":          flag,
        "time_horizon":  market.get("time_horizon", "MONTHLY"),
        **spread,
        **drift,
    }


def score_markets(markets: list[dict], config: dict) -> list[dict]:
    """Scores all filtered markets and returns them sorted by raw_edge descending."""
    scored = [score_market(m, config) for m in markets]
    # Put flagged markets first, then sort by edge desc
    scored.sort(key=lambda m: (not m.get("flag", False), -(m.get("raw_edge") or 0)))
    return scored
