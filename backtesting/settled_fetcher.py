"""
backtesting/settled_fetcher.py — replay-settled-fetcher.

Pulls Kalshi settled markets (with their final YES/NO outcome) and persists
them to a dedicated `settled_markets` table in leviathan.db — separate from
`signals`/`runs`, which this module never reads or writes. Read-only against
every existing table.

Why: the local snapshot archive (data/snapshots) only reaches back to
2026-06-16 (confirmed by directory listing), which caps any backtesting
corpus built from it well below what is useful. Kalshi's own settled-event
history reaches much further back;
this module is the read path for that history, not a reconstruction of
market state as of a historical date (that's replay-asof-reconstruction's
job, once this table gives it something to reconstruct against).

Usage:
    python -m backtesting.settled_fetcher [--max-events N]
"""

import argparse
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from core import kalshi
from core.logger import DB_PATH

DEFAULT_MAX_EVENTS = 2000


@contextmanager
def _db(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_table(db_path: str = DB_PATH) -> None:
    """Creates settled_markets if absent. Never touches signals or runs."""
    with _db(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settled_markets (
                ticker           TEXT PRIMARY KEY,
                event_ticker     TEXT,
                series_ticker    TEXT,
                category         TEXT,
                title            TEXT,
                result           TEXT,
                close_time       TEXT,
                settlement_ts    TEXT,
                volume           REAL,
                open_interest    REAL,
                last_price       REAL,
                fetched_at       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_settled_markets_event  ON settled_markets(event_ticker);
            CREATE INDEX IF NOT EXISTS idx_settled_markets_series ON settled_markets(series_ticker);
            CREATE INDEX IF NOT EXISTS idx_settled_markets_close  ON settled_markets(close_time);
        """)


def _to_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _row_from_market(market: dict, event: dict) -> dict | None:
    """
    Builds one settled_markets row. series_ticker/category live on the
    EVENT object only, not the raw market object (the same lesson learned
    for series_ticker in the Kalshi-link work) — passed in from the
    event-fetch loop, never guessed.
    Returns None if the market has no clean YES/NO result — a market that
    was voided or otherwise unresolved isn't a settled outcome to persist.
    """
    result = (market.get("result") or "").upper()
    if result not in ("YES", "NO"):
        return None
    return {
        "ticker":         market.get("ticker", ""),
        "event_ticker":   market.get("event_ticker", "") or event.get("event_ticker", ""),
        "series_ticker":  event.get("series_ticker", ""),
        "category":       event.get("category", ""),
        "title":          market.get("title", ""),
        "result":         result,
        "close_time":     market.get("close_time", ""),
        "settlement_ts":  market.get("settlement_ts", ""),
        "volume":         _to_float(market.get("volume_fp")),
        "open_interest":  _to_float(market.get("open_interest_fp")),
        "last_price":     _to_float(market.get("last_price_dollars")),
        "fetched_at":     datetime.now(timezone.utc).isoformat(),
    }


def fetch_and_store_settled_markets(
    config: dict, db_path: str = DB_PATH, max_events: int = DEFAULT_MAX_EVENTS,
) -> dict:
    """
    Fetches settled events (excludes KXMVE parlays), fetches each event's
    settled markets, and inserts new rows into settled_markets. Idempotent:
    INSERT OR IGNORE on ticker (the primary key), so re-running never
    duplicates or overwrites a row already persisted.

    Returns {"events_scanned": n, "markets_fetched": n, "inserted": n,
             "already_present": n, "skipped_unresolved": n}.
    """
    _init_table(db_path)

    events = kalshi.fetch_settled_events(config, max_fetch=max_events)
    events = [e for e in events if "KXMVE" not in (e.get("event_ticker") or e.get("ticker") or "")]

    summary = {
        "events_scanned": 0, "markets_fetched": 0,
        "inserted": 0, "already_present": 0, "skipped_unresolved": 0,
    }

    for i, event in enumerate(events):
        if i > 0:
            time.sleep(0.3)  # ~3 req/s, matches resolve_outcomes()'s existing rate-limit courtesy
        event_ticker = event.get("event_ticker") or event.get("ticker", "")
        if not event_ticker:
            continue
        try:
            markets = kalshi.fetch_settled_event_markets(config, event_ticker)
        except Exception as e:
            print(f"  [settled_fetcher] fetch_settled_event_markets({event_ticker}): {e}")
            continue

        summary["events_scanned"] += 1
        summary["markets_fetched"] += len(markets)

        for market in markets:
            row = _row_from_market(market, event)
            if row is None:
                summary["skipped_unresolved"] += 1
                continue
            with _db(db_path) as conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO settled_markets "
                    "(ticker, event_ticker, series_ticker, category, title, result, "
                    " close_time, settlement_ts, volume, open_interest, last_price, fetched_at) "
                    "VALUES (:ticker, :event_ticker, :series_ticker, :category, :title, :result, "
                    " :close_time, :settlement_ts, :volume, :open_interest, :last_price, :fetched_at)",
                    row,
                )
                if cur.rowcount:
                    summary["inserted"] += 1
                else:
                    summary["already_present"] += 1

    return summary


def get_settled_market_count(db_path: str = DB_PATH) -> int:
    """Read-only count of rows currently in settled_markets. 0 if table doesn't exist yet."""
    try:
        with _db(db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM settled_markets").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch and persist Kalshi settled markets")
    parser.add_argument("--max-events", type=int, default=DEFAULT_MAX_EVENTS,
                        help=f"Max settled events to pull (default: {DEFAULT_MAX_EVENTS})")
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import json
    with open(os.path.join(root, "config.json"), encoding="utf-8") as f:
        config = json.load(f)

    print(f"Fetching up to {args.max_events} settled events...")
    result = fetch_and_store_settled_markets(config, max_events=args.max_events)
    print(f"Events scanned:      {result['events_scanned']}")
    print(f"Markets fetched:     {result['markets_fetched']}")
    print(f"Newly inserted:      {result['inserted']}")
    print(f"Already present:     {result['already_present']}")
    print(f"Skipped (unresolved):{result['skipped_unresolved']}")
    print(f"Total in table now:  {get_settled_market_count()}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
