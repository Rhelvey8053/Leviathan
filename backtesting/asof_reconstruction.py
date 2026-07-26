"""
backtesting/asof_reconstruction.py — replay-asof-reconstruction.

Given a ticker and a historical as-of date, reconstructs the raw market
state as it stood then, and runs it through the SAME core.scanner.score_market()
the live pipeline uses — so the enriched output is exactly the dict shape
core.scorer.score_markets() already consumes. No scorer, heuristic, or
scanner logic is duplicated or modified here; scoring runs verbatim against
reconstructed input, which is the only way to guarantee shape-match rather
than hope for it.

Two source tiers, tried in priority order:

  1. EXACT  — data/snapshots/*.json, a point-in-time raw dump of every
     active market. Used when a snapshot exists at or before as_of_date and
     the ticker appears in it. Bid/ask/last-price come straight from that
     snapshot's own market record — no aggregation, no interpolation.

  2. APPROXIMATE — Kalshi's daily candlestick history
     (core.kalshi.fetch_market_candlesticks), which reaches back to a
     market's open_time regardless of the local snapshot floor
     (2026-06-16 — see settled_fetcher.py). A day's closing candle stands
     in for point-in-time bid/ask/last-price, so precision is degraded to
     "within one day", not exact-instant — this is a real precision loss,
     not a detail, and callers should treat EXACT and APPROXIMATE results
     differently rather than blurring them together.

If neither tier has data for (ticker, as_of_date), reconstruct_market_state()
returns None. It never fabricates a state — the same "never coerce missing
data to a default" discipline applied throughout this codebase (Kalshi URL
construction, market_baseline_brier NULL handling, run_id backfill,
settled_markets skipping unresolved markets).

Permanent, structural limitation (not a bug): whale/watchlist/Polymarket
cross-reference enrichment (main.py steps 4-6) has no historical archive.
Replayed markets never carry whale_data/poly/watchlist_signal — those keys
are simply absent, exactly as score_market() leaves them absent for any
live market where that enrichment hasn't run yet.

Usage:
    python -m backtesting.asof_reconstruction TICKER 2026-06-20
"""

import argparse
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from core import kalshi, scanner
from core.logger import DB_PATH

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS_DIR = os.path.join(_ROOT, "data", "snapshots")


