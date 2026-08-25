"""
scripts/automation_health_check.py -- Unattended-ops watcher for scheduled-
task drift and Litestream replica lag.

heartbeat_check.py already answers "did the daily pipeline complete
recently?" by reading the runs table -- the deepest signal available for
that one task. This script answers a different question: are the OTHER
scheduled Windows tasks (gate notifier, position reconciliation, weekly
audit, etc.) actually still firing, and is Litestream's continuous DB
backup actually keeping up with the live database? Both gaps were real,
not hypothetical: Leviathan-CodeAudit's own Task Scheduler result code sat
unexamined for weeks, and Litestream silently stopped replicating for
~5.5h behind a zombie process (MultipleInstances: IgnoreNew silently
skipped every relaunch attempt) before this script existed to catch it
(2026-08-23 incident, fixed by hand -- see docs/RUNBOOK.md).

Deliberately excludes:
  - Leviathan-DailyRun: heartbeat_check.py already covers it, and more
    deeply (pipeline actually reached step 7, not just "Task Scheduler
    thinks it fired").
  - Leviathan-Litestream's own Task Scheduler LastRunTime: it's a
    continuous "run once, then loop forever" task, so LastRunTime only
    ever reflects when the wrapper process last (re)started, not whether
    it's still usefully replicating. Checked instead via
    check_litestream_replica() -- how far the replica's newest file lags
    behind the live DB's own last-modified time, which is exactly the
    signal that would have caught the 2026-08-23 incident.

Unlike heartbeat_check.py's fire-once-until-resolved semantics (keyed off
a run_id that naturally changes once the pipeline succeeds again),
Task-Scheduler drift and replica lag have no equivalent "resolved" signal
this script can detect between runs -- so state
(data/automation_health_state.json, git-ignored) dedupes by calendar day
per problem signature: alerts once per UTC day per distinct problem, and
keeps re-alerting daily for as long as that problem persists, rather than
going silent forever until someone happens to notice and fix it.

Usage:
    python scripts/automation_health_check.py              # normal run
    python scripts/automation_health_check.py --dry-run    # print, send/persist nothing
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from core.report import send_report

DEFAULT_STATE = ROOT / "data" / "automation_health_state.json"
LITESTREAM_REPLICA_DIR = ROOT / "data" / "db_backups" / "litestream_replica"
LIVE_DB_PATH = ROOT / "data" / "leviathan.db"

# Expected max hours between scheduled runs, plus real margin for timing
# jitter -- same philosophy as heartbeat_check.py's own
# DEFAULT_MAX_SILENCE_HOURS. Leviathan-DailyRun and Leviathan-Litestream
# are deliberately absent -- see module docstring.
TASK_CADENCE_HOURS = {
    "Leviathan-GateNotifier":            30.0,   # daily ~8:45am
    "Leviathan-PositionReconciliation":  30.0,   # daily ~9:15am
    "Leviathan-ResolveFirst":            30.0,   # daily ~8:30am
    "Leviathan-SmartMoneyScan":          30.0,   # daily ~7:07am
    "Leviathan-Heartbeat":               24.0,   # twice daily (2pm, 8pm) -- worst-case gap
                                                  # between runs is 18h (8pm -> next 2pm), so
                                                  # 14h would false-positive every single
                                                  # morning; 24h keeps the same ~6h margin
                                                  # philosophy as the other daily entries below
    "Leviathan-CodeAudit":              192.0,   # weekly, Monday 11am (+~24h margin)
    "Leviathan-WeeklyAudit":            192.0,   # weekly, Monday 10am (+~24h margin)
}

# Win32/Task-Scheduler result codes that are informational, not failures --
# surfaced in the detail listing but never alerted on their own. 267014
# (SCHED_S_TASK_TERMINATED) has shown up consistently on Leviathan-CodeAudit
# and Leviathan-DailyRun without any corroborating sign of real failure
# (2026-08-23 audit) -- not fully explained, so it's allowlisted as benign
# rather than silenced by assumption; worth digging into further if it's
# ever seen alongside an actual symptom.
BENIGN_RESULT_CODES = {0, 267014}


def _parse_dotnet_date(raw) -> datetime | None:
    """Parses PowerShell ConvertTo-Json's '/Date(ms)/' epoch format."""
    if not raw:
        return None
    m = re.search(r"/Date\((\d+)\)/", raw)
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)


