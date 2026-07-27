# Leviathan — Unattended Operations Runbook

For diagnosing a failed or missing run without reloading full project
context. If you're reading this because an alert email arrived, find the
matching heading below and start there.

---

## "No successful run alert fired" (from scripts/heartbeat_check.py)

This means no row has been written to the `runs` table within the
configured window (default 30 hours). A `runs` row is only written at
`main.py`'s step 7 (`core.logger.log_run`), near the end of the pipeline
— so this alert does not distinguish between:

1. **The scheduled task didn't fire at all.**
   ```powershell
   Get-ScheduledTask -TaskName Leviathan-DailyRun
   Get-ScheduledTaskInfo -TaskName Leviathan-DailyRun
   ```
   Check `State` is `Ready` (not `Disabled`) and `LastTaskResult` is `0`.
   If the task is missing entirely, re-register it:
   `powershell -ExecutionPolicy Bypass -File scripts\schedule_setup.ps1`

2. **The task fired, but the run failed early** — most commonly Kalshi
   auth (step 1/8). Check the most recent scheduled-task run's captured
   output (Task Scheduler doesn't retain stdout by default; if you don't
   have a log redirect configured, re-run manually to see the error):
   ```powershell
   cd C:\Users\Administrator\Downloads\Leviathan
   python main.py
   ```
   Common causes: `KALSHI_KEY_ID`/private key in `.env` expired or
   rotated; Kalshi API outage (check https://status.kalshi.com if it
   exists, or just retry — transient outages resolve themselves).

3. **Something crashed uncaught between steps 1 and 7.** Every step in
   `main.py` is wrapped in its own `try/except` that prints and continues
   — so this is rarer, but possible if a bug slips past that pattern.
   Re-run manually (above) and read the traceback.

**To resolve:** once a run completes successfully, the next heartbeat
check will see a fresh `runs` row and stop alerting automatically — no
manual state reset needed for the common case. If you changed
`config.markets.max_events`/etc. and want to confirm quickly without
waiting for the next scheduled run, just run `python main.py` directly.

---

## "No runs ever recorded" (from scripts/heartbeat_check.py)

The `runs` table is completely empty. Either:
- This is a fresh install/database and no run has ever completed — expected,
  not an error, until the first run lands.
- `data/leviathan.db` is missing, was deleted, or points somewhere
  unexpected. Check `core.logger.DB_PATH` and confirm the file exists and
  has a `runs` table:
  ```powershell
  python -c "from core import logger; print(logger.DB_PATH)"
  ```

---

## "API shape anomaly detected, run aborted" (from main.py step 2)

More than `config.markets.shape_anomaly_threshold` (default 50%) of the
markets fetched this run were missing one or more fields the rest of the
pipeline assumes exist (`ticker`, `close_time`, `yes_bid_dollars`,
`yes_ask_dollars`, `title`). The run aborted **before scoring** rather
than silently process what's likely a Kalshi API response shape change
as if it were real, complete data — no `runs` row is written for an
aborted run, so `heartbeat_check.py` will also start counting toward its
own alert if this keeps happening.

**To diagnose:**
1. The alert email lists exactly which fields were missing and on how
   many markets — that tells you what changed. Fetch a market manually
   and inspect the raw response shape:
   ```python
   from core import kalshi
   import json
   config = {...}  # load config.json
   events = kalshi.fetch_events(config)
   markets = kalshi.fetch_event_markets(config, events[0]["event_ticker"])
   print(json.dumps(markets[0], indent=2))
   ```
2. Compare against a recent snapshot in `data/snapshots/` to see the
   field names Kalshi used to return.
3. If Kalshi genuinely renamed/removed a field, the fix is in
   `core/kalshi.py` (the fetch functions) — this codebase has already hit
   this exact failure mode more than once (see `docs/PROGRESS.md`,
   2026-07-25 bug sweep: `fetch_orderbook`/`fetch_trades` both assumed a
   response shape that turned out not to exist). Verify the real shape
   with a live request before changing code, the same way those fixes
   were made.
