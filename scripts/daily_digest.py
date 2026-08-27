"""
scripts/daily_digest.py -- One daily "here's what happened" email.

Confirmed additive (2026-08-24), not a replacement: heartbeat_check.py,
automation_health_check.py, and gate_notifier.py keep sending their own
emails on their own triggers exactly as before. This is a second,
always-sent channel -- a single place to read "what ran and what it
found" instead of checking ~5 separate log/data files that otherwise
only get opened once something has visibly already broken. Out of scope
for this version, deliberately deferred rather than forgotten: monday.com/
Liam's posts, and folding the existing alert emails into this one.

Sections, each read against files/DB other scripts already write --
nothing here re-runs a scan or hits a live API:
  - Task health: automation_health_check.check_scheduled_tasks() +
    check_litestream_replica(), heartbeat_check.get_last_run()/hours_since()
  - Reconciliation: today's data/reconciliation/<date>.json, rendered via
    position_reconciliation.format_report() (it already had a `compact`
    mode built for exactly this -- see its own docstring)
  - Smart money: data/smart_money/latest_signals.json's run_at/signal_count
  - Resolve-first: signals table, flag_path='RESOLVE_FIRST', logged in the
    last ~20h -- analysis/resolve_first.py's own marker for a mechanical
    pick, not a Claude call; see its log_selected(). Undercounts by
    design, not by bug: log_selected() only falls back to 'RESOLVE_FIRST'
    when the market has no real flag_path of its own (m.get("flag_path")
    or "RESOLVE_FIRST") -- a resolve-first pick that core.scanner already
    scored as a genuine EDGE/DRIFT/HEURISTIC/BR_NONE signal keeps that
    real flag instead, which is arguably more informative (it shows up
    under its actual signal type, not lumped in as "mechanical"). This
    section only ever shows the picks that were resolve-first-only.
    analysis/resolve_first.py carries its own HARD FREEZE note against
    new logging/scoring changes, so this reads its existing shape as-is
    rather than adding a dedicated marker column.
  - Backlog (added 2026-08-27): every `ready` item, priority-ordered, plus
    what changed in backlog.json since the last digest (new items, status
    transitions, and notes-only updates -- most days' real backlog work is
    an investigation note, not a status flip) with the purpose pulled from
    the item's own most-recently-appended notes paragraph (this backlog's
    convention: dated paragraphs appended per update, newest last) rather
    than re-derived. Diffed against a per-item {status, notes_len}
    snapshot persisted in state["backlog_snapshot"] -- cheap enough that a
    length change is a good-enough proxy for "notes were edited" without
    hashing the full text.
  - Weekly audits: logs/weekly_audit.log / logs/weekly_code_audit.log,
    included only when modified in roughly the last 20h (i.e. an actual
    Monday run happened), as a raw tail excerpt -- both are free-form
    Claude-CLI narrative output, not structured data, so no attempt is
    made to parse a one-line summary out of them

Sends once daily, unconditionally -- not alert-only. For a digest,
"nothing wrong" is itself useful information (it confirms the whole
chain actually ran), so there's no silent-when-healthy behavior the way
heartbeat/automation-health have.

2026-08-25: restructured on request into an explicit per-task checklist
(every monitored task listed by name with [x]/[!], not collapsed into a
"N healthy" summary) plus a dedicated NEEDS YOUR ATTENTION section at
the top of the email, aggregating every problem found across sections so
there's one place to look rather than scanning each section for a stray
[!]. Scope of what counts as "needs attention" is currently task health
+ reconciliation only (the two sections that already had a has_problem
signal) -- smart-money staleness and resolve-first counts stay
informational-only for now, a deliberate boundary, not an oversight.
The subject line keeps the same [ATTENTION] prefix, driven by the same
problem list the top section renders.

Usage:
    python scripts/daily_digest.py              # normal run
    python scripts/daily_digest.py --dry-run    # print, send/persist nothing
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from core import logger
from core.report import send_report
from backlog.engine import load_backlog as _load_backlog
from backlog import checker as _checker
import scripts.automation_health_check as _ahc
import scripts.heartbeat_check as _hb
import scripts.position_reconciliation as _recon

DEFAULT_STATE = ROOT / "data" / "daily_digest_state.json"
BACKLOG_PATH = ROOT / "backlog" / "backlog.json"
RECONCILIATION_DIR = ROOT / "data" / "reconciliation"
SMART_MONEY_LATEST = ROOT / "data" / "smart_money" / "latest_signals.json"
WEEKLY_LOGS = {
    "Weekly project audit": ROOT / "logs" / "weekly_audit.log",
    "Weekly code audit":    ROOT / "logs" / "weekly_code_audit.log",
}
WEEKLY_LOG_FRESHNESS_HOURS = 20.0
WEEKLY_LOG_TAIL_LINES = 15
RESOLVE_FIRST_LOOKBACK_HOURS = 20.0
# format_report()'s own `compact` flag is accepted but currently a no-op
# (doesn't actually shorten per-ticker listings) -- truncated here instead
# so a day with many unplaced/aligned tickers doesn't blow up the digest.
# format_report() already puts misaligned (highest-priority) entries
# first, so any truncation only ever drops the lower-priority tail.
MAX_RECONCILIATION_LINES = 25


def _today_utc_str(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def section_task_health(now: datetime | None = None) -> tuple[str, list[str]]:
    """
    Returns (checklist text, problems) -- one [x]/[!] line per monitored
    task rather than a collapsed "N healthy" summary, so a clean day still
    shows every task by name (what actually ran), not just an aggregate.
    problems is a flat list of "Task: reason" strings, consumed by
    compose_digest() to build the NEEDS YOUR ATTENTION section.
    """
    now = now or datetime.now(timezone.utc)
    task_results = _ahc.check_scheduled_tasks(now=now)
    litestream = _ahc.check_litestream_replica()
    last_run = _hb.get_last_run()
    hb_hours = _hb.hours_since(last_run["timestamp"], now=now) if last_run else None

    problems: list[str] = []
    lines = ["TASK CHECKLIST"]

    if last_run is None:
        lines.append("  [!] Daily pipeline (main.py) -- no runs recorded")
        problems.append("Daily pipeline (main.py): no runs recorded")
    elif hb_hours > _hb.DEFAULT_MAX_SILENCE_HOURS:
        lines.append(f"  [!] Daily pipeline (main.py) -- no successful run in {hb_hours:.1f}h")
        problems.append(f"Daily pipeline (main.py): no successful run in {hb_hours:.1f}h")
    else:
        lines.append(f"  [x] Daily pipeline (main.py) -- last run {hb_hours:.1f}h ago")

    for r in task_results:
        label = r["task"].replace("Leviathan-", "")
        # Weekly-cadence tasks (192h threshold) pass this check on every day
        # of their week, not just the day they actually ran -- "(weekly)"
        # keeps a bare [x] from reading as "ran today" on a Tuesday.
        cadence_hours = _ahc.TASK_CADENCE_HOURS.get(r["task"], 24.0)
        suffix = " (weekly)" if cadence_hours > 48.0 else ""
        if r["problem"]:
            lines.append(f"  [!] {label}{suffix} -- {r['problem']}")
            problems.append(f"{label}: {r['problem']}")
        else:
            lines.append(f"  [x] {label}{suffix}")

    if litestream["problem"]:
        lines.append(f"  [!] Litestream replica -- {litestream['problem']}")
        problems.append(f"Litestream replica: {litestream['problem']}")
    else:
        lines.append("  [x] Litestream replica")

    return "\n".join(lines), problems


def section_reconciliation(now: datetime | None = None) -> tuple[str, bool]:
    """Returns (section text, has_problem)."""
    date_str = _today_utc_str(now)
    path = RECONCILIATION_DIR / f"{date_str}.json"
    if not path.exists():
        return (f"RECONCILIATION\n  No reconciliation file for {date_str} yet "
                 "(Leviathan-PositionReconciliation runs ~9:15am)."), False

    data = json.loads(path.read_text(encoding="utf-8"))
    has_problem = bool(data.get("misaligned")) or bool(data.get("error"))

    lines = _recon.format_report(data, compact=True).splitlines()
    if len(lines) > MAX_RECONCILIATION_LINES:
        omitted = len(lines) - MAX_RECONCILIATION_LINES
        lines = lines[:MAX_RECONCILIATION_LINES] + [
            f"  ... ({omitted} more line(s) truncated -- see data/reconciliation/{path.name} for the full list)"]

    return "RECONCILIATION\n" + "\n".join(lines), has_problem


def section_smart_money(now: datetime | None = None) -> str:
    if not SMART_MONEY_LATEST.exists():
        return "SMART MONEY\n  No smart-money cache found."

    data = json.loads(SMART_MONEY_LATEST.read_text(encoding="utf-8"))
    run_at = data.get("run_at", "")
    is_today = run_at[:10] == _today_utc_str(now)
    freshness = "today" if is_today else f"stale -- last run {run_at[:19].replace('T', ' ')} UTC"
    return ("SMART MONEY\n"
            f"  Kalshi cross-reference signals: {data.get('signal_count', 0)} ({freshness})")


def section_resolve_first(now: datetime | None = None,
                           lookback_hours: float = RESOLVE_FIRST_LOOKBACK_HOURS) -> str:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=lookback_hours)).isoformat()
    with logger._db() as conn:
        rows = conn.execute(
            "SELECT direction, COUNT(*) as n FROM signals "
            "WHERE flag_path = 'RESOLVE_FIRST' AND timestamp > ? GROUP BY direction",
            (cutoff,),
        ).fetchall()

    header = "RESOLVE-FIRST (mechanical picks only -- a pick that also scored as a real " \
             "EDGE/DRIFT/HEURISTIC/BR_NONE signal is counted under that signal type instead)"
    if not rows:
        return f"{header}\n  No resolve-first-only signals logged in the last day."

    parts = ", ".join(f"{r['direction']}={r['n']}" for r in rows)
    total = sum(r["n"] for r in rows)
    return f"{header}\n  {total} signal(s) logged: {parts}"


def section_weekly_logs(now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    blocks = []
    for label, path in WEEKLY_LOGS.items():
        if not path.exists():
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if (now - mtime).total_seconds() / 3600.0 > WEEKLY_LOG_FRESHNESS_HOURS:
            continue
        tail_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-WEEKLY_LOG_TAIL_LINES:]
        block = f"  {label} (ran {mtime.strftime('%Y-%m-%d %H:%M')} UTC):\n" + \
                "\n".join(f"    {line}" for line in tail_lines)
        blocks.append(block)

    return "WEEKLY AUDITS\n" + "\n\n".join(blocks) if blocks else None


def _backlog_snapshot(items: list[dict]) -> dict:
    """
    Minimal per-item fingerprint for diffing against the previous digest's
    snapshot: status (catches transitions like ready->done) and notes
    length (catches investigation updates that don't change status --
    this backlog has far more of those than pure status flips, e.g. a
    "checked again, still doesn't reproduce" note with nothing to unlock).
    """
    return {i["id"]: {"status": i.get("status", ""), "notes_len": len(i.get("notes") or "")}
            for i in items}


def _change_purpose(item: dict, max_len: int = 280) -> str:
    """
    Best one-line account of *why* an item changed. This backlog's own
    convention (see backlog/backlog.json) is to append a new dated
    paragraph to `notes` per update, newest last -- so the last paragraph
    is normally the actual finding/purpose behind the most recent change.
    Falls back to the action text for brand-new items with no notes yet.
    """
    notes = (item.get("notes") or "").strip()
    text = notes.split("\n\n")[-1].strip() if notes else item.get("action", "")
    return _checker._summarize_action(text, max_len=max_len)


def section_backlog(prev_snapshot: dict | None = None) -> tuple[str, dict]:
    """
    Returns (section text, current snapshot). Two parts: every `ready`
    item (priority-ordered -- what's actionable right now, without
    opening the board or monday.com), and what changed since the
    previous digest (new items, status transitions, and notes-only
    updates) with the purpose pulled from the item's own notes/action
    text rather than re-derived. prev_snapshot is None/empty both on the
    very first run this feature ever executes AND on any later run where
    state was reset -- either way there's no real baseline to diff
    against, so the changes list is reported as "no prior snapshot" that
    one time rather than dumping the entire backlog as "new" (which would
    bury the one genuinely new item in dozens of pre-existing ones).
    """
    first_run = not prev_snapshot
    backlog = _load_backlog(BACKLOG_PATH)
    items = backlog.get("items", [])
    items_by_id = {i["id"]: i for i in items}
    snapshot = _backlog_snapshot(items)

    ready = sorted((i for i in items if i.get("status") == "ready"),
                   key=lambda i: (i.get("priority", 9), i["id"]))
    lines = ["BACKLOG", f"  Ready ({len(ready)}):"]
    if not ready:
        lines.append("    (none)")
    for item in ready:
        summary = _checker._summarize_action(item.get("action", ""), max_len=140)
        lines.append(f"    [{item.get('priority', '?')}] {item['id']} -- {summary}")

    lines.append("")
    if first_run:
        lines.append("  Changes since last digest: no prior snapshot yet (first run of this section) -- "
                      "baseline established, real diffs start next digest.")
        return "\n".join(lines), snapshot

    changes = []
    for item_id, snap in snapshot.items():
        prev = prev_snapshot.get(item_id)
        item = items_by_id[item_id]
        if prev is None:
            changes.append((item, f"new, {snap['status']}"))
        elif prev.get("status") != snap["status"]:
            changes.append((item, f"{prev.get('status')} -> {snap['status']}"))
        elif prev.get("notes_len") != snap["notes_len"]:
            changes.append((item, f"{snap['status']}, notes updated"))

    changes.sort(key=lambda c: (c[0].get("priority", 9), c[0]["id"]))
    lines.append(f"  Changes since last digest ({len(changes)}):")
    if not changes:
        lines.append("    (none)")
    for item, transition in changes:
        lines.append(f"    [{item.get('priority', '?')}] {item['id']} ({transition})")
        lines.append(f"        {_change_purpose(item)}")

    return "\n".join(lines), snapshot


def _attention_section(problems: list[str]) -> str:
    lines = ["NEEDS YOUR ATTENTION"]
    if not problems:
        lines.append("  Nothing -- every task on the checklist below completed normally.")
    else:
        for p in problems:
            lines.append(f"  - {p}")
    return "\n".join(lines)


def compose_digest(now: datetime | None = None,
                    prev_backlog_snapshot: dict | None = None) -> tuple[str, str]:
    now = now or datetime.now(timezone.utc)

    task_text, task_problems = section_task_health(now)
    recon_text, recon_problem = section_reconciliation(now)
    backlog_text, _ = section_backlog(prev_backlog_snapshot)

    problems = list(task_problems)
    if recon_problem:
        problems.append("Reconciliation: misaligned signal(s) found -- see RECONCILIATION section below")

    sections = [_attention_section(problems), task_text, recon_text,
                section_smart_money(now), section_resolve_first(now)]

    weekly = section_weekly_logs(now)
    if weekly:
        sections.append(weekly)
    sections.append(backlog_text)

    body = "\n\n".join(sections)
    flag = "[ATTENTION] " if problems else ""
    subject = f"Leviathan Daily Digest — {flag}{now.strftime('%Y-%m-%d')}"
    return body, subject


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def check(config: dict, state_path: Path = DEFAULT_STATE, dry_run: bool = False,
          now: datetime | None = None) -> dict:
    """
    Returns {"sent": bool, "dry_run": bool, "error": str|None,
    "skipped_duplicate": bool}.
    """
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    state = load_state(state_path)

    if not dry_run and state.get("last_sent") == today:
        return {"sent": False, "dry_run": dry_run, "error": None, "skipped_duplicate": True}

    prev_backlog_snapshot = state.get("backlog_snapshot", {})
    body, subject = compose_digest(now, prev_backlog_snapshot)

    if dry_run:
        print(subject)
        print()
        print(body)
        return {"sent": False, "dry_run": True, "error": None, "skipped_duplicate": False}

    try:
        send_report(body, signals=[], whale_flags=0, config=config, subject_override=subject)
    except Exception as e:
        print(f"[daily_digest] send FAILED — state NOT persisted, will retry next run: {e}")
        return {"sent": False, "dry_run": False, "error": str(e), "skipped_duplicate": False}

    backlog_snapshot = _backlog_snapshot(_load_backlog(BACKLOG_PATH).get("items", []))
    save_state(state_path, {"last_sent": today, "backlog_snapshot": backlog_snapshot})
    print(f"[daily_digest] sent for {today}")
    return {"sent": True, "dry_run": False, "error": None, "skipped_duplicate": False}


def load_config() -> dict:
    cfg_path = ROOT / "config.json"
    if not cfg_path.exists():
        cfg_path = ROOT / "config.example.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Leviathan daily operations digest")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the digest, send nothing, persist nothing")
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    args = parser.parse_args()

    config = load_config()
    result = check(config, Path(args.state), dry_run=args.dry_run)

    if result["skipped_duplicate"]:
        print("[daily_digest] already sent today — skipping")

    sys.exit(1 if result.get("error") else 0)


if __name__ == "__main__":
    main()
