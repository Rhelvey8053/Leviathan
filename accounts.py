"""
Smart money tracking via Polymarket's public on-chain data.

Discovers winning wallets, enriches them with profile data (name, win rate,
active markets), caches them, and scans matched markets for their positioning.
"""

import json
import os
import time
import requests

DATA_API   = "https://data-api.polymarket.com"
POLY_URL   = "https://polymarket.com/profile"
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
    result = _get("trades", {"limit": limit})
    return result if isinstance(result, list) else []


def fetch_user_trades(address: str, limit: int = 100) -> list[dict]:
    result = _get("trades", {"user": address, "limit": limit})
    return result if isinstance(result, list) else []


def fetch_user_positions(address: str) -> list[dict]:
    result = _get("positions", {"user": address, "limit": 500})
    return result if isinstance(result, list) else []


# ── Profile enrichment ────────────────────────────────────────────────────────

def _extract_profile(trades: list[dict], address: str) -> dict:
    """
    Extracts display name, pseudonym, and profile URL from trade records.
    Polymarket embeds name/pseudonym directly in trade responses.
    Falls back to truncated address if no name set.
    """
    for t in trades:
        if t.get("proxyWallet", "").lower() == address.lower():
            name      = (t.get("name") or "").strip()
            pseudonym = (t.get("pseudonym") or "").strip()
            display   = name or pseudonym or ""
            return {
                "display_name": display,
                "name":         name,
                "pseudonym":    pseudonym,
                "profile_url":  f"{POLY_URL}/{address}",
            }
    return {
        "display_name": "",
        "name":         "",
        "pseudonym":    "",
        "profile_url":  f"{POLY_URL}/{address}",
    }


def _score_wallet(positions: list[dict]) -> dict | None:
    """
    Scores a wallet on their position performance.
    Computes win rate from resolved positions (redeemable=True means market resolved).
    """
    if not positions:
        return None

    pct_pnls   = []
    cash_pnls  = []
    resolved   = []
    active_mkts = []

    for p in positions:
        try:
            pct  = float(p.get("percentPnl") or 0)
            cash = float(p.get("cashPnl") or 0)
            title = (p.get("title") or "").strip()

            # Exclude coin-flip / tick-resolution markets from all scoring
            if _is_coinflip(title):
                continue

            pct_pnls.append(pct)
            cash_pnls.append(cash)

            # Track active (unresolved) markets by title
            slug    = (p.get("eventSlug") or p.get("slug") or "").strip()
            outcome = (p.get("outcome") or "").strip()
            if title and not p.get("redeemable"):
                active_mkts.append({
                    "title":   title,
                    "slug":    slug,
                    "outcome": outcome,
                    "pct_pnl": pct,
                    "url":     f"https://polymarket.com/event/{slug}" if slug else "",
                })

            # Resolved = redeemable flag set (market closed)
            if p.get("redeemable"):
                resolved.append(pct)
        except (TypeError, ValueError):
            pass

    if not pct_pnls:
        return None

    wins = sum(1 for p in resolved if p > 0)
    win_rate = round(wins / len(resolved) * 100, 1) if resolved else None

    # Sort active markets by cash PnL desc
    active_mkts.sort(key=lambda m: abs(m["pct_pnl"]), reverse=True)

    return {
        "position_count":    len(positions),
        "avg_pct_pnl":       round(sum(pct_pnls) / len(pct_pnls), 2),
        "total_cash_pnl":    round(sum(cash_pnls), 2),
        "winning_positions": sum(1 for p in pct_pnls if p > 0),
        "resolved_count":    len(resolved),
        "win_rate":          win_rate,
        "active_markets":    active_mkts[:5],  # top 5 by PnL magnitude
    }


_COINFLIP_PATTERNS = [
    "up or down", "up/down", "bitcoin up", "btc up", "eth up",
    "5m", "1m", "10m", "15m", "price up", "price down",
    "higher or lower", "above or below",
]

def _is_coinflip(title: str) -> bool:
    t = title.lower()
    return any(p in t for p in _COINFLIP_PATTERNS)


