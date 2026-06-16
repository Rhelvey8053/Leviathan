"""
Snapshot the current open Kalshi market universe to disk.

Fetches via events catalog (same path as main.py), skips KXMVE event tickers,
and saves complete raw market objects to data/snapshots/markets_YYYYMMDD_HHMMSS.json.

Usage:
    python analysis/snapshot_markets.py

Each run creates a new timestamped file — never overwrites.
"""

import json
import os
import sys
from datetime import datetime, timezone

# Ensure project root is on path so we can import kalshi / config
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

import kalshi

CONFIG_PATH = os.path.join(ROOT, "config.json")
SNAPSHOT_DIR = os.path.join(ROOT, "data", "snapshots")


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_snapshot(config: dict) -> tuple[list[dict], int]:
    """
    Fetches open markets via events catalog — identical logic to main.py step 2.
    Returns (markets, event_count).
    """
    events = kalshi.fetch_events(config)
    categories = config.get("markets", {}).get("categories", [])
    if categories:
        events = [e for e in events if e.get("category", "") in categories]

    seen = set()
    markets = []
    for event in events:
        event_ticker = event.get("event_ticker") or event.get("ticker", "")
        if not event_ticker or "KXMVE" in event_ticker:
            continue
        try:
            for m in kalshi.fetch_event_markets(config, event_ticker):
                t = m.get("ticker")
                if t and t not in seen:
                    seen.add(t)
                    markets.append(m)
        except Exception as e:
            print(f"  [warn] fetch_event_markets({event_ticker}): {e}")

    return markets, len(events)


def save_snapshot(markets: list[dict], event_count: int, config: dict) -> str:
    """Saves complete market objects to a timestamped JSON file. Returns the path."""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SNAPSHOT_DIR, f"markets_{ts}.json")

    payload = {
        "header": {
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
            "environment":   config.get("environment", "demo"),
            "event_count":   event_count,
            "market_count":  len(markets),
        },
        "markets": markets,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return path


def main():
    config = load_config()
    env = config.get("environment", "demo").upper()
    print(f"Environment: {env}")

    print("Authenticating with Kalshi...")
    kalshi.authenticate(config)

    print("Fetching markets via events catalog...")
    markets, event_count = fetch_snapshot(config)
    print(f"  {len(markets)} markets from {event_count} events")

    path = save_snapshot(markets, event_count, config)
    print(f"\nSnapshot saved: {path}")
    print(f"Market count:   {len(markets)}")


if __name__ == "__main__":
    main()