@contextmanager
def _db(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_snapshot_index() -> list[tuple[datetime, str]]:
    """
    Returns [(fetched_at, filepath), ...] sorted ascending by fetched_at.
    fetched_at comes from each file's own header, not the filename — the
    filename timestamp is when the write started, header.fetched_at is
    the authoritative capture time.
    """
    index = []
    if not os.path.isdir(SNAPSHOTS_DIR):
        return index
    for name in os.listdir(SNAPSHOTS_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(SNAPSHOTS_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                parsed = json.load(f)
            # Defensive against a wrong-shaped file (e.g. a bare JSON array
            # instead of the {"header":..., "markets":...} envelope every
            # writer in this codebase is supposed to produce) — a single
            # malformed file must never break reconstruction for every
            # other ticker/date, only be skipped itself.
            header = parsed.get("header", {}) if isinstance(parsed, dict) else {}
            fetched_at = _parse_dt(header["fetched_at"])
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            continue
        index.append((fetched_at, path))
    index.sort(key=lambda pair: pair[0])
    return index


def _find_snapshot_market(ticker: str, as_of_dt: datetime) -> tuple[dict, str] | None:
    """
    Latest snapshot at or before as_of_dt that contains `ticker`.
    Only the closest prior snapshot is checked — if the ticker isn't in it,
    the market either didn't exist yet or had already closed by then (closed
    markets drop out of later dumps the same way fetch_events() only
    returns "open" ones), so guessing from an older snapshot would risk
    reporting a stale price as if it were current. Falls through to the
    candlestick tier instead.
    """
    index = _load_snapshot_index()
    candidates = [p for (fetched_at, p) in index if fetched_at <= as_of_dt]
    if not candidates:
        return None
    path = candidates[-1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for market in data.get("markets", []):
        if market.get("ticker") == ticker:
            return market, os.path.basename(path)
    return None


def _static_metadata(ticker: str, db_path: str = DB_PATH) -> dict | None:
    """
    series_ticker/event_ticker/category/title/close_time for a ticker,
    sourced from settled_markets if present, else merged across every
    snapshot the ticker appears in. Snapshots are scanned newest-first and,
    per field, the first non-empty value found wins — category in
    particular isn't merged into every snapshot's raw market records the
    same way, so a single (even recent) occurrence isn't reliably complete;
    checking only the nearest one silently produced blank series_ticker/
    category for tickers whose earliest snapshot predated that field being
    populated. Returns None if the ticker is unknown to both sources.
    """
    with _db(db_path) as conn:
        row = conn.execute(
            "SELECT ticker, event_ticker, series_ticker, category, title, close_time "
            "FROM settled_markets WHERE ticker = ?",
            (ticker,),
        ).fetchone()
    if row:
        return dict(row)

    fields = ("event_ticker", "series_ticker", "category", "title", "close_time")
    merged: dict = {}
    found = False
    for _fetched_at, path in reversed(_load_snapshot_index()):
        if found and all(merged.get(field) for field in fields):
            break
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for market in data.get("markets", []):
            if market.get("ticker") != ticker:
                continue
            found = True
            for field in fields:
                if not merged.get(field):
                    value = market.get(field)
                    if value:
                        merged[field] = value
            break
    if not found:
        return None
    merged["ticker"] = ticker
    for field in fields:
        merged.setdefault(field, "")
    return merged


def _raw_dict_from_snapshot_market(market: dict, meta: dict) -> dict:
    return {
        "ticker": meta["ticker"],
        "event_ticker": meta["event_ticker"],
        "series_ticker": meta["series_ticker"],
        "category": meta["category"],
        "title": meta["title"],
        "close_time": meta["close_time"],
        "yes_bid_dollars": market.get("yes_bid_dollars"),
        "yes_ask_dollars": market.get("yes_ask_dollars"),
        "last_price_dollars": market.get("last_price_dollars"),
        "previous_price_dollars": market.get("previous_price_dollars"),
        "volume_fp": market.get("volume_fp"),
        "open_interest_fp": market.get("open_interest_fp"),
    }


def _find_candlestick(config: dict, series_ticker: str, ticker: str, as_of_dt: datetime) -> dict | None:
    """
    Most recent daily candle whose period has fully completed at or before
    as_of_dt (end_period_ts <= as_of_dt) — never a later one. A candle
    ending after as_of_dt reflects trades from after the as-of instant,
    which would leak look-ahead price information into a "historical"
    reconstruction; this was caught by testing against a real ticker
    (KXLIRRSTRIKE-26-MAY19 as of 2026-05-18: the nearest-by-time candle's
    period ran until 2026-05-18 04:00 UTC, four hours into the future
    relative to the requested instant).

    Also skips any candle with no price data (price.close_dollars missing —
    a no-trade day). Kalshi's candlestick response contains None-price
    candles for illiquid days (confirmed on a real thin market); silently
    accepting one would flow yes_bid_dollars/yes_ask_dollars=None into
    scanner.score_market(), whose `float(x or 0)` coercions turn a missing
    price into an indistinguishable 0.0 rather than the function honestly
    reporting "no data" — the same "never fabricate missing data" rule
    applied everywhere else in this module.

    Looks back up to 10 days for a qualifying candle; returns None if none
    exists (thin markets can go long stretches with no real trade).
    """
    as_of_ts = int(as_of_dt.timestamp())
    start_ts = as_of_ts - 10 * 86400
    end_ts = as_of_ts
    try:
        candles = kalshi.fetch_market_candlesticks(
            config, series_ticker, ticker, start_ts, end_ts, period_interval=1440,
        )
    except Exception:
        return None
    candidates = [
        c for c in candles
        if c.get("end_period_ts", 0) <= as_of_ts and c.get("price", {}).get("close_dollars")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c["end_period_ts"])


def _raw_dict_from_candlestick(candle: dict, meta: dict) -> dict:
    price = candle.get("price", {})
    yes_bid = candle.get("yes_bid", {})
    yes_ask = candle.get("yes_ask", {})
    return {
        "ticker": meta["ticker"],
        "event_ticker": meta["event_ticker"],
        "series_ticker": meta["series_ticker"],
        "category": meta["category"],
        "title": meta["title"],
        "close_time": meta["close_time"],
        "yes_bid_dollars": yes_bid.get("close_dollars"),
        "yes_ask_dollars": yes_ask.get("close_dollars"),
        "last_price_dollars": price.get("close_dollars"),
        "previous_price_dollars": price.get("previous_dollars"),
        "volume_fp": candle.get("volume_fp"),
        "open_interest_fp": candle.get("open_interest_fp"),
    }


def reconstruct_market_state(
    config: dict, ticker: str, as_of_date: str | datetime, db_path: str = DB_PATH,
) -> dict | None:
    """
    Reconstructs a ticker's market state as of as_of_date and scores it via
    the live scanner.score_market(), returning the same enriched dict shape
    core.scorer.score_markets() consumes, plus two extra transparency keys:
      reconstruction_tier:   "exact" | "approximate"
      reconstruction_source: snapshot filename, or "kalshi_candlestick"
    These are additional keys, not a wrapper — score_market()'s own callers
    only .get() the fields they know about, so they're ignored downstream,
    same as any other enrichment key (whale_data, poly, ...) that may or
    may not be present.

    Returns None if the ticker is unknown, or if neither source tier has
    price data for it as of that date.
    """
    as_of_dt = _parse_dt(as_of_date)

    meta = _static_metadata(ticker, db_path)
    if meta is None:
        return None

    snap_hit = _find_snapshot_market(ticker, as_of_dt)
    if snap_hit is not None:
        market, snapshot_name = snap_hit
        raw = _raw_dict_from_snapshot_market(market, meta)
        tier, source = "exact", snapshot_name
    else:
        series_ticker = meta.get("series_ticker")
        candle = _find_candlestick(config, series_ticker, ticker, as_of_dt) if series_ticker else None
        if candle is None:
            return None
        raw = _raw_dict_from_candlestick(candle, meta)
        tier, source = "approximate", "kalshi_candlestick"

    close_time_str = raw.get("close_time")
    if close_time_str:
        try:
            close_dt = _parse_dt(close_time_str)
            raw["time_horizon"] = scanner.classify_time_horizon(close_dt, as_of_dt)
        except ValueError:
            pass

    enriched = scanner.score_market(raw, config)
    enriched["reconstruction_tier"] = tier
    enriched["reconstruction_source"] = source
    enriched["reconstruction_as_of"] = as_of_dt.isoformat()
    return enriched


def main():
    parser = argparse.ArgumentParser(description="Reconstruct a market's as-of state and score it.")
    parser.add_argument("ticker")
    parser.add_argument("as_of_date", help="ISO date/datetime, e.g. 2026-06-20")
    args = parser.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.json"), encoding="utf-8") as f:
        config = json.load(f)

    result = reconstruct_market_state(config, args.ticker, args.as_of_date)
    if result is None:
        print(f"No reconstructable state for {args.ticker} as of {args.as_of_date}")
        return
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
