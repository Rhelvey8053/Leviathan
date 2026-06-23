"""
backlog.py - Leviathan backlog CLI and importable engine.

Subcommands:
  status  Print summary of backlog items grouped by status.
  add     Append a validated item to backlog.json.

Default when no subcommand given: status.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

DEFAULT_FILE = Path(__file__).parent / "backlog.json"

VALID_AREAS = frozenset({
    "execution", "data-quality", "validation",
    "reporting", "calibration", "smart-money", "backtesting",
})
VALID_OPS = frozenset({">=", "<=", "==", ">", "<"})

_TRIGGER_RE = re.compile(r"^([a-z_]+)(>=|<=|==|>|<)(\d+(?:\.\d+)?)$")


def parse_trigger(s: str) -> dict:
    """Parse a trigger string into {"all": [conditions]}."""
    s = s.strip()
    if s == "manual" or s == "":
        return {"all": []}
    conditions = []
    for part in s.split(","):
        part = part.strip()
        m = _TRIGGER_RE.match(part)
        if not m:
            raise ValueError(f"Invalid trigger condition: {part!r}")
        metric, op, raw_value = m.group(1), m.group(2), m.group(3)
        value = float(raw_value) if "." in raw_value else int(raw_value)
        conditions.append({"metric": metric, "op": op, "value": value})
    return {"all": conditions}


def determine_status(trigger: dict, depends_on: list) -> str:
    """Apply status precedence: blocked > locked > ready."""
    if depends_on:
        return "blocked"
    if trigger.get("all"):
        return "locked"
    return "ready"


def validate_item(item: dict, existing_items: list, glossary: dict) -> list:
    """Return list of error strings; empty list means valid."""
    errors = []
    existing_ids = {i["id"] for i in existing_items}

    item_id = item.get("id", "")
    if not item_id:
        errors.append("id is required and must be non-empty")
    elif item_id in existing_ids:
        errors.append(f"id {item_id!r} already exists in backlog")

    area = item.get("area", "")
    if area not in VALID_AREAS:
        errors.append(f"area {area!r} is not valid; must be one of {sorted(VALID_AREAS)}")

    priority = item.get("priority")
    if not isinstance(priority, int) or not (1 <= priority <= 9):
        errors.append(f"priority must be an integer 1-9, got {priority!r}")

    trigger = item.get("trigger", {})
    for cond in trigger.get("all", []):
        metric = cond.get("metric", "")
        op = cond.get("op", "")
        value = cond.get("value")
        if metric not in glossary:
            errors.append(f"trigger metric {metric!r} is not in metrics_glossary")
        if op not in VALID_OPS:
            errors.append(f"trigger op {op!r} is not valid")
        if not isinstance(value, (int, float)):
            errors.append(f"trigger value {value!r} must be numeric")

    for dep in item.get("depends_on", []):
        if dep not in existing_ids:
            errors.append(f"depends_on id {dep!r} does not exist in backlog")

    return errors


def load_backlog(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_backlog(path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _trigger_display(trigger: dict) -> str:
    conds = trigger.get("all", [])
    if not conds:
        return "manual"
    return ", ".join(f"{c['metric']}{c['op']}{c['value']}" for c in conds)


def cmd_status(args) -> int:
    path = args.file
    data = load_backlog(path)
    items = data.get("items", [])
    glossary = data.get("metrics_glossary", {})

    all_errors = []
    for item in items:
        others = [i for i in items if i["id"] != item["id"]]
        errs = validate_item(item, others, glossary)
        for e in errs:
            all_errors.append(f"  [{item['id']}] {e}")
    if all_errors:
        print("VALIDATION ERRORS:")
        for e in all_errors:
            print(e)
        print()

    groups = {"ready": [], "locked": [], "blocked": [], "done": []}
    for item in items:
        groups.setdefault(item["status"], []).append(item)

    for status in ("ready", "locked", "blocked", "done"):
        bucket = sorted(groups[status], key=lambda i: (i["priority"], i["id"]))
        groups[status] = bucket

    total = len(items)
    print(f"Leviathan Backlog  ({data.get('updated', '?')})")
    print(f"  Total:   {total}")
    print(f"  Ready:   {len(groups['ready'])}")
    print(f"  Locked:  {len(groups['locked'])}")
    print(f"  Blocked: {len(groups['blocked'])}")
    print(f"  Done:    {len(groups['done'])}")
    print()

    for status, label in (("done", "DONE"), ("ready", "READY"), ("locked", "LOCKED"), ("blocked", "BLOCKED")):
        bucket = groups[status]
        if not bucket:
            continue
        print(f"-- {label} ({len(bucket)}) --")
        for item in bucket:
            trig = _trigger_display(item.get("trigger", {}))
            deps = ", ".join(item.get("depends_on", [])) or "-"
            print(f"  [{item['priority']}] {item['id']}")
            print(f"        trigger: {trig}")
            if item.get("depends_on"):
                print(f"        depends: {deps}")
        print()

    return 0


def cmd_add(args) -> int:
    path = args.file
    data = load_backlog(path)
    items = data.get("items", [])
    glossary = data.get("metrics_glossary", {})

    trigger_str = args.trigger if args.trigger else "manual"
    try:
        trigger = parse_trigger(trigger_str)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1

    depends_on = []
    if args.depends_on:
        depends_on = [d.strip() for d in args.depends_on.split(",") if d.strip()]

    status = determine_status(trigger, depends_on)

    new_item = {
        "id": args.id,
        "title": args.title,
        "area": args.area,
        "priority": args.priority,
        "status": status,
        "trigger": trigger,
        "depends_on": depends_on,
        "action": args.action,
        "notes": args.notes or "",
    }

    errors = validate_item(new_item, items, glossary)
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        return 1

    print(f"id:      {new_item['id']}")
    print(f"status:  {status}")
    print(f"trigger: {_trigger_display(trigger)}")

    if args.dry_run:
        print("(dry-run: nothing written)")
        return 0

    items.append(new_item)
    data["items"] = items
    data["updated"] = date.today().isoformat()
    save_backlog(path, data)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Leviathan backlog CLI")
    parser.add_argument("--file", default=str(DEFAULT_FILE), metavar="PATH",
                        help="Path to backlog.json (default: %(default)s)")

    sub = parser.add_subparsers(dest="subcommand")

    sub.add_parser("status", help="Print backlog summary")

    add_p = sub.add_parser("add", help="Append a new item to the backlog")
    add_p.add_argument("--id", required=True)
    add_p.add_argument("--title", required=True)
    add_p.add_argument("--area", required=True)
    add_p.add_argument("--priority", required=True, type=int)
    add_p.add_argument("--action", required=True)
    add_p.add_argument("--trigger", default="manual")
    add_p.add_argument("--depends-on", default="", dest="depends_on")
    add_p.add_argument("--notes", default="")
    add_p.add_argument("--dry-run", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand is None or args.subcommand == "status":
        return cmd_status(args)
    if args.subcommand == "add":
        return cmd_add(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
