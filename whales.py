from datetime import datetime, timezone, timedelta


def detect_whale_activity(ticker: str, trades: list[dict], config: dict) -> dict:
    """
    Detects unusually large trade activity as a proxy for informed money.

    - Flags individual trades > size_multiplier * avg trade size
    - Detects volume spikes: last N hours vs prior period
    - Notes direction of large trades (YES or NO)
    """
    cfg = config.get("whales", {})
    size_multiplier = cfg.get("size_multiplier", 5)
    spike_hours = cfg.get("volume_spike_hours", 1)

    if not trades:
        return {
            "ticker": ticker,
            "whale_detected": False,
            "large_trades": [],
            "volume_spike": False,
            "whale_direction": None,
            "avg_trade_size": 0,
            "max_trade_size": 0,
        }

    # Normalise sizes — Kalshi trades may use "count" or "size"
    def _size(t: dict) -> float:
        return float(t.get("count") or t.get("size") or 0)

    sizes = [_size(t) for t in trades]
    avg_size = sum(sizes) / len(sizes) if sizes else 0
    max_size = max(sizes) if sizes else 0
    threshold = avg_size * size_multiplier

    large_trades = [t for t in trades if _size(t) > threshold]

    # Volume spike: compare last spike_hours to prior window
    now = datetime.now(timezone.utc)
    spike_cutoff = now - timedelta(hours=spike_hours)
    prior_cutoff = now - timedelta(hours=spike_hours * 24)

    def _parse_time(t: dict) -> datetime | None:
        ts = t.get("created_time") or t.get("ts")
        if not ts:
            return None
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            return None

    recent_vol = sum(_size(t) for t in trades if (_parse_time(t) or now) >= spike_cutoff)
    prior_vol = sum(
        _size(t)
        for t in trades
        if prior_cutoff <= (_parse_time(t) or now) < spike_cutoff
    )
    prior_hourly_avg = prior_vol / max(spike_hours * 23, 1)
    volume_spike = recent_vol > prior_hourly_avg * size_multiplier if prior_hourly_avg > 0 else False

    # Whale direction: majority side of large trades
    whale_direction = None
    if large_trades:
        yes_vol = sum(_size(t) for t in large_trades if (t.get("taker_side") or "").upper() == "YES")
        no_vol = sum(_size(t) for t in large_trades if (t.get("taker_side") or "").upper() == "NO")
        if yes_vol > no_vol:
            whale_direction = "YES"
        elif no_vol > yes_vol:
            whale_direction = "NO"

    whale_detected = bool(large_trades) or volume_spike

    return {
        "ticker": ticker,
        "whale_detected": whale_detected,
        "large_trades": large_trades,
        "volume_spike": volume_spike,
        "whale_direction": whale_direction,
        "avg_trade_size": avg_size,
        "max_trade_size": max_size,
    }


def scan_all_markets(tickers: list[str], trades_by_ticker: dict[str, list[dict]], config: dict) -> list[dict]:
    """
    Runs detect_whale_activity across all provided tickers.
    Returns only markets where whale_detected = True.
    """
    results = []
    for ticker in tickers:
        trades = trades_by_ticker.get(ticker, [])
        result = detect_whale_activity(ticker, trades, config)
        if result["whale_detected"]:
            results.append(result)
    return results
