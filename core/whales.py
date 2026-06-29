import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

STREAK_PATH = Path(__file__).parent.parent / "data" / "whale_history" / "streak.json"


def load_whale_streak() -> dict:
    """Load cross-scan whale direction streak data from disk."""
    if STREAK_PATH.exists():
        try:
            return json.loads(STREAK_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def update_whale_streak(
    whale_results: dict,
    streak_data: dict,
    now_iso: str,
) -> dict:
    """
    Update per-ticker streak counts given today's whale detection results.

    Rules:
      - whale detected, same direction as stored: increment streak
      - whale detected, opposite direction: reset streak to 1
      - no whale detected for a ticker: leave streak unchanged (scan gap; don't penalise)
    """
    for ticker, result in whale_results.items():
        if not result.get("whale_detected"):
            continue
        direction = result.get("whale_direction")
        if not direction:
            continue
        existing = streak_data.get(ticker, {})
        if existing.get("direction") == direction:
            streak_data[ticker] = {
                "direction":    direction,
                "streak":       existing.get("streak", 0) + 1,
                "last_updated": now_iso,
            }
        else:
            streak_data[ticker] = {
                "direction":    direction,
                "streak":       1,
                "last_updated": now_iso,
            }
    return streak_data


def save_whale_streak(streak_data: dict) -> None:
    """Persist streak data to disk."""
    STREAK_PATH.parent.mkdir(parents=True, exist_ok=True)
    STREAK_PATH.write_text(json.dumps(streak_data, indent=2), encoding="utf-8")


def _size(t: dict) -> float:
    """Normalise trade size — Kalshi uses count_fp; fallback to count/size."""
    return float(t.get("count_fp") or t.get("count") or t.get("size") or 0)


def _parse_time(t: dict) -> datetime | None:
    ts = t.get("created_time") or t.get("ts")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def detect_whale_activity(ticker: str, trades: list[dict], config: dict) -> dict:
    """
    Detects unusually large trade activity as a proxy for informed money.

    - Flags individual trades > size_multiplier * avg trade size
    - Detects volume spikes: last N hours vs prior period
    - Notes direction of large trades (YES or NO)
    - Also flags is_block_trade=True trades regardless of relative size
    """
    cfg = config.get("whales", {})
    size_multiplier  = cfg.get("size_multiplier", 5)
    spike_hours      = cfg.get("volume_spike_hours", 1)
    min_whale_size   = cfg.get("min_whale_size", 100)  # absolute floor in contracts

    if not trades:
        return {
            "ticker":         ticker,
            "whale_detected": False,
            "large_trades":   [],
            "block_trades":   [],
            "volume_spike":   False,
            "whale_direction": None,
            "avg_trade_size": 0,
            "max_trade_size": 0,
        }

    sizes    = [_size(t) for t in trades]
    avg_size = sum(sizes) / len(sizes) if sizes else 0
    max_size = max(sizes) if sizes else 0
    threshold = max(avg_size * size_multiplier, min_whale_size)

    large_trades = [t for t in trades if _size(t) >= threshold]
    block_trades = [t for t in trades if t.get("is_block_trade")]

    # Volume spike: compare last spike_hours to prior window
    now          = datetime.now(timezone.utc)
    spike_cutoff = now - timedelta(hours=spike_hours)
    prior_cutoff = now - timedelta(hours=spike_hours * 24)

    recent_vol = sum(_size(t) for t in trades if (_parse_time(t) or now) >= spike_cutoff)
    prior_vol  = sum(
        _size(t) for t in trades
        if prior_cutoff <= (_parse_time(t) or now) < spike_cutoff
    )
    prior_hourly_avg = prior_vol / max(spike_hours * 23, 1)
    volume_spike = recent_vol > prior_hourly_avg * size_multiplier if prior_hourly_avg > 0 else False

    # Whale direction: majority side of large + block trades
    signal_trades = large_trades or block_trades
    whale_direction = None
    if signal_trades:
        yes_vol = sum(_size(t) for t in signal_trades if (t.get("taker_side") or "").lower() == "yes")
        no_vol  = sum(_size(t) for t in signal_trades if (t.get("taker_side") or "").lower() == "no")
        if yes_vol > no_vol:
            whale_direction = "YES"
        elif no_vol > yes_vol:
            whale_direction = "NO"

    whale_detected = bool(large_trades) or bool(block_trades) or volume_spike

    return {
        "ticker":          ticker,
        "whale_detected":  whale_detected,
        "large_trades":    large_trades,
        "block_trades":    block_trades,
        "volume_spike":    volume_spike,
        "whale_direction": whale_direction,
        "avg_trade_size":  round(avg_size, 2),
        "max_trade_size":  round(max_size, 2),
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


def scan_recent_trades(trades: list[dict], config: dict) -> list[dict]:
    """
    Groups a global recent-trades feed by ticker and runs whale detection
    on each group. Returns flagged tickers sorted by max_trade_size descending.

    Use this when you have a global trade feed (kalshi.fetch_recent_trades)
    rather than per-market trade lists. Lets you surface whale activity
    in markets that didn't pass the mechanical filter.
    """
    by_ticker: dict[str, list[dict]] = {}
    for t in trades:
        ticker = t.get("ticker", "")
        if ticker:
            by_ticker.setdefault(ticker, []).append(t)

    flagged = []
    for ticker, ticker_trades in by_ticker.items():
        result = detect_whale_activity(ticker, ticker_trades, config)
        if result["whale_detected"]:
            flagged.append(result)

    flagged.sort(key=lambda r: -r["max_trade_size"])
    return flagged
