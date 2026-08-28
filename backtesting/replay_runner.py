"""
backtesting/replay_runner.py — replay-runner.

Drives Claude scoring (Pro/CLI backend, same as the live daily pipeline --
see 2026-08-26 note below) over the settled-market corpus built by
replay-settled-fetcher, using replay-asof-reconstruction to build each
market's historical input, then grades each replayed score against the
now-known settled outcome.

LOOK-AHEAD CONTAMINATION — permanent, structural, not something this module
fixes: Claude's training data and any live web search may already know how
a replayed market resolved, since it's in the past relative to both. A
replayed hit rate is NOT a clean estimate of live out-of-sample performance
for this reason. Schema separation (results write to `replay_signals`, a
table this module is the only writer of, exported to its own CSVs, never
joined with the live `signals` table) protects downstream analysis from
being silently fooled into pooling the two populations — it does not, and
cannot, remove the contamination itself.

AS-OF DATE SELECTION: to avoid trivially "backtesting" a market that's
already been telegraphed by consensus price (e.g. scoring a market at 0.99
right before it settles YES), each candidate is reconstructed at one of
several lookback windows before its close_time (furthest-from-close tried
first), and skipped entirely if no candidate's reconstructed mid_price
falls within config.markets.min_market_price/max_market_price — the same
price band core.scanner.filter_markets() already applies on the live
pipeline. This reuses an existing, already-defensible threshold rather than
inventing a new one; verified against real settled_markets price
trajectories (via candlesticks) before this rule was chosen — some tickers
show genuine multi-week uncertainty before close, others are already
near-certain days out, and very short-lived markets may have no
reconstructable state at typical lookback windows at all.

BOUNDED AND RESUMABLE: originally forced config.llm.backend="api" so
core.llm's daily cost ceiling could bound a run in real dollars — the
CLI/Pro-subscription path has no cost concept, so that specific ceiling
can't apply to it. 2026-08-26: switched to backend="cli" at the user's
explicit request, after confirming the API requirement was purely a
side effect of that $-based safety mechanism, not something the
validation task itself needs — the grading instrument (Brier scoring,
edge-case handling) tests the SCORES Claude returns, not which billing
path produced them, and market-baseline-brier/replay-runner's own
correctness were already validated independent of backend (see this
item's own backlog notes). The LLMCostCeilingExceeded catch below is now
inert in practice (CLI never raises it) but left in place rather than
removed, in case backend is ever reverted to "api" for a specific reason.
Boundedness now comes from `max_markets` per invocation alone, same as
this module already relied on primarily -- still processes at most
`max_markets` newly-scored tickers per call and persists idempotently
(INSERT OR IGNORE keyed on ticker). Skipped candidates (no reconstructable
data, or already-telegraphed at every lookback) are not persisted, so a
re-run may re-attempt them — wasted local computation, but never wasted
spend, since skips are decided before any scoring call. Trade-off worth
knowing: this now draws on the same shared Claude Pro usage the live
daily pipeline uses, not a separate, unlimited resource -- large
`max_markets` values compete with the live pipeline's own usage, unlike
the old $-metered path which was a fully separate budget.

KNOWN SIMPLIFICATION: main.py applies additional post-hoc confidence
downgrade rules (HIGH -> MED below min_high_confidence_edge, short-horizon
HIGH requires corroboration) on top of Claude's raw response before it
becomes a live signal. This module grades Claude's raw response as
returned, without replicating those downstream business rules — extracting
them into a shared helper would mean touching main.py's live path, a larger
change than this item's scope.

Usage:
    python -m backtesting.replay_runner [--max-markets N] [--report PATH]
"""

