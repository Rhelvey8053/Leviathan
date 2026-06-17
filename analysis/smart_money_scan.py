"""
Smart money watchlist scanner.

Tracks open positions of the top Polymarket traders (seeded from the monthly
leaderboard) and surfaces what they're betting on right now. Cross-references
with the latest Kalshi snapshot to flag markets where smart money is active
on a related Polymarket market.

Run:
    python analysis/smart_money_scan.py
"""

import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import accounts
import polymarket

CONFIG_PATH  = os.path.join(ROOT, "config.json")
SNAPSHOT_DIR   = os.path.join(ROOT, "data", "snapshots")
CACHE_PATH     = os.path.join(ROOT, "data", "watchlist_cache.json")
REPORT_DIR     = os.path.join(ROOT, "data", "smart_money")


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _cache_fresh(ttl_hours: float) -> bool:
    try:
        data = json.load(open(CACHE_PATH, encoding="utf-8"))
        age  = (datetime.now(timezone.utc).timestamp() - data.get("ts", 0)) / 3600
        return age < ttl_hours
    except Exception:
        return False


def _save_cache(positions_by_trader: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"ts": datetime.now(timezone.utc).timestamp(),
                   "data": positions_by_trader}, f, indent=2)


def _load_cache() -> dict:
    return json.load(open(CACHE_PATH, encoding="utf-8")).get("data", {})


def fetch_watchlist_positions(config: dict, force: bool = False) -> dict[str, list[dict]]:
    """
    Returns {trader_name: [open_position, ...]} for each watchlist entry.
    Uses a short TTL cache (default 4h) to avoid hammering the API.
    """
    cfg     = config.get("accounts", {})
    ttl     = cfg.get("watchlist_cache_ttl_hours", 4)
    min_val = cfg.get("watchlist_min_position_value", 1000)
    watchlist = cfg.get("watchlist", [])

    if not force and _cache_fresh(ttl):
        print("  [watchlist] Loaded from cache")
        return _load_cache()

    result = {}
    for entry in watchlist:
        name    = entry["name"]
        addr    = entry["address"]
        monthly = entry.get("monthly_pnl", 0)
        positions = accounts.fetch_user_positions(addr)
        open_pos  = [
            p for p in positions
            if not p.get("redeemable")
            and float(p.get("currentValue") or 0) >= min_val
        ]
        open_pos.sort(key=lambda p: float(p.get("currentValue") or 0), reverse=True)
        result[name] = {
            "address":     addr,
            "monthly_pnl": monthly,
            "positions":   open_pos,
        }
        print(f"  {name:<18}  ${monthly/1e6:.1f}M/mo  {len(open_pos)} open positions >= ${min_val:,}")

    _save_cache(result)
    return result


