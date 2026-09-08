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
    """
    Read live metrics from leviathan.db (read-only). Returns dict of counts,
    plus a "_data_gaps" key (list of metric names whose backing table/column
    doesn't exist yet -- distinct from a metric that's genuinely 0).

    2026-08-26: resolved_count_per_wallet_max used to silently report 0 on
    a missing smart_money_fills table with no way to tell that apart from
    "genuinely zero resolved fills across all wallets" -- found via a
    weekly_code_audit.py run, backlog: smart-money-fills-table-missing.
    The gate-evaluation VALUE is unchanged (0 either way is correct for
    "not unlockable yet" purposes, and building the actual fills-tracking
    pipeline is a separate, much larger feature -- the three items gated
    on this metric are all still locked behind resolved_count_per_wallet_max
    >= 10 regardless, i.e. nowhere near unlocking even if this table did
    exist). This just makes the gap visible instead of silent, the same
    way a sentinel trigger metric (e.g. api_spend_authorized) is already
    flagged as "requires human decision" rather than reported as a bare
    "not met" that implies it could clear on its own.
    """
    metrics = {k: 0 for k in METRICS_KEYS}
    metrics["_data_gaps"] = []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.cursor()

            # resolved-count-metric-desync (2026-08-16): this SQL diverged
            # from core.logger.get_stats()['resolved'] (the number
            # scripts/gate_notifier.py's actual gate-unlock emails use) two
            # ways -- (1) no direction filter, so PASS-direction rows (which
            # resolve LOSS by construction, not a real call -- see
            # export-validation-pass-exclusion) were counted, and (2) no
            # source filter, so real_fill and research_probe rows (tracked
            # separately from paper signals by design) were counted too.
            # Both fixed to match get_stats()'s _PAPER + direction != 'PASS'
            # filters exactly -- live data went from 47 (buggy) -> 16
            # (direction fix only) -> 13 (matches get_stats() exactly).
            cur.execute(
                "SELECT count(*) FROM signals "
                "WHERE result != '' AND result IS NOT NULL AND direction != 'PASS' "
                "AND (source = 'paper' OR source IS NULL)"
            )
            metrics["resolved_count"] = cur.fetchone()[0] or 0

            # resolved-count-per-category-max-wrong-column (2026-08-27): this
            # used to GROUP BY flag_path -- only ~5 coarse buckets (EDGE,
            # DRIFT, HEURISTIC, BR_NONE, RESOLVE_FIRST) -- despite the
            # metric's own name/glossary entry ("max resolved across any
            # single heuristic category") and its consumers (core/sizing.py,
            # the per-heuristic-scorecard/heuristic-sunsetting backlog
            # triggers) all meaning the much finer heuristic_label column
            # (SCOTUS, CONFLICT, IMPEACHMENT, etc -- ~17-90 distinct values).
            # The coarse grouping read 86 live; the real per-label max was 2.
            # Filters match resolved_count's own (direction != 'PASS',
            # paper-source-only) for consistency within this same function.
            # Isolated in its own try/except (like the smart_money_fills
            # query below) so a schema surprise on this one query can't
            # silently zero out resolved_count_per_wallet_max/fills_count
            # too -- exactly the failure mode a bare outer except caused
            # when this query was first added against a test fixture
            # missing the heuristic_label column entirely.
            try:
                cur.execute(
                    "SELECT max(cnt) FROM ("
                    "  SELECT heuristic_label, count(*) as cnt FROM signals"
                    "  WHERE result != '' AND result IS NOT NULL AND heuristic_label IS NOT NULL "
                    "  AND direction != 'PASS' AND (source = 'paper' OR source IS NULL)"
                    "  GROUP BY heuristic_label"
                    ")"
                )
                row = cur.fetchone()
                metrics["resolved_count_per_category_max"] = row[0] or 0
            except sqlite3.OperationalError as e:
                metrics["resolved_count_per_category_max"] = 0
                if "no such column" in str(e):
                    metrics["_data_gaps"].append("resolved_count_per_category_max")

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
            except sqlite3.OperationalError as e:
                metrics["resolved_count_per_wallet_max"] = 0
                if "no such table" in str(e):
                    metrics["_data_gaps"].append("resolved_count_per_wallet_max")

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

def gate_progress_str(item: dict, metrics: dict) -> str:
    """Live progress toward an item's own trigger, sentinel-aware (mirrors
    scripts/verify_liam_report.py's ground-truth logic): shows the current
    live value and MET/not-met per condition, and flags a sentinel metric
    (one never computed by compute_metrics -- e.g. api_spend_authorized)
    as requiring a human decision rather than reporting it as simply
    "not met", which would wrongly imply it could clear on its own.

    For a still-locked/blocked item, not a newly-unlocked one -- see
    _gate_str below for that case, where the trigger is by definition
    already satisfied. Returns '' if the item has no trigger conditions."""
    conds = item.get("trigger", {}).get("all", [])
    if not conds:
        return ""
    data_gaps = metrics.get("_data_gaps", [])
    parts = []
    for c in conds:
        if c["metric"] not in METRICS_KEYS:
            parts.append(f"{c['metric']} {c['op']} {c['value']} [requires human decision, never auto-computed]")
            continue
        live = metrics.get(c["metric"], 0)
        met = _OP_FNS[c["op"]](live, c["value"])
        gap_note = " [data not tracked yet, not a real 0 -- see backlog: smart-money-fills-table-missing]" if c["metric"] in data_gaps else ""
        parts.append(f"{c['metric']}={live} {c['op']} {c['value']} ({'MET' if met else 'not met'}){gap_note}")
    return "; ".join(parts)


