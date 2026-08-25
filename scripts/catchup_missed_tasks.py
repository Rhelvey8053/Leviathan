"""
scripts/catchup_missed_tasks.py -- Catch-up for missed scheduled runs,
fired on wake, on logon, and once daily.

2026-08-24 finding: the machine slept through Leviathan-DailyRun's 6am
trigger (asleep roughly 12:18am-10:16am local) and, despite
StartWhenAvailable=True on the task, Task Scheduler never actually
launched it on wake -- NextRunTime silently advanced to the next day
with no launch attempt logged anywhere in the Task Scheduler operational
log. This script is the fallback: instead of trusting Task Scheduler's
own catch-up behavior, it explicitly checks each task's real staleness
(reusing heartbeat_check's and automation_health_check's own detection
logic -- not reinventing it) and re-launches anything that's overdue.

Registered on three separate triggers -- see
scripts/setup_wake_catchup_scheduler.ps1:
  - Wake-from-sleep (Kernel-Power Event ID 1), not logon alone, since
    this machine's console session stays "Active" through sleep/resume
    without necessarily generating a fresh logon event.
  - At logon, specifically for a full shutdown -> cold boot, which a
    wake event never fires for at all.
  - A fixed daily time (noon), for the case neither of the above covers
    at all: the machine just left on and awake the whole day. Without
    this, a genuinely missed task could sit unfixed indefinitely on a
    machine that never sleeps or logs out.

Caveat, stated plainly rather than assumed away: 2026-08-24 also found
that manually-triggered Start-ScheduledTask calls on this machine were,
at the time, getting stuck in "Queued" and never actually spawning a
process -- on tasks both old and newly-registered alike. That's a live
Task-Scheduler/session issue this script's own logic cannot detect or
work around; if it's still present when this fires, the
Start-ScheduledTask calls below may silently do nothing even though this
script reports "launched". This has not been (and, short of an actual
sleep/wake cycle, cannot be) end-to-end verified from an automated
session -- the first real wake is the real test, not this script's own
tests, which only cover its decision logic (which tasks it judges
stale), not whether Windows actually delivers the launch.

Scope: Leviathan-DailyRun + the 8 tasks automation_health_check.py
already monitors (GateNotifier, PositionReconciliation, ResolveFirst,
SmartMoneyScan, SubscriberReport, Heartbeat, CodeAudit, WeeklyAudit).
Deliberately excludes Litestream (a continuous process, not a
daily-batch task -- its own failure mode was a zombie process blocking
relaunch, which restarting the task doesn't reliably fix on its own; see
docs/RUNBOOK.md) and this script's own siblings (AutomationHealthCheck,
DailyDigest -- newer, lower-stakes, kept out to avoid any
self-referential complexity).

Usage:
    python scripts/catchup_missed_tasks.py              # normal run
    python scripts/catchup_missed_tasks.py --dry-run    # print what would launch, launch nothing
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

import scripts.automation_health_check as _ahc
import scripts.heartbeat_check as _hb

DAILY_RUN_TASK = "Leviathan-DailyRun"


def find_stale_tasks(now: datetime | None = None) -> list[str]:
    """
    Returns Task Scheduler names of every task that looks overdue right
    now -- staleness only, not tasks whose last run merely returned a
    non-zero result code (that's a different failure mode; re-launching
    isn't this script's job to attempt).
    """
    now = now or datetime.now(timezone.utc)
    stale = []

    last_run = _hb.get_last_run()
    hours = _hb.hours_since(last_run["timestamp"]) if last_run else None
    if last_run is None or hours > _hb.DEFAULT_MAX_SILENCE_HOURS:
        stale.append(DAILY_RUN_TASK)

    for r in _ahc.check_scheduled_tasks(now=now):
        problem = r["problem"] or ""
        if "no run in" in problem or "has never run" in problem:
            stale.append(r["task"])

    return stale


def launch_task(task_name: str) -> tuple[bool, str]:
    """Fires Start-ScheduledTask for one task. Returns (ok, message)."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"Start-ScheduledTask -TaskName '{task_name}'"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return False, (result.stderr.strip() or f"exit code {result.returncode}")
    return True, "launched"


def run(dry_run: bool = False, now: datetime | None = None) -> dict:
    """Returns {"stale": list[str], "launched": list[str], "failed": dict[str, str]}."""
    stale = find_stale_tasks(now=now)
    if not stale:
        print("[catchup_missed_tasks] nothing overdue")
        return {"stale": [], "launched": [], "failed": {}}

    print(f"[catchup_missed_tasks] overdue: {', '.join(stale)}")
    if dry_run:
        return {"stale": stale, "launched": [], "failed": {}}

    launched, failed = [], {}
    for task in stale:
        ok, msg = launch_task(task)
        if ok:
            launched.append(task)
            print(f"[catchup_missed_tasks] launched {task}")
        else:
            failed[task] = msg
            print(f"[catchup_missed_tasks] FAILED to launch {task}: {msg}")

    return {"stale": stale, "launched": launched, "failed": failed}


def main():
    parser = argparse.ArgumentParser(
        description="Wake-triggered catch-up for missed Leviathan scheduled tasks")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be launched, launch nothing")
    args = parser.parse_args()

    print(f"\n[catchup_missed_tasks] {datetime.now(timezone.utc).isoformat()}")
    result = run(dry_run=args.dry_run)
    sys.exit(1 if result["failed"] else 0)


if __name__ == "__main__":
    main()
