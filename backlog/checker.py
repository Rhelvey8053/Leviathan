"""
backlog/checker.py - Weekly backlog checker for Leviathan.

Reads backlog.json, computes live metrics from leviathan.db, evaluates
locked item triggers, prompts the user (CLI mode) or formats an email
block (--email mode), regenerates BACKLOG.md.

Usage:
  python backlog/checker.py           # CLI prompt mode
  python backlog/checker.py --email   # email block mode (no writes to backlog.json)

Windows Task Scheduler (weekly, Monday 08:00):
  schtasks /create /tn "LeviathanBacklogChecker" /tr
  "python C:\\Users\\Administrator\\Downloads\\Leviathan\\backlog\\checker.py --email"
  /sc weekly /d MON /st 08:00

Manual run:
  python backlog/checker.py          (CLI prompt mode)
  python backlog/checker.py --email  (email block mode)
"""

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

PKG_DIR   = Path(__file__).resolve().parent
REPO_ROOT = PKG_DIR.parent
DEFAULT_BACKLOG = PKG_DIR / "backlog.json"
DEFAULT_DB = REPO_ROOT / "data" / "leviathan.db"
BACKLOG_MD = REPO_ROOT / "BACKLOG.md"

sys.path.insert(0, str(REPO_ROOT))
from backlog.engine import load_backlog, save_backlog, determine_status

METRICS_KEYS = [
    "resolved_count",
    "resolved_count_per_category_max",
    "resolved_count_per_wallet_max",
    "fills_count",
]


# ---------------------------------------------------------------------------
# Metrics engine
# ---------------------------------------------------------------------------

def compute_metrics(db_path=DEFAULT_DB) -> dict:
    """Read live metrics from leviathan.db (read-only). Returns dict of counts."""
    metrics = {k: 0 for k in METRICS_KEYS}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.cursor()

            cur.execute(
                "SELECT count(*) FROM signals "
                "WHERE result != '' AND result IS NOT NULL"
            )
            metrics["resolved_count"] = cur.fetchone()[0] or 0

            cur.execute(
                "SELECT max(cnt) FROM ("
                "  SELECT flag_path, count(*) as cnt FROM signals"
                "  WHERE result != '' AND result IS NOT NULL"
                "  GROUP BY flag_path"
                ")"
            )
            row = cur.fetchone()
            metrics["resolved_count_per_category_max"] = row[0] or 0

            try:
                cur.execute(
                    "SELECT max(cnt) FROM ("
                    "  SELECT wallet, count(*) as cnt FROM smart_money_fills"
                    "  WHERE resolved = 1"
                    "  GROUP BY wallet"
                    ")"
                )
                row = cur.fetchone()
                metrics["resolved_count_per_wallet_max"] = row[0] or 0
            except sqlite3.OperationalError:
                metrics["resolved_count_per_wallet_max"] = 0

            cur.execute(
                "SELECT count(*) FROM signals WHERE source = 'real_fill'"
            )
            metrics["fills_count"] = cur.fetchone()[0] or 0

        finally:
            conn.close()
    except Exception:
        pass

    return metrics


# ---------------------------------------------------------------------------
# Trigger evaluator
# ---------------------------------------------------------------------------

_OP_FNS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    ">":  lambda a, b: a > b,
    "<":  lambda a, b: a < b,
}


def evaluate_triggers(backlog: dict, metrics: dict) -> dict:
    """Return dict mapping item id -> bool (True = all conditions met and deps done)."""
    items_by_id = {i["id"]: i for i in backlog["items"]}
    results = {}
    for item in backlog["items"]:
        trigger_ok = True
        for cond in item.get("trigger", {}).get("all", []):
            fn = _OP_FNS.get(cond["op"])
            if fn is None or not fn(metrics.get(cond["metric"], 0), cond["value"]):
                trigger_ok = False
                break

        deps_ok = True
        for dep_id in item.get("depends_on", []):
            dep = items_by_id.get(dep_id)
            if dep is None or dep.get("status") != "done":
                deps_ok = False
                break

        results[item["id"]] = trigger_ok and deps_ok
    return results


# ---------------------------------------------------------------------------
# Status updater
# ---------------------------------------------------------------------------

def compare_statuses(backlog: dict, trigger_results: dict) -> list:
    """
    Return list of newly-unlocked item ids (were locked/blocked, now ready).
    Updates item status in-memory only; caller decides whether to persist.
    """
    newly_unlocked = []
    for item in backlog["items"]:
        if item.get("status") in ("locked", "blocked") and trigger_results.get(item["id"]):
            newly_unlocked.append(item["id"])
            item["status"] = "ready"
    return newly_unlocked