def _gate_str(item: dict, metrics: dict) -> str:
    conds = item.get("trigger", {}).get("all", [])
    if not conds:
        return "manual"
    data_gaps = metrics.get("_data_gaps", [])
    parts = []
    for c in conds:
        live = metrics.get(c["metric"], 0)
        gap_note = " [data not tracked yet]" if c["metric"] in data_gaps else ""
        parts.append(f"{c['metric']} {c['op']} {c['value']} ({live} {c['op']} {c['value']}){gap_note}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# BACKLOG.md generator
# ---------------------------------------------------------------------------

def _summarize_action(action: str, max_len: int = 200) -> str:
    """First sentence (or a hard cutoff) of an action narrative, for
    BACKLOG.md's tables. Done items in particular accumulate long
    retrospective paragraphs (see backlog.json) that would otherwise get
    re-embedded in full on every regeneration -- backlog.json stays the
    source of truth for the complete text, this is just a summary."""
    action = action.replace("|", "/").strip()
    period = action.find(". ")
    if 0 < period < max_len:
        return action[:period + 1]
    if len(action) <= max_len:
        return action
    return action[:max_len].rsplit(" ", 1)[0] + "…"


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
        "Action text below is summarized. Full narrative per item is "
        "`backlog/backlog.json`'s `action` field -- this file is "
        "auto-generated, never hand-edit it.",
        "",
    ]

    # Ready
    ready = sorted(groups.get("ready", []), key=sort_key)
    lines.append(f"## Ready ({len(ready)})")
    lines.append("| Priority | ID | Action | Area |")
    lines.append("|----------|-----|--------|------|")
    for item in ready:
        lines.append(f"| {item['priority']} | {item['id']} | {_summarize_action(item['action'])} | {item['area']} |")
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
        lines.append(f"| {item['priority']} | {item['id']} | {_summarize_action(item['action'])} | {item['area']} |")
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

    wallet_gap = " (not tracked yet -- smart_money_fills table missing, not a real 0)" \
        if "resolved_count_per_wallet_max" in metrics.get("_data_gaps", []) else ""
    lines += [
        "Live Metrics:",
        f"resolved_count: {metrics.get('resolved_count', 0)}",
        f"resolved_count_per_category_max: {metrics.get('resolved_count_per_category_max', 0)}",
        f"resolved_count_per_wallet_max: {metrics.get('resolved_count_per_wallet_max', 0)}{wallet_gap}",
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

def run(backlog_path=DEFAULT_BACKLOG, db_path=DEFAULT_DB, email_mode=False,
        markdown_path=BACKLOG_MD):
    backlog = load_backlog(backlog_path)
    metrics = compute_metrics(db_path)
    trigger_results = evaluate_triggers(backlog, metrics)
    newly_unlocked = compare_statuses(backlog, trigger_results)

    # compare_statuses() only mutates the in-memory dict -- persist any
    # locked/blocked -> ready transition immediately, in BOTH modes.
    # Becoming "ready" is a fact (the trigger/dependency conditions are
    # met), independent of what a human later decides to do about it
    # (continue/manual-review/skip via _prompt_item, which does its own
    # separate save for the ready -> done transition). A prior version
    # only ever saved here on the interactive C/M path, so --email
    # mode (the scheduled, non-interactive path this module's own
    # docstring describes running via Windows Task Scheduler) discarded
    # the mutation on exit -- backlog.json on disk never advanced past
    # "locked", so every subsequent scheduled run re-evaluated the same
    # already-met trigger and re-sent the same "Newly Unlocked" email
    # indefinitely.
    if newly_unlocked:
        save_backlog(backlog_path, backlog)

    # markdown_path defaults to the real project BACKLOG.md but is a real
    # parameter (not hardcoded) precisely so a caller pointed at a
    # different backlog_path (an alternate/test backlog.json) doesn't
    # silently overwrite the real repo's BACKLOG.md with unrelated
    # content -- a prior version called write_markdown(backlog, metrics)
    # with no destination at all, so it always targeted BACKLOG_MD
    # regardless of backlog_path. A test that ran the real checker.py
    # against a synthetic one-item backlog (to verify the --email
    # persistence fix above) clobbered the actual repo's BACKLOG.md with
    # that synthetic content on every test-suite run, and the corrupted
    # file was committed and pushed before this was caught.
    write_markdown(backlog, metrics, dest=markdown_path)

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
    parser.add_argument("--markdown", default=str(BACKLOG_MD), metavar="PATH",
                        help="Where to write the rendered BACKLOG.md (default: repo root)")
    args = parser.parse_args()

    run(backlog_path=Path(args.file), db_path=Path(args.db), email_mode=args.email,
        markdown_path=Path(args.markdown))
    return 0


if __name__ == "__main__":
    sys.exit(main())
