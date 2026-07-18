"""
scripts/gate_notifier.py — Gate-unlock notifier.

Reads BACKLOG.md's Locked table, evaluates each gate's condition against
current DB metrics (reusing existing logger functions only — computes no
new metric), and emails ONCE via the existing core.report.send_report path
when any gate transitions locked/unknown -> unlocked. This is a notifier,
not an agent: it forms no opinions, changes no thresholds, and takes no
action beyond composing and sending one batched email.

Fire-once semantics: state is persisted in data/gate_state.json (git-
ignored). A gate already "unlocked" in that file does not re-notify.

Usage:
    python scripts/gate_notifier.py              # normal run, sends email if needed
    python scripts/gate_notifier.py --dry-run    # prints subject+body, sends nothing,
                                                  # persists nothing (repeatable)

Scheduled via Windows Task Scheduler — see scripts/setup_gate_notifier_scheduler.ps1.
Run after the daily pipeline + resolve-first job so metrics are fresh.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from core import logger
from core.report import send_report

DEFAULT_BACKLOG = ROOT / "BACKLOG.md"
DEFAULT_STATE   = ROOT / "data" / "gate_state.json"


class GateParseError(Exception):
    """Raised when one or more Locked-table Gate cells fail to parse as a
    known grammar (metric-gate). Carries every offending row's message so
    all failures can be reported together, not just the first."""

    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


# ── Markdown table parsing (no eval/exec on any parsed content) ──────────────

def _extract_table_rows(markdown: str, section_header: str) -> list[list[str]]:
    """
    Returns each data row (as a list of trimmed cell strings) from the
    markdown table under a `## {section_header}` heading, stopping at the
    next `## ` heading or end of file. Skips the header row and the
    |---|---| separator row.
    """
    lines = markdown.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith(f"## {section_header}"):
            start = i + 1
            break
    if start is None:
        return []

    rows = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or all(c == "" for c in cells):
            continue
        if all(set(c) <= {"-"} for c in cells if c):
            continue  # separator row
        if cells and cells[0] == "Priority":
            continue  # header row
        rows.append(cells)
    return rows


#: METRIC OP NUMBER, e.g. "resolved_count >= 25". No eval()/exec() — a fixed grammar.
_GATE_RE = re.compile(r"^([a-z_][a-z0-9_]*)\s*(>=|<=|==|>|<)\s*(-?\d+(?:\.\d+)?)$")


def parse_gate_cell(cell: str) -> tuple[str, str, float] | None:
    """Parses a Gate cell into (metric_name, op, threshold), or None if it
    doesn't match the fixed METRIC OP NUMBER grammar at all."""
    m = _GATE_RE.match(cell.strip())
    if not m:
        return None
    metric, op, num = m.groups()
    return metric, op, float(num)


def parse_locked_table(markdown: str) -> tuple[list[dict], list[str]]:
    """
    Parses BACKLOG.md's Locked table into row dicts {id, area, gate_cell}
    plus a list of human-readable messages for any row whose Gate cell does
    not match the metric-gate grammar (these must fail loud, not be dropped
    silently — see GateParseError in run()).
    """
    rows = []
    failures = []
    for cells in _extract_table_rows(markdown, "Locked"):
        if len(cells) < 4:
            failures.append(f"Locked row has too few columns: {cells!r}")
            continue
        _priority, gate_id, gate_cell, area = cells[0], cells[1], cells[2], cells[3]
        if parse_gate_cell(gate_cell) is None:
            failures.append(f"id={gate_id!r} has an unparseable Gate cell: {gate_cell!r}")
            continue
        rows.append({"id": gate_id, "area": area, "gate_cell": gate_cell})
    return rows, failures


def parse_blocked_table(markdown: str) -> list[dict]:
    """
    Parses BACKLOG.md's Blocked table into row dicts {id, area, waiting_on}.

    PART A.5 CHOICE: dependency gates are deferred from v1 (not evaluated —
    reported as "dependency-tracked" only). Several Blocked rows depend on
    MULTIPLE comma-separated backlog IDs (e.g. "sample-size-gates,
    brier-tracking"), which needs AND-logic across each ID's Done-table
    membership — real added complexity beyond this notifier's core
    single-metric-gate pattern. Deferring keeps v1 bounded; a later pass can
    wire dependency evaluation using the same Done-table-membership check
    without touching this module's core parsing/notify logic.
    """
    rows = []
    for cells in _extract_table_rows(markdown, "Blocked"):
        if len(cells) < 4:
            continue
        _priority, gate_id, waiting_on, area = cells[0], cells[1], cells[2], cells[3]
        rows.append({"id": gate_id, "area": area, "waiting_on": waiting_on})
    return rows


