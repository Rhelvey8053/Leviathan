"""
Smart money tracking via Polymarket's public on-chain data.

Polymarket exposes all wallet addresses, trade history, and position PnL
through their data API — no auth required. We use this to:

  1. Discover winning wallets: sample recent traders, score by open-position
     PnL across multiple markets (position count + avg % return + cash PnL).
  2. Scan flagged markets: check if any winning wallet has recently traded
     a specific conditionId and note their direction (YES/NO).
  3. Cache winners to winning_accounts.json and refresh every N hours.
"""

import json
import os
import time
import requests

DATA_API   = "https://data-api.polymarket.com"
CACHE_FILE = os.path.join(os.path.dirname(__file__), "winning_accounts.json")


# ── API helpers ───────────────────────────────────────────────────────────────

def _get(path: str, params: dict = None, timeout: int = 12) -> list | dict | None:
    try:
        resp = requests.get(f"{DATA_API}/{path}", params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def fetch_recent_trades(limit: int = 300) -> list[dict]:
    """Recent trades across all Polymarket markets. Returns wallet addresses + conditionIds."""
    result = _get("trades", {"limit": limit})
    return result if isinstance(result, list) else []


def fetch_user_trades(address: str, limit: int = 100) -> list[dict]:
    """Trade history for a specific wallet."""
    result = _get("trades", {"user": address, "limit": limit})
    return result if isinstance(result, list) else []


def fetch_user_positions(address: str) -> list[dict]:
    """Open positions for a wallet with PnL breakdown."""
    result = _get("positions", {"user": address, "limit": 500})
    return result if isinstance(result, list) else []


# ── Wallet scoring ────────────────────────────────────────────────────────────

def _score_wallet(positions: list[dict]) -> dict | None:
    """
    Score a wallet on their open position performance.
    Needs multiple positions with positive returns to qualify —
    filters out lucky one-market trades.
    """
    if not positions:
        return None

    pct_pnls  = []
    cash_pnls = []
    for p in positions:
        try:
            pct_pnls.append(float(p.get("percentPnl") or 0))
            cash_pnls.append(float(p.get("cashPnl") or 0))
        except (TypeError, ValueError):
            pass

    if not pct_pnls:
        return None

    return {
        "position_count": len(positions),
        "avg_pct_pnl":    round(sum(pct_pnls) / len(pct_pnls), 2),
        "total_cash_pnl": round(sum(cash_pnls), 2),
        "winning_positions": sum(1 for p in pct_pnls if p > 0),
    }


def _is_winner(stats: dict, config: dict) -> bool:
    cfg = config.get("accounts", {})
    return (
        stats["position_count"]  >= cfg.get("min_positions", 3)
        and stats["avg_pct_pnl"] >= cfg.get("min_pct_pnl", 10.0)
        and stats["total_cash_pnl"] >= cfg.get("min_cash_pnl", 25.0)
    )


# ── Discovery ─────────────────────────────────────────────────────────────────

def discover_winners(config: dict) -> list[dict]:
    """
    Discovers winning wallets from recent Polymarket activity.

    1. Fetch N recent trades across all markets → unique wallet addresses
    2. For each wallet, fetch their open positions and score by PnL
    3. Filter to wallets meeting winner criteria
    4. Return ranked list
    """
    cfg            = config.get("accounts", {})
    sample_size    = cfg.get("discovery_sample_size", 300)
    max_wallets    = cfg.get("max_wallets_to_track", 50)

    print(f"  [accounts] Sampling {sample_size} recent trades for wallet discovery...")
    trades   = fetch_recent_trades(sample_size)
    wallets  = list({t["proxyWallet"] for t in trades if t.get("proxyWallet")})
    print(f"  [accounts] {len(wallets)} unique wallets found, scoring...")

    winners = []
    for address in wallets:
        positions = fetch_user_positions(address)
        stats     = _score_wallet(positions)
        if stats and _is_winner(stats, config):
            winners.append({"address": address, **stats})

    winners.sort(key=lambda w: (w["avg_pct_pnl"], w["total_cash_pnl"]), reverse=True)
    return winners[:max_wallets]


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_fresh(config: dict) -> bool:
    if not os.path.exists(CACHE_FILE):
        return False
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ttl_hours = config.get("accounts", {}).get("cache_ttl_hours", 24)
        age_hours = (time.time() - data.get("updated_at", 0)) / 3600
        return age_hours < ttl_hours
    except Exception:
        return False


def _save_cache(winners: list[dict]) -> None:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated_at": time.time(), "winners": winners}, f, indent=2)


def _load_cache() -> list[dict]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("winners", [])
    except Exception:
        return []


def load_winners(config: dict) -> list[dict]:
    """
    Returns the cached winners list, running discovery if stale or empty.
    """
    if _cache_fresh(config):
        winners = _load_cache()
        print(f"  [accounts] Loaded {len(winners)} cached winners")
        return winners

    winners = discover_winners(config)
    _save_cache(winners)
    print(f"  [accounts] Discovered {len(winners)} winning wallets — cache updated")
    return winners


# ── Smart money scan ──────────────────────────────────────────────────────────

def scan_market(condition_id: str, winners: list[dict], config: dict) -> list[dict]:
    """
    Checks if any winning wallet has recently traded a specific market.

    For each winner, fetches their recent trade history and looks for trades
    matching condition_id. Returns list of smart money signals with direction.
    """
    if not condition_id or not winners:
        return []

    cfg        = config.get("accounts", {})
    max_check  = cfg.get("max_wallets_per_scan", 20)
    signals    = []
    winner_map = {w["address"]: w for w in winners}

    for winner in winners[:max_check]:
        trades = fetch_user_trades(winner["address"], limit=50)
        market_trades = [
            t for t in trades
            if t.get("conditionId") == condition_id
        ]
        if not market_trades:
            continue

        # Most recent trade determines current direction
        latest     = max(market_trades, key=lambda t: t.get("timestamp", 0))
        outcome    = (latest.get("outcome") or "").strip()
        side       = (latest.get("side") or "BUY").upper()
        direction  = None

        if outcome.lower() in ("yes", "1", "true"):
            direction = "YES" if side == "BUY" else "NO"
        elif outcome.lower() in ("no", "0", "false", "down"):
            direction = "NO" if side == "BUY" else "YES"

        if direction:
            signals.append({
                "address":        winner["address"][:10] + "...",
                "direction":      direction,
                "trade_count":    len(market_trades),
                "avg_pct_pnl":    winner["avg_pct_pnl"],
                "total_cash_pnl": winner["total_cash_pnl"],
                "last_trade_ts":  latest.get("timestamp"),
            })

    return signals


# ── Main entry point ──────────────────────────────────────────────────────────

def enrich_with_smart_money(flagged_markets: list[dict], poly_data: dict, config: dict) -> dict[str, list[dict]]:
    """
    For each flagged market that has a Polymarket match, scans for smart money.
    Returns dict: {kalshi_ticker → [smart_money_signals]}
    """
    if not config.get("accounts", {}).get("enabled", True):
        return {}

    winners = load_winners(config)
    if not winners:
        print("  [accounts] No winning wallets found — skipping smart money scan")
        return {}

    results = {}
    for m in flagged_markets:
        ticker = m.get("ticker", "")
        poly   = poly_data.get(ticker)
        if not poly:
            continue

        condition_id = poly.get("condition_id") or poly.get("poly_slug", "")
        if not condition_id:
            continue

        signals = scan_market(condition_id, winners, config)
        if signals:
            results[ticker] = signals

    return results
