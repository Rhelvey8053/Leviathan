"""
Standalone whale scanner: pulls the global recent-trade feed from Kalshi,
detects unusually large or block trades by ticker, and prints a ranked table.

Surfaces whale activity in markets that may not pass the mechanical filter —
the signal here is informed money moving, not price mispricing per se.

Run:
    python analysis/whale_scan.py
"""

import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import kalshi
from core import whales

CONFIG_PATH   = os.path.join(ROOT, "config.json")
SNAPSHOT_DIR  = os.path.join(ROOT, "data", "snapshots")


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_market_titles() -> dict[str, str]:
    """Returns {ticker: title} from the most recent snapshot for display."""
    try:
        files = sorted(f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json"))
        if not files:
            return {}
        with open(os.path.join(SNAPSHOT_DIR, files[-1]), encoding="utf-8") as f:
            data = json.load(f)
        return {m["ticker"]: m.get("title", "") for m in data.get("markets", [])}
    except Exception:
        return {}


def run_whale_scan(config: dict | None = None, trade_limit: int = 500) -> list[dict]:
    """
    Fetch recent trades, run whale detection, print ranked table.
    Returns list of flagged whale result dicts.
    """
    if config is None:
        config = load_config()

    trade_limit = config.get("whales", {}).get("scan_trade_limit", trade_limit)
    titles = _load_market_titles()

    print(f"\n=== Whale Scan | {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} ===\n")
    print(f"Fetching {trade_limit} recent trades from Kalshi global feed...")

    trades = kalshi.fetch_recent_trades(config, limit=trade_limit)
    print(f"  {len(trades)} trades fetched across all markets\n")

    flagged = whales.scan_recent_trades(trades, config)

    if not flagged:
        print("  No whale activity detected in this trade window.\n")
        return []

    cfg         = config.get("whales", {})
    min_size    = cfg.get("min_whale_size", 100)
    multiplier  = cfg.get("size_multiplier", 5)
    print(f"  Thresholds: min_whale_size={min_size} contracts, size_multiplier={multiplier}x avg\n")
    print(f"  {len(flagged)} market(s) with whale activity:\n")

    print(f"  {'Ticker':<46} {'MaxTrade':>9} {'AvgTrade':>9} {'Dir':<5} {'Block':>5} {'Spike':>6}  Title")
    print("  " + "-" * 110)
    for r in flagged:
        ticker  = r["ticker"]
        title   = titles.get(ticker, "")[:45]
        blocks  = len(r["block_trades"])
        spike   = "YES" if r["volume_spike"] else "no"
        direction = r["whale_direction"] or "—"
        print(
            f"  {ticker:<46} "
            f"{r['max_trade_size']:>9.1f} "
            f"{r['avg_trade_size']:>9.1f} "
            f"{direction:<5} "
            f"{blocks:>5} "
            f"{spike:>6}  "
            f"{title}"
        )

    print(f"\n  Total trades scanned: {len(trades)}")
    print(f"  Markets flagged:      {len(flagged)}")
    block_count = sum(len(r["block_trades"]) for r in flagged)
    if block_count:
        print(f"  Block trades found:   {block_count}")
    print()

    return flagged


if __name__ == "__main__":
    cfg = load_config()
    run_whale_scan(cfg)