# ── Known-metric registry (PART B) — existing logger functions only ─────────

def _metric_resolved_count(_config: dict) -> float:
    return logger.get_stats()["resolved"]


def _metric_fills_count(_config: dict) -> float:
    return logger.get_stats_real()["total_fills"]


def _metric_resolved_count_per_category_max(_config: dict) -> float:
    """
    get_stats_by_flag_path() only includes rows with a resolved outcome
    (WHERE outcome != '' AND outcome IS NOT NULL — confirmed against
    core/logger.py), so each row's "total" IS a per-category resolved
    count, not an all-signals count. max() across groups gives the metric
    this gate names. Returns 0 (a real, computable value — not unknown)
    when no resolved data exists yet.
    """
    rows = logger.get_stats_by_flag_path()
    return max((r["total"] for r in rows), default=0)


#: metric_name -> callable(config) -> float. A metric NOT in this dict is
#: classified "unknown" ("not yet measurable"), never guessed at.
#: resolved_count_per_wallet_max is deliberately absent: no logger function
#: computes per-wallet resolved counts today (per-wallet-track-record is
#: itself a locked backlog item) — this gate must stay unmeasurable.
KNOWN_METRICS = {
    "resolved_count":                   _metric_resolved_count,
    "fills_count":                      _metric_fills_count,
    "resolved_count_per_category_max":  _metric_resolved_count_per_category_max,
}

_OPS = {
    ">=": lambda a, b: a >= b,
    ">":  lambda a, b: a > b,
    "==": lambda a, b: a == b,
    "<=": lambda a, b: a <= b,
    "<":  lambda a, b: a < b,
}


def evaluate_gate(row: dict, config: dict) -> dict:
    """
    Evaluates one parsed Locked-table row against current metrics.
    Returns {id, area, status, metric_name, op, threshold, value}.
    status is "unlocked", "locked", or "unknown" (metric not in the
    registry — reported once, never counts as unlocked).
    """
    metric_name, op, threshold = parse_gate_cell(row["gate_cell"])
    result = {
        "id": row["id"], "area": row["area"],
        "metric_name": metric_name, "op": op, "threshold": threshold,
    }
    if metric_name not in KNOWN_METRICS:
        return {**result, "status": "unknown", "value": None}

    value = KNOWN_METRICS[metric_name](config)
    status = "unlocked" if _OPS[op](value, threshold) else "locked"
    return {**result, "status": status, "value": value}


# ── State persistence + transition detection (PART C) ───────────────────────

def load_state(path: Path) -> dict:
    if not path.exists():
        return {"gates": {}}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "gates" not in data:
            return {"gates": {}}
        return data
    except Exception:
        return {"gates": {}}


