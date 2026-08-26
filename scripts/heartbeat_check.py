"""
scripts/heartbeat_check.py — Unattended-ops heartbeat (alert on absence).

Leviathan's main pipeline (main.py) is scheduled once daily via Windows
Task Scheduler (scripts/schedule_setup.ps1, 7:00 AM by default). If that
scheduler silently stops firing -- disabled, machine restarted with the
task not re-enabled, credentials expired, the account locked out -- nothing
inside main.py itself can ever detect it, since main.py never runs at all.
This script is a separate, independently-scheduled watcher: it checks the
`runs` table for the most recent row's timestamp and emails an alert if
none has landed within the configured window. See
scripts/setup_heartbeat_scheduler.ps1 to schedule it, and docs/RUNBOOK.md
for what to do when it fires.

A `runs` row is written only at main.py's step 7 (core.logger.log_run),
after most of the pipeline has already run — so "no runs row recently"
means either the scheduler didn't fire, an early step (most commonly
Kalshi auth) failed and main() returned before step 7, or an uncaught
exception hit somewhere between steps 1 and 7. This script can't tell
those apart on its own; docs/RUNBOOK.md covers how to.

Fire-once semantics per stale period: state persisted in
data/heartbeat_state.json (git-ignored) records the run_id last alerted
against, so re-running this script before a new pipeline run lands doesn't
re-send the same alert every time. Once a newer run_id appears, the
state resets and a future silence is free to alert again.

Usage:
    python scripts/heartbeat_check.py              # normal run
    python scripts/heartbeat_check.py --dry-run    # print, send nothing, persist nothing
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from core import logger
from core.report import send_report

DEFAULT_STATE = ROOT / "data" / "heartbeat_state.json"
DEFAULT_MAX_SILENCE_HOURS = 30.0  # 7:00 AM daily run + real margin for timing jitter


def get_last_run() -> dict | None:
    """Most recent row from the runs table, or None if the table is empty."""
    with logger._db() as conn:
        row = conn.execute(
            "SELECT run_id, timestamp FROM runs ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def hours_since(timestamp_iso: str, now: datetime | None = None) -> float:
    """
    2026-08-25: now accepts an optional `now` -- previously always used
    real datetime.now(), even when called from daily_digest.py's
    section_task_health(now=...) and catchup_missed_tasks.py's
    find_stale_tasks(now=...), both of which already thread a `now`
    through to check_scheduled_tasks() but had no way to give this
    function the same frozen time. Found because tests/test_daily_digest.py
    and tests/test_catchup_missed_tasks.py hardcode a fixed NOW constant
    and started failing as real wall-clock time passed it by more than
    DEFAULT_MAX_SILENCE_HOURS -- a ticking-time-bomb test bug, not
    anything actually wrong with either script's live behavior. Defaults
    to real time so every existing real caller is unaffected.
    """
    now = now or datetime.now(timezone.utc)
    ts = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / 3600.0


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def compose_email(last_run: dict | None, hours: float | None, max_silence: float) -> tuple[str, str]:
    if last_run is None:
        subject = "Leviathan ALERT — no runs ever recorded"
        body = (
            "The `runs` table has no rows at all. Either the pipeline has "
            "never completed through step 7, or leviathan.db is missing "
            "or was recreated.\n\n"
            "See docs/RUNBOOK.md — start with 'No runs row at all'."
        )
        return body, subject

    subject = f"Leviathan ALERT — no successful run in {hours:.0f}h (threshold {max_silence:.0f}h)"
    body = (
        f"No pipeline run has reached step 7 (signal logging) in "
        f"{hours:.1f} hours (threshold: {max_silence:.0f}h).\n\n"
        f"Last known run: {last_run['run_id']} at {last_run['timestamp']}\n\n"
        "This means either the scheduled task stopped firing, an early "
        "step (most commonly Kalshi auth) failed and the run returned "
        "before logging, or something crashed uncaught partway through.\n\n"
        "See docs/RUNBOOK.md for the full diagnostic checklist — start "
        "with 'No successful run alert fired'."
    )
    return body, subject


def check(config: dict, state_path: Path = DEFAULT_STATE,
          max_silence_hours: float = DEFAULT_MAX_SILENCE_HOURS,
          dry_run: bool = False) -> dict:
    """
    Returns {"stale": bool, "last_run": dict|None, "hours_silent": float|None,
    "sent": bool, "dry_run": bool, "error": str|None}.
    """
    last_run = get_last_run()
    hours = hours_since(last_run["timestamp"]) if last_run else None
    stale = last_run is None or hours > max_silence_hours

    if not stale:
        return {"stale": False, "last_run": last_run, "hours_silent": hours,
                "sent": False, "dry_run": dry_run, "error": None}

    state = load_state(state_path)
    already_alerted_for = state.get("alerted_run_id")
    current_run_id = last_run["run_id"] if last_run else None
    # Fire-once: only re-alert if the "last known run" has changed since the
    # prior alert (i.e. don't re-send the same alert every heartbeat tick
    # while still waiting on the same stale state) -- except when there has
    # never been a run at all (current_run_id is None), where every check
    # legitimately re-alerts since there's no run_id to dedupe against.
    if current_run_id is not None and already_alerted_for == current_run_id:
        return {"stale": True, "last_run": last_run, "hours_silent": hours,
                "sent": False, "dry_run": dry_run, "error": None}

    body, subject = compose_email(last_run, hours, max_silence_hours)

    if dry_run:
        print(subject)
        print()
        print(body)
        return {"stale": True, "last_run": last_run, "hours_silent": hours,
                "sent": False, "dry_run": True, "error": None}

    try:
        send_report(body, signals=[], whale_flags=0, config=config, subject_override=subject)
    except Exception as e:
        print(f"[heartbeat_check] send FAILED — state NOT persisted, will retry next check: {e}")
        return {"stale": True, "last_run": last_run, "hours_silent": hours,
                "sent": False, "dry_run": False, "error": str(e)}

    save_state(state_path, {"alerted_run_id": current_run_id,
                            "alerted_at": datetime.now(timezone.utc).isoformat()})
    print(f"[heartbeat_check] ALERT sent: no successful run in {hours:.1f}h" if last_run
          else "[heartbeat_check] ALERT sent: no runs ever recorded")
    return {"stale": True, "last_run": last_run, "hours_silent": hours,
            "sent": True, "dry_run": False, "error": None}


def load_config() -> dict:
    cfg_path = ROOT / "config.json"
    if not cfg_path.exists():
        cfg_path = ROOT / "config.example.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Leviathan run-absence heartbeat")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print alert if stale, send nothing, persist nothing")
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--max-silence-hours", type=float, default=DEFAULT_MAX_SILENCE_HOURS)
    args = parser.parse_args()

    config = load_config()
    result = check(config, Path(args.state), args.max_silence_hours, dry_run=args.dry_run)

    if not result["stale"]:
        print(f"[heartbeat_check] OK — last run {result['hours_silent']:.1f}h ago")

    sys.exit(1 if result.get("error") else 0)


if __name__ == "__main__":
    main()