import argparse
import csv
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from backtesting import asof_reconstruction as asof
from backtesting.harness import BacktestRunner
from core import scorer
from core.llm import LLMCostCeilingExceeded
from core.logger import DB_PATH

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MAX_MARKETS = 5
# Furthest-from-close tried first — a market is more likely to still be
# genuinely uncertain the earlier before its close it's examined.
LOOKBACK_DAYS_CANDIDATES = (30, 14, 7, 3, 1)


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
    """Creates replay_signals if absent. Never touches signals or runs."""
    with _db(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS replay_signals (
                ticker              TEXT PRIMARY KEY,
                as_of_date          TEXT,
                close_time          TEXT,
                reconstruction_tier TEXT,
                direction           TEXT,
                confidence          TEXT,
                edge                REAL,
                reasoning           TEXT,
                flag_path           TEXT,
                time_horizon        TEXT,
                resolved_yes        INTEGER,
                hit                 INTEGER,
                scored_at           TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_replay_signals_close ON replay_signals(close_time);
        """)


def _candidate_tickers(db_path: str, limit: int) -> list[dict]:
    """
    Settled tickers not yet in replay_signals, restricted to rows with a
    series_ticker (required for the candlestick reconstruction tier —
    series_ticker is always populated on settled_markets rows in practice,
    but the guard costs nothing and documents the real requirement).
    """
    with _db(db_path) as conn:
        rows = conn.execute("""
            SELECT ticker, series_ticker, close_time, result
            FROM settled_markets
            WHERE series_ticker != ''
              AND ticker NOT IN (SELECT ticker FROM replay_signals)
            ORDER BY RANDOM()
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def _price_in_band(mid_price, config: dict) -> bool:
    cfg = config.get("markets", {})
    lo = cfg.get("min_market_price", 0.05)
    hi = cfg.get("max_market_price", 0.95)
    return mid_price is not None and lo <= mid_price <= hi


def _find_reconstructable_as_of(config: dict, ticker: str, close_time_str: str, db_path: str) -> dict | None:
    """
    Tries lookback windows furthest-from-close first, returns the first
    reconstruction whose mid_price is still within the live pipeline's own
    price band (not already telegraphed by consensus) — or None if no
    candidate qualifies.
    """
    try:
        close_dt = asof._parse_dt(close_time_str)
    except (ValueError, TypeError):
        return None

    for days in LOOKBACK_DAYS_CANDIDATES:
        as_of_dt = close_dt - timedelta(days=days)
        enriched = asof.reconstruct_market_state(config, ticker, as_of_dt, db_path)
        if enriched is None:
            continue
        if _price_in_band(enriched.get("mid_price"), config):
            return enriched
    return None


def _row_from_scored(enriched: dict, cs: dict, result: str, as_of_dt: datetime) -> dict:
    direction = (cs.get("direction") or "").strip().upper()
    resolved_yes = result == "YES"
    hit = None
    if direction in ("YES", "NO"):
        hit = (direction == "YES" and resolved_yes) or (direction == "NO" and not resolved_yes)
    return {
        "ticker":              enriched["ticker"],
        "as_of_date":          as_of_dt.isoformat(),
        "close_time":          enriched.get("close_time"),
        "reconstruction_tier": enriched.get("reconstruction_tier"),
        "direction":           direction,
        "confidence":          (cs.get("confidence") or "").strip().upper(),
        "edge":                cs.get("edge"),
        "reasoning":           cs.get("reasoning", ""),
        "flag_path":           enriched.get("flag_path"),
        "time_horizon":        enriched.get("time_horizon"),
        "resolved_yes":        int(resolved_yes),
        "hit":                 None if hit is None else int(hit),
        "scored_at":           datetime.now(timezone.utc).isoformat(),
    }


def run_replay(config: dict, max_markets: int = DEFAULT_MAX_MARKETS, db_path: str = DB_PATH) -> dict:
    """
    Scores up to `max_markets` new settled tickers via the Claude Pro/CLI
    backend (2026-08-26, was the metered API backend -- see module
    docstring) and persists results to replay_signals. Returns a summary
    dict: candidates_considered / skipped_no_data / scored / ceiling_stopped.
    """
    _init_table(db_path)
    replay_config = {**config, "llm": {**config.get("llm", {}), "backend": "cli"}}

    summary = {
        "candidates_considered": 0,
        "skipped_no_data":       0,
        "scored":                0,
        "scoring_errors":        0,
        "ceiling_stopped":       False,
    }

    pool = _candidate_tickers(db_path, max_markets * 10)
    for candidate in pool:
        if summary["scored"] >= max_markets:
            break
        summary["candidates_considered"] += 1
        ticker = candidate["ticker"]

        enriched = _find_reconstructable_as_of(replay_config, ticker, candidate["close_time"], db_path)
        if enriched is None:
            summary["skipped_no_data"] += 1
            continue

        as_of_dt = asof._parse_dt(enriched["reconstruction_as_of"])
        try:
            scored, _token_info = scorer.score_markets([enriched], replay_config, now=as_of_dt)
        except LLMCostCeilingExceeded:
            summary["ceiling_stopped"] = True
            break
        except Exception as e:
            # 2026-08-28: a single market's malformed CLI response (see
            # core.scorer._score_via_cli's type-check fix, same date) used
            # to crash this entire batch via an uncaught exception -- every
            # other candidate this run would have scored, and every dollar
            # of Pro/CLI usage already spent scoring them, was lost. One bad
            # response shouldn't cost the whole batch: count it and move on,
            # same resilience `skipped_no_data` already gives a
            # can't-reconstruct candidate.
            print(f"[replay_runner] scoring error on {ticker}, skipping: {e}")
            summary["scoring_errors"] += 1
            continue

        if not scored:
            summary["skipped_no_data"] += 1
            continue

        row = _row_from_scored(enriched, scored[0], candidate["result"], as_of_dt)
        with _db(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO replay_signals "
                "(ticker, as_of_date, close_time, reconstruction_tier, direction, "
                " confidence, edge, reasoning, flag_path, time_horizon, resolved_yes, hit, scored_at) "
                "VALUES (:ticker, :as_of_date, :close_time, :reconstruction_tier, :direction, "
                ":confidence, :edge, :reasoning, :flag_path, :time_horizon, :resolved_yes, :hit, :scored_at)",
                row,
            )
        summary["scored"] += 1

    return summary


def export_and_report(db_path: str = DB_PATH, report_path: str | None = None) -> str:
    """
    Exports replay_signals to its own CSV pair (kept in data/replay_export/,
    never mixed into data/powerbi_export/ — schema separation, not just a
    different filename) and runs the existing, unmodified
    backtesting.harness.BacktestRunner over them.
    """
    with _db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM replay_signals WHERE direction IS NOT NULL AND direction != ''"
        ).fetchall()
        rows = [dict(r) for r in rows]

    export_dir = os.path.join(_ROOT, "data", "replay_export")
    os.makedirs(export_dir, exist_ok=True)
    signals_csv     = os.path.join(export_dir, "replay_signals.csv")
    resolutions_csv = os.path.join(export_dir, "replay_resolutions.csv")

    signal_fields = ["ticker", "direction", "edge", "confidence", "flag_path", "time_horizon"]
    with open(signals_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=signal_fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in signal_fields})

    with open(resolutions_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "resolved_yes", "close_date"])
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "ticker":       r["ticker"],
                "resolved_yes": "true" if r["resolved_yes"] else "false",
                "close_date":   (r.get("close_time") or "")[:10],
            })

    report_path = report_path or os.path.join(_ROOT, "reports", "replay_backtest_report.txt")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    runner = BacktestRunner()
    runner.load_signals(signals_csv)
    runner.load_resolutions(resolutions_csv)
    runner.match_signals_to_resolutions()
    runner.report(report_path)
    return report_path


def main():
    parser = argparse.ArgumentParser(description="Replay-score the settled-market corpus and grade it.")
    parser.add_argument("--max-markets", type=int, default=DEFAULT_MAX_MARKETS, metavar="N")
    parser.add_argument("--report", default=None, metavar="PATH")
    args = parser.parse_args()

    with open(os.path.join(_ROOT, "config.json"), encoding="utf-8") as f:
        config = json.load(f)

    summary = run_replay(config, max_markets=args.max_markets)
    print(json.dumps(summary, indent=2))

    report_path = export_and_report(report_path=args.report)
    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