def save_state(path: Path, current: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "gates": current,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def compute_newly_unlocked(current: dict[str, dict], prior_state: dict) -> list[dict]:
    """
    Returns every gate whose status is "unlocked" THIS run and whose prior
    status was "locked", "unknown", or absent (first-seen-as-locked). A
    gate already "unlocked" in prior_state does NOT re-notify — this is
    the fire-once behavior the whole notifier exists to provide.
    """
    prior_gates = prior_state.get("gates", {})
    newly = []
    for gate_id, result in current.items():
        if result["status"] != "unlocked":
            continue
        prior = prior_gates.get(gate_id)
        prior_status = prior["status"] if prior else "locked"
        if prior_status != "unlocked":
            newly.append(result)
    return newly


# ── Email composition (PART D — reuses send_report as-is) ───────────────────

def compose_email(newly_unlocked: list[dict]) -> tuple[str, str]:
    """Returns (body, subject) for the batched notification. Never one email per gate."""
    n = len(newly_unlocked)
    ids = ", ".join(g["id"] for g in newly_unlocked)
    subject = f"Leviathan — {n} gate{'s' if n != 1 else ''} unlocked: {ids}"

    lines = ["The following backlog gate(s) just unlocked:", ""]
    for g in newly_unlocked:
        lines.append(f"  [{g['area']}] {g['id']}")
        lines.append(f"    Gate: {g['metric_name']} {g['op']} {g['threshold']}  "
                      f"|  observed: {g['value']}")
        lines.append("")
    body = "\n".join(lines)
    return body, subject


# ── Config loading (matches backtesting/eval_rescore.py convention) ─────────

def load_config() -> dict:
    cfg_path = ROOT / "config.json"
    if not cfg_path.exists():
        cfg_path = ROOT / "config.example.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


# ── Orchestration ─────────────────────────────────────────────────────────────

def run(config: dict, backlog_path: Path = DEFAULT_BACKLOG,
        state_path: Path = DEFAULT_STATE, dry_run: bool = False) -> dict:
    """
    Runs one full check: parse -> evaluate -> diff against persisted state
    -> notify (batched, once) if anything newly unlocked.

    Returns a result dict: {"current": {...}, "newly_unlocked": [...],
    "blocked": [...], "sent": bool, "dry_run": bool, "error": str|None}.
    Raises GateParseError if any Locked-table row fails to parse.
    """
    markdown = backlog_path.read_text(encoding="utf-8")
    locked_rows, parse_failures = parse_locked_table(markdown)
    blocked_rows = parse_blocked_table(markdown)

    if parse_failures:
        raise GateParseError(parse_failures)

    current = {row["id"]: evaluate_gate(row, config) for row in locked_rows}

    for gate_id, result in current.items():
        if result["status"] == "unknown":
            print(f"[gate_notifier] {gate_id}: not yet measurable "
                  f"(metric '{result['metric_name']}' has no known computation)")
    for row in blocked_rows:
        print(f"[gate_notifier] {row['id']}: dependency-tracked "
              f"(waiting on {row['waiting_on']!r}, not evaluated in v1)")

    prior_state = load_state(state_path)
    newly_unlocked = compute_newly_unlocked(current, prior_state)

    if dry_run:
        body, subject = compose_email(newly_unlocked) if newly_unlocked else ("", "")
        if newly_unlocked:
            print(subject)
            print()
            print(body)
        else:
            print("[gate_notifier] --dry-run: no newly-unlocked gates this run")
        return {"current": current, "newly_unlocked": newly_unlocked,
                "blocked": blocked_rows, "sent": False, "dry_run": True, "error": None}

    if not newly_unlocked:
        save_state(state_path, current)
        return {"current": current, "newly_unlocked": [], "blocked": blocked_rows,
                "sent": False, "dry_run": False, "error": None}

    body, subject = compose_email(newly_unlocked)
    try:
        send_report(body, signals=[], whale_flags=0, config=config, subject_override=subject)
    except Exception as e:
        print(f"[gate_notifier] send FAILED — state NOT persisted, will retry next run: {e}")
        return {"current": current, "newly_unlocked": newly_unlocked, "blocked": blocked_rows,
                "sent": False, "dry_run": False, "error": str(e)}

    save_state(state_path, current)
    print(f"[gate_notifier] Sent: {len(newly_unlocked)} gate(s) unlocked "
          f"({', '.join(g['id'] for g in newly_unlocked)})")
    return {"current": current, "newly_unlocked": newly_unlocked, "blocked": blocked_rows,
            "sent": True, "dry_run": False, "error": None}


def main():
    parser = argparse.ArgumentParser(description="Leviathan gate-unlock notifier")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print subject+body, send nothing, persist nothing")
    parser.add_argument("--backlog", default=str(DEFAULT_BACKLOG))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    args = parser.parse_args()

    config = load_config()

    try:
        result = run(config, Path(args.backlog), Path(args.state), dry_run=args.dry_run)
    except GateParseError as e:
        print("[gate_notifier] FATAL: unparseable Locked-table gate row(s):")
        for msg in e.messages:
            print(f"  - {msg}")
        sys.exit(1)

    sys.exit(1 if result.get("error") else 0)


if __name__ == "__main__":
    main()