4. If this was a false alarm (e.g. Kalshi legitimately returned very few
   markets today, not malformed ones), check
   `config.markets.shape_anomaly_min_sample`/`shape_anomaly_threshold`
   aren't miscalibrated — the check only fires when at least
   `shape_anomaly_min_sample` (default 20) markets were fetched at all.

---

## Two `runs` rows recorded close together (concurrent/duplicate-looking run)

This means two `main.py` processes ran at nearly the same time, both
scanning the same markets. It looks alarming but has a mundane cause every
time it's been seen so far (2026-07-27): `Leviathan-DailyRun`'s trigger is
set for a fixed local time, but its Settings also have `StartWhenAvailable:
True` and `DisallowStartIfOnBatteries: True`. If the machine was on
battery power (or otherwise unavailable) at the scheduled time, Task
Scheduler skips that slot and catches up later once conditions allow —
which can land within a couple of minutes of someone separately running
`python main.py` by hand, producing two `runs` rows close together purely
by coincidence.

`MultipleInstances: IgnoreNew` on the task does NOT protect against this:
it only stops Task Scheduler from launching a second instance of *its own*
registered task while one is already running. A manually-invoked
`python main.py` bypasses Task Scheduler entirely, so there's no mechanism
that prevents it from overlapping with a Task-Scheduler-launched instance.

**To diagnose (requires the Task Scheduler Operational event log to be
enabled — see Quick reference below to check/enable it):**
```powershell
Get-WinEvent -LogName "Microsoft-Windows-TaskScheduler/Operational" |
  Where-Object { $_.Message -like "*Leviathan-DailyRun*" } |
  Select-Object TimeCreated, Id, Message
```
This shows whether a given launch came from the task's own trigger firing
(on schedule, or a delayed catch-up) versus a manual `Start-ScheduledTask`/
`schtasks /run` invocation. Cross-reference the timestamps against
`Get-ScheduledTaskInfo -TaskName Leviathan-DailyRun`'s `LastRunTime` and
the `runs` table's own `timestamp` column (UTC — convert to local via
`Get-TimeZone` before comparing against `LastRunTime`, which is local).

**If this becomes a recurring problem** rather than a one-off coincidence,
the fix is changing the task's settings, not the code: either disable
`DisallowStartIfOnBatteries` for this task (if it's expected to run
unattended regardless of power state) or accept the occasional overlap —
two concurrent scans against the same SQLite DB haven't been observed to
corrupt anything (SQLite's own locking handles concurrent writes), just to
waste one run's worth of Kalshi/Claude calls.

---

## Quick reference

| Task | Command |
|---|---|
| Run the pipeline manually | `python main.py` (7-20 min, real Kalshi/Claude calls, sends the real report email) |
| Check last run without running it | `python scripts/heartbeat_check.py --dry-run` |
| Check scheduled task status | `Get-ScheduledTaskInfo -TaskName Leviathan-DailyRun` |
| Re-register the daily run scheduler | `powershell -ExecutionPolicy Bypass -File scripts\schedule_setup.ps1` |
| Re-register the heartbeat scheduler | `powershell -ExecutionPolicy Bypass -File scripts\setup_heartbeat_scheduler.ps1` |
| Check today's LLM daily spend (metered API only) | `python -c "from core.llm import get_daily_cost_usd; print(get_daily_cost_usd())"` |
| Check DB location | `python -c "from core import logger; print(logger.DB_PATH)"` |
| Full test suite | `python -m pytest -q` |
| Check Task Scheduler event log is enabled | `Get-WinEvent -ListLog "Microsoft-Windows-TaskScheduler/Operational" \| Select IsEnabled` |
| Enable it if not (requires Administrator) | `wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true` — from Git Bash, prefix with `MSYS_NO_PATHCONV=1` or its automatic POSIX-path conversion mangles the `/e:true` argument |
| View recent scheduled-task activity | `Get-WinEvent -LogName "Microsoft-Windows-TaskScheduler/Operational" -MaxEvents 50` |