def get_raw_task_info() -> list[dict]:
    """
    Shells out to Task Scheduler for every Leviathan-* task's current
    LastRunTime/LastTaskResult. Isolated in its own function so tests can
    patch it directly rather than mocking subprocess/PowerShell.
    """
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-ScheduledTask -TaskName 'Leviathan-*' | Get-ScheduledTaskInfo | "
         "Select-Object TaskName, LastRunTime, LastTaskResult | ConvertTo-Json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Get-ScheduledTask failed: {result.stderr.strip()}")
    raw = json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(raw, dict):  # a single task comes back as a dict, not a list
        raw = [raw]
    return raw


def check_scheduled_tasks(now: datetime | None = None) -> list[dict]:
    """
    Returns one dict per entry in TASK_CADENCE_HOURS:
    {"task": str, "problem": str|None, "hours_since_run": float|None,
     "last_result": int|None}
    problem is None when the task is healthy.
    """
    now = now or datetime.now(timezone.utc)
    try:
        raw_tasks = {t["TaskName"]: t for t in get_raw_task_info()}
    except Exception as e:
        return [{"task": name, "problem": f"could not query Task Scheduler: {e}",
                  "hours_since_run": None, "last_result": None}
                for name in TASK_CADENCE_HOURS]

    results = []
    for name, max_hours in TASK_CADENCE_HOURS.items():
        info = raw_tasks.get(name)
        if info is None:
            results.append({"task": name,
                             "problem": "task not found in Task Scheduler (deleted or unregistered)",
                             "hours_since_run": None, "last_result": None})
            continue

        last_run = _parse_dotnet_date(info.get("LastRunTime"))
        last_result = info.get("LastTaskResult")
        hours_since = (now - last_run).total_seconds() / 3600.0 if last_run else None

        # Staleness and a bad result code are independent problems -- a task
        # that errored out on its last run and then never ran again should
        # report BOTH in the alert, not have the error code silently
        # dropped because the staleness check happened to match first.
        problem_parts = []
        if last_run is None:
            problem_parts.append("has never run")
        else:
            if hours_since > max_hours:
                problem_parts.append(f"no run in {hours_since:.1f}h (threshold {max_hours:.0f}h)")
            if last_result is not None and last_result != 0 and last_result not in BENIGN_RESULT_CODES:
                problem_parts.append(f"last run returned result code {last_result} (non-zero, not in known-benign list)")
        problem = "; ".join(problem_parts) if problem_parts else None

        results.append({"task": name, "problem": problem, "hours_since_run": hours_since,
                         "last_result": last_result})
    return results


def check_litestream_replica(replica_dir: Path | None = None,
                              live_db_path: Path | None = None,
                              max_lag_hours: float = 3.0) -> dict:
    """
    Compares the replica's newest file mtime against the live DB's own
    mtime. A large gap means the replica has stopped keeping up with the
    live DB -- exactly the signature of the 2026-08-23 zombie-process
    incident (litestream.exe alive but blocked from ever relaunching after
    a restart, silently not replicating for ~5.5h before manual catch).
    Returns {"problem": str|None, "lag_hours": float|None}.

    replica_dir/live_db_path default to the module-level constants,
    resolved at call time (not bound as default-arg values) so tests can
    patch the module attributes directly.
    """
    if replica_dir is None:
        replica_dir = LITESTREAM_REPLICA_DIR
    if live_db_path is None:
        live_db_path = LIVE_DB_PATH

    if not live_db_path.exists():
        return {"problem": f"live DB not found at {live_db_path}", "lag_hours": None}

    replica_files = [f for f in replica_dir.rglob("*") if f.is_file()] if replica_dir.exists() else []
    if not replica_files:
        return {"problem": f"no replica files found under {replica_dir}", "lag_hours": None}

    newest_replica_mtime = max(f.stat().st_mtime for f in replica_files)
    db_mtime = live_db_path.stat().st_mtime
    lag_hours = max(0.0, (db_mtime - newest_replica_mtime) / 3600.0)

    problem = None
    if lag_hours > max_lag_hours:
        problem = f"replica is {lag_hours:.1f}h behind the live DB (threshold {max_lag_hours:.0f}h)"
    return {"problem": problem, "lag_hours": lag_hours}