# ---------------------------------------------------------------------------
# Execute-action stubs (dispatch table)
# ---------------------------------------------------------------------------

def _stub(item_id: str, action: str) -> bool:
    print(f"[STUB] Execute: {action}")
    return True


_ACTION_DISPATCH = {
    "trade-reconciliation":        lambda item: _stub(item["id"], item["action"]),
    "realfill-dedup":              lambda item: _stub(item["id"], item["action"]),
    "sample-size-gates":           lambda item: _stub(item["id"], item["action"]),
    "wilson-intervals":            lambda item: _stub(item["id"], item["action"]),
    "title-scraping-fix":          lambda item: _stub(item["id"], item["action"]),
    "smart-money-drift-alerts":    lambda item: _stub(item["id"], item["action"]),
    "backtest-harness":            lambda item: _stub(item["id"], item["action"]),
    "position-reconciliation-job": lambda item: _stub(item["id"], item["action"]),
    "brier-tracking":              lambda item: _stub(item["id"], item["action"]),
    "confluence-detection":        lambda item: _stub(item["id"], item["action"]),
    "edge-decay-analysis":         lambda item: _stub(item["id"], item["action"]),
    "per-wallet-track-record":     lambda item: _stub(item["id"], item["action"]),
    "skill-vs-luck-weighting":     lambda item: _stub(item["id"], item["action"]),
    "walk-forward-validation":     lambda item: _stub(item["id"], item["action"]),
    "slippage-tracking":           lambda item: _stub(item["id"], item["action"]),
    "calibration-curve":           lambda item: _stub(item["id"], item["action"]),
    "calibration-curve-dashboard": lambda item: _stub(item["id"], item["action"]),
    "per-heuristic-scorecard":     lambda item: _stub(item["id"], item["action"]),
    "heuristic-sunsetting":        lambda item: _stub(item["id"], item["action"]),
    "empirical-base-rates-poly":   lambda item: _stub(item["id"], item["action"]),
    "auto-calibration-loop":       lambda item: _stub(item["id"], item["action"]),
    "wallet-tracking-dashboard":   lambda item: _stub(item["id"], item["action"]),
}


def execute_action(item: dict) -> bool:
    fn = _ACTION_DISPATCH.get(item["id"])
    if fn:
        return fn(item)
    print(f"[STUB] Execute: {item.get('action', '')}")
    return True


# ---------------------------------------------------------------------------
# Condition description helper
# ---------------------------------------------------------------------------

def _gate_str(item: dict, metrics: dict) -> str:
    conds = item.get("trigger", {}).get("all", [])
    if not conds:
        return "manual"
    parts = []
    for c in conds:
        live = metrics.get(c["metric"], 0)
        parts.append(f"{c['metric']} {c['op']} {c['value']} ({live} {c['op']} {c['value']})")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# BACKLOG.md generator
# ---------------------------------------------------------------------------

def generate_markdown(backlog: dict, metrics: dict) -> str:
    today = date.today().isoformat()
    rc = metrics.get("resolved_count", 0)
    fc = metrics.get("fills_count", 0)

    groups = {"ready": [], "locked": [], "blocked": [], "done": []}
    for item in backlog["items"]:
        groups.setdefault(item["status"], []).append(item)

    def sort_key(i):
        return (i["priority"], i["id"])

    lines = [
        "# Leviathan Backlog",
        f"Last updated: {today} | Metrics: resolved={rc}, fills={fc}",
        "",
    ]

    # Ready
    ready = sorted(groups.get("ready", []), key=sort_key)
    lines.append(f"## Ready ({len(ready)})")
    lines.append("| Priority | ID | Action | Area |")
    lines.append("|----------|-----|--------|------|")
    for item in ready:
        lines.append(f"| {item['priority']} | {item['id']} | {item['action']} | {item['area']} |")
    lines.append("")

    # Locked
    locked = sorted(groups.get("locked", []), key=sort_key)
    lines.append(f"## Locked ({len(locked)})")
    lines.append("| Priority | ID | Gate | Area |")
    lines.append("|----------|-----|------|------|")
    for item in locked:
        conds = item.get("trigger", {}).get("all", [])
        gate = "; ".join(
            f"{c['metric']} {c['op']} {c['value']}" for c in conds
        ) or "manual"
        lines.append(f"| {item['priority']} | {item['id']} | {gate} | {item['area']} |")
    lines.append("")

    # Blocked
    blocked = sorted(groups.get("blocked", []), key=sort_key)
    lines.append(f"## Blocked ({len(blocked)})")
    lines.append("| Priority | ID | Waiting On | Area |")
    lines.append("|----------|-----|-----------|------|")
    for item in blocked:
        waiting = ", ".join(item.get("depends_on", [])) or "-"
        lines.append(f"| {item['priority']} | {item['id']} | {waiting} | {item['area']} |")
    lines.append("")

    # Done
    done = sorted(groups.get("done", []), key=sort_key)
    lines.append(f"## Done ({len(done)})")
    lines.append("| Priority | ID | Action | Area |")
    lines.append("|----------|-----|--------|------|")
    for item in done:
        lines.append(f"| {item['priority']} | {item['id']} | {item['action']} | {item['area']} |")
    lines.append("")

    return "\n".join(lines)


