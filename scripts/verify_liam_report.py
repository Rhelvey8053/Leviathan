"""
scripts/verify_liam_report.py -- triage tool for Liam (monday.com's built-in
PM agent) reports against Leviathan's own real state.

Liam posts to the "Leviathan Sync Log" item (config.json monday.log_item_id)
on a recurring cadence. It's genuinely useful for external research (market/
regulatory intelligence it gathers fresh) but unreliable on internal state:
confirmed twice (2026-08-18, 2026-08-19) that it conflates "depends_on is
satisfied" with "the item's real trigger metric is met", and it has no
visibility into policy decisions made in conversation (e.g. the user's
explicit no-metered-API-spend call re-blocking replay-instrument-validation
via a sentinel trigger) -- it just re-recommends unblocking those items every
report. It also sometimes has no live DB access at all ("no live DB access"
appeared verbatim in the 2026-08-19 report) and its "Automated Actions Taken"
section has been observed narrating state Claude/the user already wrote,
not necessarily actions Liam itself performed.

This script never trusts Liam's claims directly. It fetches Liam's latest
post (for a human/Claude to read the actual language), then independently
computes ground truth for every locked/blocked backlog item from the real
DB and backlog.json -- the same computation backlog/checker.py already does
for status, made explicit per-item here so a "stale block" claim can be
checked in one pass instead of re-deriving it by hand each time.

Usage:
    python scripts/verify_liam_report.py
"""
import io
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))
from backlog import checker
from scripts import monday_sync as ms

LOG_ITEM_ID = "12828205953"  # config.json monday.log_item_id
BACKLOG_PATH = Path(__file__).parent.parent / "backlog" / "backlog.json"

_OP_FNS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
           "==": lambda a, b: a == b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def fetch_latest_liam_post() -> dict | None:
    data = ms.gql(
        """
        query($item_id: [ID!]) {
          items(ids: $item_id) {
            updates(limit: 10) { id body text_body created_at creator { name } }
          }
        }
        """,
        {"item_id": [LOG_ITEM_ID]},
    )
    updates = data["items"][0]["updates"]
    for u in updates:
        if "Liam" in (u.get("text_body") or "") or "Liam" in (u.get("body") or ""):
            return u
    return None


def ground_truth_table(backlog: dict, metrics: dict) -> list[dict]:
    items_by_id = {i["id"]: i for i in backlog["items"]}
    rows = []
    for item in backlog["items"]:
        if item["status"] not in ("locked", "blocked"):
            continue
        conds = item.get("trigger", {}).get("all", [])
        cond_results = []
        sentinel = False
        data_gaps = metrics.get("_data_gaps", [])
        for c in conds:
            val = metrics.get(c["metric"])
            if c["metric"] not in checker.METRICS_KEYS:
                sentinel = True
                cond_results.append(f"{c['metric']} {c['op']} {c['value']} [NEVER COMPUTED -- policy/human gate]")
                continue
            met = _OP_FNS[c["op"]](val, c["value"])
            gap_note = " [DATA NOT TRACKED YET, not a real 0 -- see backlog: smart-money-fills-table-missing]" \
                if c["metric"] in data_gaps else ""
            cond_results.append(f"{c['metric']}={val} {c['op']} {c['value']} -> {'MET' if met else 'not met'}{gap_note}")
        deps = item.get("depends_on", [])
        dep_results = [(d, items_by_id.get(d, {}).get("status", "MISSING")) for d in deps]
        deps_done = all(s == "done" for _, s in dep_results)
        trigger_met = all(
            (c["metric"] in checker.METRICS_KEYS and _OP_FNS[c["op"]](metrics.get(c["metric"]), c["value"]))
            for c in conds
        ) if conds else True
        really_unlockable = deps_done and trigger_met and not sentinel
        rows.append({
            "id": item["id"],
            "status": item["status"],
            "depends_on": dep_results,
            "deps_done": deps_done,
            "trigger_conditions": cond_results,
            "sentinel_gate": sentinel,
            "really_unlockable_now": really_unlockable,
        })
    return rows


def main():
    post = fetch_latest_liam_post()
    backlog = json.loads(BACKLOG_PATH.read_text(encoding="utf-8"))
    metrics = checker.compute_metrics()

    print("=" * 78)
    print("LATEST LIAM POST" + (f" ({post['created_at']})" if post else " -- none found"))
    print("=" * 78)
    if post:
        print(post["text_body"])
    print()

    print("=" * 78)
    print("GROUND TRUTH -- every locked/blocked item, computed fresh from real DB + backlog.json")
    print("=" * 78)
    for row in ground_truth_table(backlog, metrics):
        flag = "*** REALLY UNLOCKABLE NOW ***" if row["really_unlockable_now"] else ""
        print(f"\n{row['id']}  [{row['status']}]  {flag}")
        if row["depends_on"]:
            for dep_id, dep_status in row["depends_on"]:
                mark = "OK" if dep_status == "done" else "NOT DONE"
                print(f"    depends_on: {dep_id} ({dep_status}) [{mark}]")
        for c in row["trigger_conditions"]:
            print(f"    trigger: {c}")
        if row["sentinel_gate"]:
            print("    NOTE: gated behind a sentinel metric -- requires an explicit human decision, "
                  "not something that will ever clear on its own. Check the item's own notes field for why.")
        if not row["depends_on"] and not row["trigger_conditions"]:
            print("    (no depends_on, no trigger -- blocked status is manual/notes-only)")

    print("\n" + "=" * 78)
    print("If Liam's post recommends moving an item to Ready, cross-check its id against the "
          "table above: only items marked '*** REALLY UNLOCKABLE NOW ***' actually qualify.")


if __name__ == "__main__":
    main()