def compose_email(task_results: list[dict], litestream_result: dict) -> tuple[str, str]:
    task_problems = [r for r in task_results if r["problem"]]
    lines = []
    if task_problems:
        lines.append("Scheduled tasks with a problem:")
        for r in task_problems:
            lines.append(f"  - {r['task']}: {r['problem']}")
        lines.append("")
    if litestream_result["problem"]:
        lines.append(f"Litestream replica: {litestream_result['problem']}")
        lines.append("")
    lines.append("See docs/RUNBOOK.md for what to do about a stale scheduled task "
                  "or a lagging Litestream replica.")
    body = "\n".join(lines)
    n = len(task_problems) + (1 if litestream_result["problem"] else 0)
    subject = f"Leviathan ALERT — automation health: {n} problem{'s' if n != 1 else ''} found"
    return body, subject


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def check(config: dict, state_path: Path = DEFAULT_STATE, dry_run: bool = False) -> dict:
    """
    Returns {"healthy": bool, "task_results": list[dict], "litestream": dict,
    "sent": bool, "dry_run": bool, "error": str|None}.
    """
    task_results = check_scheduled_tasks()
    litestream_result = check_litestream_replica()

    problems = {f"task:{r['task']}": r["problem"] for r in task_results if r["problem"]}
    if litestream_result["problem"]:
        problems["litestream"] = litestream_result["problem"]

    if not problems:
        return {"healthy": True, "task_results": task_results, "litestream": litestream_result,
                "sent": False, "dry_run": dry_run, "error": None}

    today = datetime.now(timezone.utc).date().isoformat()
    prior_alerts = load_state(state_path).get("alerted", {})
    # Only skip sending if EVERY current problem was already alerted today --
    # a new problem (or the day rolling over) still triggers a fresh send.
    if all(prior_alerts.get(sig) == today for sig in problems):
        return {"healthy": False, "task_results": task_results, "litestream": litestream_result,
                "sent": False, "dry_run": dry_run, "error": None}

    body, subject = compose_email(task_results, litestream_result)

    if dry_run:
        print(subject)
        print()
        print(body)
        return {"healthy": False, "task_results": task_results, "litestream": litestream_result,
                "sent": False, "dry_run": True, "error": None}

    try:
        send_report(body, signals=[], whale_flags=0, config=config, subject_override=subject)
    except Exception as e:
        print(f"[automation_health_check] send FAILED — state NOT persisted, will retry next check: {e}")
        return {"healthy": False, "task_results": task_results, "litestream": litestream_result,
                "sent": False, "dry_run": False, "error": str(e)}

    save_state(state_path, {"alerted": {sig: today for sig in problems}})
    print(f"[automation_health_check] ALERT sent: {len(problems)} problem(s)")
    return {"healthy": False, "task_results": task_results, "litestream": litestream_result,
            "sent": True, "dry_run": False, "error": None}


def load_config() -> dict:
    cfg_path = ROOT / "config.json"
    if not cfg_path.exists():
        cfg_path = ROOT / "config.example.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Leviathan scheduled-task + Litestream automation health check")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print alert if unhealthy, send nothing, persist nothing")
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    args = parser.parse_args()

    config = load_config()
    result = check(config, Path(args.state), dry_run=args.dry_run)

    if result["healthy"]:
        print("[automation_health_check] OK — all scheduled tasks and Litestream replica healthy")

    sys.exit(1 if result.get("error") else 0)


if __name__ == "__main__":
    main()