def _load_kalshi_titles() -> dict[str, str]:
    try:
        files = sorted(f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json"))
        if not files:
            return {}
        data = json.load(open(os.path.join(SNAPSHOT_DIR, files[-1]), encoding="utf-8"))
        return {m["ticker"]: m.get("title", "") for m in data.get("markets", [])}
    except Exception:
        return {}


def _match_to_kalshi(pos_title: str, kalshi_titles: dict[str, str],
                     min_score: float = 0.35) -> list[tuple[str, float]]:
    """Simple keyword overlap score between a Polymarket title and Kalshi titles."""
    words = set(pos_title.lower().split())
    stop  = {"will", "the", "a", "an", "in", "on", "of", "to", "by", "vs",
              "vs.", "at", "for", "and", "or", "is", "be", "win", "does"}
    words -= stop
    if not words:
        return []

    matches = []
    for ticker, title in kalshi_titles.items():
        kwords  = set(title.lower().split()) - stop
        overlap = len(words & kwords) / max(len(words | kwords), 1)
        if overlap >= min_score:
            matches.append((ticker, round(overlap, 3)))
    matches.sort(key=lambda x: -x[1])
    return matches[:3]


def run_smart_money_scan(config: dict | None = None, force_refresh: bool = False) -> dict:
    if config is None:
        config = load_config()

    print(f"\n=== Smart Money Watchlist Scan | {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} ===\n")
    print("Fetching open positions for watchlist traders...")

    trader_data   = fetch_watchlist_positions(config, force=force_refresh)
    kalshi_titles = _load_kalshi_titles()

    print(f"\n  Kalshi snapshot: {len(kalshi_titles)} markets loaded for cross-reference\n")
    print("=" * 100)

    all_signals   = []
    total_traders = 0
    total_pos     = 0

    for name, data in trader_data.items():
        positions = data.get("positions", [])
        if not positions:
            continue
        total_traders += 1
        total_pos     += len(positions)

        monthly = data.get("monthly_pnl", 0)
        print(f"\n  {name}  (${monthly/1e6:.1f}M/mo)  —  {len(positions)} significant open positions:")

        for p in positions:
            val     = float(p.get("currentValue") or 0)
            price   = float(p.get("curPrice") or p.get("avgPrice") or 0)
            outcome = p.get("outcome", "?")
            title   = p.get("title", "")
            slug    = p.get("eventSlug") or p.get("slug", "")
            pct_pnl = float(p.get("percentPnl") or 0)
            pnl_str = f"{pct_pnl:+.1f}%"

            kalshi_matches = _match_to_kalshi(title, kalshi_titles)
            match_str = ""
            if kalshi_matches:
                match_str = f"  -> Kalshi: {kalshi_matches[0][0]} ({kalshi_matches[0][1]:.0%} match)"

            print(f"    {outcome:<4}  ${val:>9,.0f}  {price:.2f}  {pnl_str:>7}  {title[:55]}{match_str}")

            if kalshi_matches:
                all_signals.append({
                    "trader":        name,
                    "monthly_pnl":   monthly,
                    "poly_title":    title,
                    "poly_outcome":  outcome,
                    "poly_price":    price,
                    "position_val":  val,
                    "pct_pnl":       pct_pnl,
                    "poly_url":      f"https://polymarket.com/event/{slug}",
                    "kalshi_ticker": kalshi_matches[0][0],
                    "match_score":   kalshi_matches[0][1],
                })

    print(f"\n{'='*100}")
    print(f"\n  Traders with open positions: {total_traders}")
    print(f"  Total significant positions: {total_pos}")

    if all_signals:
        print(f"\n  Kalshi cross-references found ({len(all_signals)}):\n")
        all_signals.sort(key=lambda s: -s["position_val"])
        for s in all_signals:
            print(f"    [{s['trader']}]  {s['poly_outcome']} on {s['poly_title'][:45]}")
            print(f"      -> Kalshi: {s['kalshi_ticker']}  (match {s['match_score']:.0%})  "
                  f"Poly price: {s['poly_price']:.2f}  Position: ${s['position_val']:,.0f}")
    else:
        print("\n  No Kalshi cross-references found in this snapshot.")

    print()

    result = {
        "traders_active":  total_traders,
        "positions_total": total_pos,
        "kalshi_signals":  all_signals,
        "trader_data":     trader_data,
        "run_at":          datetime.now(timezone.utc).isoformat(),
    }
    return result


def save_report(result: dict) -> str:
    """Write a dated markdown report to data/smart_money/YYYY-MM-DD.md. Returns the path."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_at    = result.get("run_at", datetime.now(timezone.utc).isoformat())
    out_path  = os.path.join(REPORT_DIR, f"{date_str}.md")

    lines = [
        f"# Smart Money Watchlist — {date_str}",
        f"",
        f"**Run at:** {run_at}",
        f"**Traders active:** {result['traders_active']}  "
        f"**Positions tracked:** {result['positions_total']}",
        f"",
    ]

    signals = result.get("kalshi_signals", [])
    if signals:
        lines += [
            f"## Kalshi Cross-References ({len(signals)})",
            f"",
            f"| Trader | Outcome | Market | Poly Price | Position | Kalshi Ticker | Match |",
            f"|--------|---------|--------|-----------|----------|--------------|-------|",
        ]
        for s in sorted(signals, key=lambda x: -x["position_val"]):
            lines.append(
                f"| {s['trader']} | {s['poly_outcome']} | {s['poly_title'][:45]} "
                f"| {s['poly_price']:.2f} | ${s['position_val']:,.0f} "
                f"| {s['kalshi_ticker']} | {s['match_score']:.0%} |"
            )
        lines.append("")
    else:
        lines += ["## Kalshi Cross-References", "", "None found in this snapshot.", ""]

    lines += ["## All Open Positions", ""]
    for name, data in result.get("trader_data", {}).items():
        positions = data.get("positions", [])
        if not positions:
            continue
        monthly = data.get("monthly_pnl", 0)
        lines.append(f"### {name}  (${monthly/1e6:.1f}M/mo)")
        lines.append("")
        lines.append("| Outcome | Value | Price | PnL | Market |")
        lines.append("|---------|-------|-------|-----|--------|")
        for p in positions:
            val     = float(p.get("currentValue") or 0)
            price   = float(p.get("curPrice") or p.get("avgPrice") or 0)
            outcome = p.get("outcome", "?")
            title   = p.get("title", "")[:55]
            pct_pnl = float(p.get("percentPnl") or 0)
            lines.append(f"| {outcome} | ${val:,.0f} | {price:.2f} | {pct_pnl:+.1f}% | {title} |")
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return out_path


if __name__ == "__main__":
    force = "--refresh" in sys.argv
    cfg   = load_config()
    result = run_smart_money_scan(cfg, force_refresh=force)
    path   = save_report(result)
    print(f"Report saved: {path}")
