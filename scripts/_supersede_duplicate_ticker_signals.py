"""
scripts/_supersede_duplicate_ticker_signals.py — one-off cleanup, 2026-08-20.

3 tickers, ever, got more than one non-PASS signal row logged because the
old 7-day repeat-dedup window (widened to 30 in this same session) let the
same still-open market re-flag as an "independent" signal weeks apart:

  KXCABLEAVE-26MAY22-26AUG            3 rows, all YES, all resolved LOSS
  KXALBUMRELEASEDATEBEY-NEW-JAN01-27  3 rows, all NO, still pending
  KXMLBDEBUT-KANDERSON-26NOV01        2 rows, NO then YES (8 days apart),
                                       still pending -- genuinely conflicting

Every stats function that matters (core.logger's ~17 _PAPER-filtered
queries, backlog/checker.py's compute_metrics()) counts each row as an
independent resolved judgment. It isn't: it's the same market re-scored,
so resolved_count (every locked backlog item's gate) is currently
inflated by non-independent samples, and once the two still-pending
tickers resolve, each will land as one win AND one loss for the same
real-world event by construction.

Fix: mark every row EXCEPT the latest (highest timestamp) per affected
ticker with source='superseded_paper' instead of 'paper'. This is the
existing discriminator every _PAPER-filtered query already checks
(source = 'paper' OR source IS NULL) -- a single-column change excludes
these rows from every stats function at once, with no need to touch each
query individually, and NOTHING is deleted: the full history stays in
the table, queryable via WHERE source='superseded_paper'.

Usage:
    python scripts/_supersede_duplicate_ticker_signals.py            # dry-run
    python scripts/_supersede_duplicate_ticker_signals.py --apply    # write
"""
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT     = Path(__file__).parent.parent
DB_PATH  = ROOT / "data" / "leviathan.db"
BAK_PATH = ROOT / "data" / "leviathan.db.bak_supersede_dupes"

# (ticker, [call_ids to supersede], call_id to KEEP as source='paper')
SUPERSEDE = [
    ("KXCABLEAVE-26MAY22-26AUG",
     ["633ad093", "6eaf940f"], "1e984bee"),
    ("KXALBUMRELEASEDATEBEY-NEW-JAN01-27",
     ["166a056d", "2c5fc4cd"], "48212cb6"),
    ("KXMLBDEBUT-KANDERSON-26NOV01",
     ["ad083472"], "c9067fa4"),
]


def run(apply: bool = False) -> int:
    if not DB_PATH.exists():
        print(f"ERROR: database not found at {DB_PATH}")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    all_supersede_ids = [cid for _, ids, _ in SUPERSEDE for cid in ids]
    rows = conn.execute(
        f"SELECT call_id, ticker, timestamp, direction, source, result "
        f"FROM signals WHERE call_id IN ({','.join('?' * len(all_supersede_ids))})",
        all_supersede_ids,
    ).fetchall()
    found = {r["call_id"]: dict(r) for r in rows}

    print(f"{'call_id':10s} {'ticker':38s} {'timestamp':28s} {'dir':4s} {'source (before)':16s} result")
    missing = []
    for ticker, ids, keep_id in SUPERSEDE:
        for cid in ids:
            r = found.get(cid)
            if r is None:
                missing.append(cid)
                continue
            print(f"{cid:10s} {r['ticker']:38s} {r['timestamp']:28s} {r['direction']:4s} {r['source'] or '(null)':16s} {r['result'] or ''}")
        keep = conn.execute("SELECT call_id, timestamp, direction, result FROM signals WHERE call_id = ?", (keep_id,)).fetchone()
        if keep:
            print(f"  -> keeping {keep['call_id']} ({keep['timestamp']}, {keep['direction']}, result={keep['result'] or 'pending'}) as source='paper'")
        else:
            missing.append(keep_id)

    if missing:
        print(f"\nERROR: call_id(s) not found in DB, aborting: {missing}")
        return 1

    if not apply:
        print(f"\nDry run only -- {len(all_supersede_ids)} rows would be marked source='superseded_paper'. Pass --apply to write.")
        return 0

    shutil.copy(DB_PATH, BAK_PATH)
    print(f"\nBacked up DB to {BAK_PATH}")

    with conn:
        conn.execute("BEGIN")
        for cid in all_supersede_ids:
            conn.execute("UPDATE signals SET source = 'superseded_paper' WHERE call_id = ?", (cid,))
    print(f"Updated {len(all_supersede_ids)} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(run(apply="--apply" in sys.argv))
