"""
One-off script: writes recovered true completion dates (from
_recover_completion_dates.py) into the monday board's unused "Completed On"
column (date_mm6agkax) -- distinct from "Completed date" (date_mm6b6w9a),
which scripts/monday_sync.py's phase2_sync owns and stamps only on a
genuine live ready/locked/blocked -> done transition observed during a
sync run. This script never touches that column.

Usage: python -m scripts._backfill_completed_on_dates [--live]
Defaults to dry-run (prints what would be written, no mutation).
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts import monday_sync as ms

BOARD_ID = 18426940027
COMPLETED_ON_COL = "date_mm6agkax"


def load_dates() -> dict:
    proc = __import__("subprocess").run(
        [sys.executable, str(Path(__file__).parent / "_recover_completion_dates.py")],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    dates = load_dates()
    items = ms.get_all_items(BOARD_ID)
    col_ids = ms._resolve_column_ids(ms.get_board_schema(BOARD_ID))
    backlog_id_col = col_ids["backlog_id"]

    by_backlog_id = {}
    for it in items:
        cols = {c["id"]: c.get("text") for c in it.get("column_values", [])}
        bid = cols.get(backlog_id_col)
        if bid:
            by_backlog_id[bid] = {"item_id": it["id"], "existing_completed_on": cols.get(COMPLETED_ON_COL)}

    to_write = []
    missing_on_board = []
    for backlog_id, info in dates.items():
        board = by_backlog_id.get(backlog_id)
        if board is None:
            missing_on_board.append(backlog_id)
            continue
        to_write.append((backlog_id, board["item_id"], info["date"], info["source"], board["existing_completed_on"]))

    print(f"{len(to_write)} items to write, {len(missing_on_board)} not found on board: {missing_on_board}")
    for backlog_id, item_id, dt, source, existing in to_write:
        marker = " (OVERWRITING existing value!)" if existing else ""
        print(f"  {backlog_id:45s} -> {dt}  [{source}]{marker}")

    if not args.live:
        print("\nDry run only -- pass --live to write.")
        return

    for backlog_id, item_id, dt, source, existing in to_write:
        ms.gql(
            """
            mutation($board_id: ID!, $item_id: ID!, $column_values: JSON!) {
              change_multiple_column_values(board_id: $board_id, item_id: $item_id,
                column_values: $column_values, create_labels_if_missing: true) { id }
            }
            """,
            {
                "board_id": str(BOARD_ID),
                "item_id": item_id,
                "column_values": json.dumps({COMPLETED_ON_COL: {"date": dt}}),
            },
        )
        time.sleep(0.3)
    print(f"\nWrote {len(to_write)} Completed On dates live.")


if __name__ == "__main__":
    main()
