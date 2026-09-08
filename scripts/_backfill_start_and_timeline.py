"""
One-off script: writes real Start Date (all items) and Timeline span (done
items only) to the monday board, recovered from backlog/backlog.json's git
history via _recover_completion_dates.py.

Start Date = the earlier of (date first tracked in backlog.json, Completed
On date) -- clamped so a backfilled-after-the-fact item never gets an
inverted/negative-duration bar. Timeline {"from","to"} is only written for
done items, where both real endpoints are known; not-done items get a
Start Date but no fabricated end.

Usage: python -m scripts._backfill_start_and_timeline [--live]
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts import monday_sync as ms

BOARD_ID = 18426940027
START_DATE_COL = "date_mm6avkdz"
TIMELINE_COL = "timerange_mm6a8aaz"
SCRIPT_DIR = Path(__file__).parent


def run(*args):
    proc = subprocess.run([sys.executable, str(SCRIPT_DIR / "_recover_completion_dates.py"), *args],
                           capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()

    started = run("--started")
    completed = run()  # {id: {"date":..., "source":...}} for done items only

    with open(SCRIPT_DIR.parent / "backlog" / "backlog.json", encoding="utf-8") as f:
        backlog = json.load(f)
    status_by_id = {i["id"]: i["status"] for i in backlog["items"]}

    plan = {}  # backlog_id -> {"start":..., "end": ... or None}
    for iid, start in started.items():
        if status_by_id.get(iid) == "done" and iid in completed:
            end = completed[iid]["date"]
            plan[iid] = {"start": min(start, end), "end": end}
        else:
            plan[iid] = {"start": start, "end": None}

    items = ms.get_all_items(BOARD_ID)
    col_ids = ms._resolve_column_ids(ms.get_board_schema(BOARD_ID))
    backlog_id_col = col_ids["backlog_id"]
    by_backlog_id = {}
    for it in items:
        cols = {c["id"]: c.get("text") for c in it.get("column_values", [])}
        bid = cols.get(backlog_id_col)
        if bid:
            by_backlog_id[bid] = it["id"]

    missing = [iid for iid in plan if iid not in by_backlog_id]
    print(f"{len(plan)} items planned, {len(missing)} not found on board: {missing}")
    for iid, p in sorted(plan.items()):
        span = f"{p['start']} -> {p['end']}" if p["end"] else f"{p['start']} (start only)"
        print(f"  {iid:40s} {span}")

    if not args.live:
        print("\nDry run only -- pass --live to write.")
        return

    for iid, p in plan.items():
        item_id = by_backlog_id.get(iid)
        if item_id is None:
            continue
        cvs = {START_DATE_COL: {"date": p["start"]}}
        if p["end"]:
            cvs[TIMELINE_COL] = {"from": p["start"], "to": p["end"]}
        ms.gql(
            """
            mutation($board_id: ID!, $item_id: ID!, $column_values: JSON!) {
              change_multiple_column_values(board_id: $board_id, item_id: $item_id,
                column_values: $column_values, create_labels_if_missing: true) { id }
            }
            """,
            {"board_id": str(BOARD_ID), "item_id": item_id, "column_values": json.dumps(cvs)},
        )
        time.sleep(0.3)
    print(f"\nWrote Start Date/Timeline for {len(plan) - len(missing)} items live.")


if __name__ == "__main__":
    main()