def write_markdown(backlog: dict, metrics: dict, dest=BACKLOG_MD) -> None:
    content = generate_markdown(backlog, metrics)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Email block formatter
# ---------------------------------------------------------------------------

def format_email_block(backlog: dict, metrics: dict, newly_unlocked: list) -> str:
    today = date.today().isoformat()
    items_by_id = {i["id"]: i for i in backlog["items"]}

    groups = {"ready": 0, "locked": 0, "blocked": 0, "done": 0}
    for item in backlog["items"]:
        groups[item.get("status", "ready")] += 1

    lines = [
        "=== LEVIATHAN BACKLOG UPDATE ===",
        f"Date: {today}",
        f"Newly Unlocked: {len(newly_unlocked)}",
        "",
    ]

    for item_id in newly_unlocked:
        item = items_by_id.get(item_id, {})
        gate = _gate_str(item, metrics)
        lines.append(f"[{item.get('priority', '?')}] {item_id}")
        lines.append(f"Action: {item.get('action', '')}")
        lines.append(f"Gate cleared: {gate}")
        lines.append(f"Reply CONTINUE:{item_id} or REVIEW:{item_id} to act.")
        lines.append("----")
        lines.append("")

    lines += [
        "Live Metrics:",
        f"resolved_count: {metrics.get('resolved_count', 0)}",
        f"resolved_count_per_category_max: {metrics.get('resolved_count_per_category_max', 0)}",
        f"resolved_count_per_wallet_max: {metrics.get('resolved_count_per_wallet_max', 0)}",
        f"fills_count: {metrics.get('fills_count', 0)}",
        "",
        f"Full backlog: {groups['ready']} ready / {groups['locked']} locked / {groups['blocked']} blocked",
        "===",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI prompt mode
# ---------------------------------------------------------------------------

def _prompt_item(item: dict, metrics: dict, backlog: dict, backlog_path: Path) -> None:
    gate = _gate_str(item, metrics)
    print(f"\n[UNLOCKED] {item['id']} (priority {item['priority']})")
    print(f"Action: {item['action']}")
    print(f"Trigger met: {gate}")
    print()
    print("  (C) Continue   (M) Manual Review   (S) Skip")

    while True:
        choice = input("  > ").strip().upper()
        if choice == "C":
            execute_action(item)
            item["status"] = "done"
            save_backlog(backlog_path, backlog)
            print(f"  Marked done: {item['id']}")
            break
        elif choice == "M":
            item["status"] = "ready"
            save_backlog(backlog_path, backlog)
            print("  Flagged for manual review")
            break
        elif choice == "S":
            break
        else:
            print("  Enter C, M, or S")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(backlog_path=DEFAULT_BACKLOG, db_path=DEFAULT_DB, email_mode=False):
    backlog = load_backlog(backlog_path)
    metrics = compute_metrics(db_path)
    trigger_results = evaluate_triggers(backlog, metrics)
    newly_unlocked = compare_statuses(backlog, trigger_results)

    write_markdown(backlog, metrics)

    if email_mode:
        block = format_email_block(backlog, metrics, newly_unlocked)
        print(block)
        return block

    if not newly_unlocked:
        print("No new items unlocked.")
        return ""

    items_by_id = {i["id"]: i for i in backlog["items"]}
    for item_id in newly_unlocked:
        item = items_by_id[item_id]
        _prompt_item(item, metrics, backlog, backlog_path)

    return ""


def main():
    parser = argparse.ArgumentParser(description="Leviathan weekly backlog checker")
    parser.add_argument("--email", action="store_true",
                        help="Email block mode: print summary, skip CLI prompts")
    parser.add_argument("--file", default=str(DEFAULT_BACKLOG), metavar="PATH")
    parser.add_argument("--db", default=str(DEFAULT_DB), metavar="PATH")
    args = parser.parse_args()

    run(backlog_path=Path(args.file), db_path=Path(args.db), email_mode=args.email)
    return 0


if __name__ == "__main__":
    sys.exit(main())
