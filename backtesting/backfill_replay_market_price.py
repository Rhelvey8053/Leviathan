"""
backtesting/backfill_replay_market_price.py — one-shot backfill for
replay_signals rows that predate the market_price/our_estimate schema fix
(commit cdba484, 2026-09-03).

Two zombie backtesting.replay_runner processes (PIDs 25380/12888, running
since 2026-09-01 on pre-fix code loaded before that commit) wrote ~300 rows
before the fix landed, all missing the two new columns even though the
pipeline had everything needed to derive them after the fact:

  market_price: re-derivable deterministically via
  backtesting.asof_reconstruction.reconstruct_market_state() using the
  ticker + as_of_date already stored on each row — no new Claude call,
  since market state is objective historical fact, not a judgment call.
  Only backfilled when re-reconstruction returns the SAME
  reconstruction_tier already recorded on the row, so a snapshot that has
  since been pruned (exact -> approximate drift) can't silently swap in a
  different data source than what the original scoring call actually saw.

  our_estimate: recovered algebraically from the already-stored edge and
  direction, using core.scorer's own invariant (RESPONSE_SCHEMA/
  _aggregate_multi_sample): edge = abs(our_estimate - market_price).
      direction == YES: our_estimate = market_price + edge
      direction == NO:  our_estimate = market_price - edge
  PASS rows are skipped for our_estimate — direction doesn't fix a sign
  for them. Clamped to [0, 1]; a result outside that range means the
  reconstructed market_price doesn't actually match what the original
  scoring call saw (tier drift despite the tier label matching, or a
  stale edge), so that row's our_estimate is left NULL rather than
  writing a nonsensical value. market_price itself is still applied in
  that case — it doesn't depend on the clamp check.

Never touches direction/confidence/edge/reasoning/hit — those were always
correct; only the two backfilled columns are new information.

Usage:
    python -m backtesting.backfill_replay_market_price [--dry-run]
"""

import argparse
import sqlite3

from backtesting import asof_reconstruction as asof
from backtesting.replay_runner import _db
from core.logger import DB_PATH


def backfill(config: dict, db_path: str = DB_PATH, dry_run: bool = False) -> dict:
    summary = {
        "total_missing":        0,
        "market_price_filled":  0,
        "our_estimate_filled":  0,
        "skipped_no_reconstruction": 0,
        "skipped_tier_mismatch":     0,
        "skipped_estimate_out_of_range": 0,
    }

    with _db(db_path) as conn:
        rows = conn.execute(
            "SELECT ticker, as_of_date, direction, edge, reconstruction_tier "
            "FROM replay_signals WHERE market_price IS NULL"
        ).fetchall()
        rows = [dict(r) for r in rows]

    summary["total_missing"] = len(rows)

    for row in rows:
        ticker = row["ticker"]
        enriched = asof.reconstruct_market_state(config, ticker, row["as_of_date"], db_path)
        if enriched is None or enriched.get("mid_price") is None:
            summary["skipped_no_reconstruction"] += 1
            continue

        if enriched.get("reconstruction_tier") != row["reconstruction_tier"]:
            summary["skipped_tier_mismatch"] += 1
            continue

        market_price = float(enriched["mid_price"])
        our_estimate = None

        direction = (row["direction"] or "").strip().upper()
        edge = row["edge"]
        if direction in ("YES", "NO") and edge is not None:
            candidate = market_price + edge if direction == "YES" else market_price - edge
            if 0.0 <= candidate <= 1.0:
                our_estimate = round(candidate, 4)
            else:
                summary["skipped_estimate_out_of_range"] += 1

        if not dry_run:
            with _db(db_path) as conn:
                conn.execute(
                    "UPDATE replay_signals SET market_price = ?, our_estimate = ? WHERE ticker = ?",
                    (round(market_price, 4), our_estimate, ticker),
                )

        summary["market_price_filled"] += 1
        if our_estimate is not None:
            summary["our_estimate_filled"] += 1

    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report counts without writing.")
    args = parser.parse_args()

    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.json"), encoding="utf-8") as f:
        config = json.load(f)

    summary = backfill(config, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