def _is_winner(stats: dict, config: dict) -> bool:
    cfg = config.get("accounts", {})
    min_resolved = cfg.get("min_resolved_count", 10)
    min_win_rate = cfg.get("min_win_rate", 55.0)

    # Must have a verified track record on resolved markets
    if stats["resolved_count"] < min_resolved:
        return False
    if stats["win_rate"] is None or stats["win_rate"] < min_win_rate:
        return False

    return (
        stats["position_count"]  >= cfg.get("min_positions", 5)
        and stats["avg_pct_pnl"] >= cfg.get("min_pct_pnl", 10.0)
        and stats["total_cash_pnl"] >= cfg.get("min_cash_pnl", 100.0)
    )


# ── Discovery ─────────────────────────────────────────────────────────────────

def discover_winners(config: dict) -> list[dict]:
    cfg         = config.get("accounts", {})
    sample_size = cfg.get("discovery_sample_size", 300)
    max_wallets = cfg.get("max_wallets_to_track", 50)

    print(f"  [accounts] Sampling {sample_size} recent trades for wallet discovery...")
    trades  = fetch_recent_trades(sample_size)
    wallets = list({t["proxyWallet"] for t in trades if t.get("proxyWallet")})
    print(f"  [accounts] {len(wallets)} unique wallets found, scoring...")

    winners = []
    for address in wallets:
        positions = fetch_user_positions(address)
        stats     = _score_wallet(positions)
        if not (stats and _is_winner(stats, config)):
            continue

        # Enrich with profile: check bulk trades first, then fetch user's own trades
        profile = _extract_profile(trades, address)
        if not profile["display_name"]:
            user_trades = fetch_user_trades(address, limit=5)
            profile = _extract_profile(user_trades, address)
            if not profile["display_name"]:
                # Last resort: name may be on any trade from that wallet
                for t in user_trades:
                    n = (t.get("name") or "").strip()
                    p = (t.get("pseudonym") or "").strip()
                    if n or p:
                        profile["display_name"] = n or p
                        profile["name"]         = n
                        profile["pseudonym"]    = p
                        break

        winners.append({
            "address":    address,
            "profile_url": profile["profile_url"],
            "display_name": profile["display_name"],
            "name":        profile["name"],
            "pseudonym":   profile["pseudonym"],
            **stats,
        })

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
    if not condition_id or not winners:
        return []

    max_check = config.get("accounts", {}).get("max_wallets_per_scan", 20)
    signals   = []

    for winner in winners[:max_check]:
        trades        = fetch_user_trades(winner["address"], limit=50)
        market_trades = [t for t in trades if t.get("conditionId") == condition_id]
        if not market_trades:
            continue

        latest    = max(market_trades, key=lambda t: t.get("timestamp", 0))
        outcome   = (latest.get("outcome") or "").strip().lower()
        side      = (latest.get("side") or "BUY").upper()
        direction = None

        if outcome in ("yes", "1", "true"):
            direction = "YES" if side == "BUY" else "NO"
        elif outcome in ("no", "0", "false", "down"):
            direction = "NO" if side == "BUY" else "YES"

        if direction:
            signals.append({
                "address":       winner["address"],
                "display_name":  winner.get("display_name", ""),
                "name":          winner.get("name", ""),
                "pseudonym":     winner.get("pseudonym", ""),
                "profile_url":   winner.get("profile_url", f"{POLY_URL}/{winner['address']}"),
                "direction":     direction,
                "trade_count":   len(market_trades),
                "avg_pct_pnl":   winner["avg_pct_pnl"],
                "total_cash_pnl": winner["total_cash_pnl"],
                "win_rate":      winner.get("win_rate"),
                "active_markets": winner.get("active_markets", []),
                "last_trade_ts": latest.get("timestamp"),
            })

    return signals


# ── Main entry point ──────────────────────────────────────────────────────────

def enrich_with_smart_money(flagged_markets: list[dict], poly_data: dict, config: dict) -> dict[str, list[dict]]:
    if not config.get("accounts", {}).get("enabled", True):
        return {}

    winners = load_winners(config)
    if not winners:
        print("  [accounts] No winning wallets found — skipping smart money scan")
        return {}

    results = {}
    for m in flagged_markets:
        ticker       = m.get("ticker", "")
        poly         = poly_data.get(ticker)
        if not poly:
            continue
        condition_id = poly.get("condition_id") or poly.get("poly_slug", "")
        if not condition_id:
            continue
        signals = scan_market(condition_id, winners, config)
        if signals:
            results[ticker] = signals

    return results
