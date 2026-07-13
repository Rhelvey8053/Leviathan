"""
scripts/verify_pnl.py — Read-only PnL integrity check (Goal 5b Part A).

Selects all resolved signals (outcome IS NOT NULL), recomputes pnl_if_traded
from stored fields using the current logger.py formula, and prints a diff table.

If any delta != 0: prints before/after, backs up leviathan.db to
leviathan.db.bak_pnlfix, and applies a one-time UPDATE inside a transaction.
If all deltas == 0: prints confirmation and exits without writing anything.

Usage:
    python scripts/verify_pnl.py [--apply]

    --apply   Actually write the backfill if deltas found (default: dry-run).
              Without --apply, a non-zero delta set exits with code 1 so you
              can inspect before committing.
"""

import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT    = Path(__file__).parent.parent
DB_PATH = ROOT / "leviathan.db"
BAK_PATH = ROOT / "leviathan.db.bak_pnlfix"


def _recompute_pnl(direction: str, price: float, entry_price, fill_count, fill_fee, outcome: str) -> float:
    """Recompute pnl_if_traded using the current logger.resolve_outcomes formula."""
    win = (outcome == direction)  # e.g. direction="YES", outcome="YES" → win
    if entry_price is not None:
        p            = float(entry_price)
        fill_count   = float(fill_count or 1)
        fee_per_unit = float(fill_fee or 0) / fill_count
    else:
        p            = float(price or 0)
        fee_per_unit = 0.0

    if direction == "YES":
        pnl = round(((1.0 - p) if win else -p) - fee_per_unit, 4)
    elif direction == "NO":
        pnl = round((p if win else -(1.0 - p)) - fee_per_unit, 4)
    else:
        pnl = 0.0
    return pnl


def run(apply: bool = False) -> int:
    if not DB_PATH.exists():
        print(f"ERROR: database not found at {DB_PATH}")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT call_id, ticker, direction, market_price, entry_price,
               fill_count, fill_fee, outcome, result, pnl_if_traded
        FROM signals
        WHERE outcome IS NOT NULL AND outcome != ''
    """).fetchall()

    print(f"Checking {len(rows)} resolved signal(s)...")

    diffs = []
    for row in rows:
        stored = row["pnl_if_traded"]
        if stored is None:
            stored = None
        recomputed = _recompute_pnl(
            direction   = row["direction"],
            price       = row["market_price"],
            entry_price = row["entry_price"],
            fill_count  = row["fill_count"],
            fill_fee    = row["fill_fee"],
            outcome     = row["outcome"],
        )
        delta = round((recomputed - (stored or 0)), 6)
        if abs(delta) > 1e-9:
            diffs.append({
                "call_id":      row["call_id"],
                "ticker":       row["ticker"],
                "direction":    row["direction"],
                "outcome":      row["outcome"],
                "stored_pnl":   stored,
                "recomputed":   recomputed,
                "delta":        delta,
            })

    if not diffs:
        print("OK: All pnl_if_traded values are correct -- no backfill needed.")
        conn.close()
        return 0

    # Print diff table
    print(f"\n{'call_id':<12}  {'ticker':<44}  {'dir':<4}  {'out':<4}  {'stored':>8}  {'recomputed':>10}  {'delta':>8}")
    print("-" * 100)
    for d in diffs:
        print(
            f"{d['call_id']:<12}  {d['ticker']:<44}  {d['direction']:<4}  {d['outcome']:<4}  "
            f"{str(d['stored_pnl']):>8}  {d['recomputed']:>10.4f}  {d['delta']:>+8.6f}"
        )
    print(f"\n{len(diffs)} row(s) with non-zero delta.")

    if not apply:
        print("\nDry-run mode. Run with --apply to write the backfill.")
        conn.close()
        return 1

    # Backup DB before writing
    print(f"\nBacking up {DB_PATH.name} → {BAK_PATH.name}...")
    shutil.copy2(str(DB_PATH), str(BAK_PATH))
    print(f"Backup written: {BAK_PATH}")

    # Apply backfill in a single transaction
    print("Applying backfill...")
    with conn:
        for d in diffs:
            conn.execute(
                "UPDATE signals SET pnl_if_traded = ? WHERE call_id = ?",
                (d["recomputed"], d["call_id"]),
            )
    print(f"OK: Backfill applied -- {len(diffs)} row(s) updated.")
    conn.close()
    return 0


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    sys.exit(run(apply=apply))
