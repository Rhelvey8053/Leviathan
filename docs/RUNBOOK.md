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

## "Leviathan ALERT — automation health: N problem(s) found" (from scripts/automation_health_check.py)

Unlike the heartbeat above (which watches only `main.py`'s own completion),
this checks the *other* scheduled tasks (gate notifier, position
reconciliation, weekly audit, etc.) plus Litestream's continuous DB
backup. The email body lists each specific problem; the two shapes are:

1. **A scheduled task listed with "no run in Xh"** — Task Scheduler's
   `LastRunTime` for that task is older than its expected cadence. Check
   its actual state:
   ```powershell
   Get-ScheduledTaskInfo -TaskName <TaskName>
   ```
   If `State` is `Disabled`, re-enable it. If the task is missing
   entirely (alert says "not found in Task Scheduler"), it was deleted or
   never registered — re-run the matching `scripts/setup_*.ps1` for it.
   A common root cause for a task that's `Ready` but still stale: a prior
   run left a zombie process alive, and `MultipleInstances: IgnoreNew`
   (the default in this project's task setup scripts) silently skips every
   relaunch attempt rather than erroring — check for a leftover process
   with the task's own executable name and kill it before restarting the
   task.

2. **A task listed with "result code N"** — its last run exited non-zero
   (and N isn't 267014, the one code seen often enough without a
   corroborating failure that it's allowlisted as benign — see
   `BENIGN_RESULT_CODES` in the script). Decode any other code:
   ```powershell
   [System.ComponentModel.Win32Exception]::new(<N>).Message
   ```

3. **"Litestream replica: replica is Xh behind the live DB"** — the
   continuous backup has stopped keeping up with `data/leviathan.db`. This
   is exactly the 2026-08-23 incident: a zombie `litestream.exe` blocked
   by `MultipleInstances: IgnoreNew` from ever relaunching after a
   restart. Check for a stray process, kill it, then restart the task:
   ```powershell
   Get-Process litestream
   Stop-Process -Id <PID> -Force   # only the stale one, if more than one shows up
   Start-ScheduledTask -TaskName Leviathan-Litestream
   ```
   Verify the fix actually replicates, don't just trust the process is
   alive — restore and compare row counts against the live DB:
   ```powershell
   tools\litestream.exe restore -config tools\litestream.yml -o <output.db> data\leviathan.db
   ```

**To resolve:** unlike the heartbeat above, this alert has no
automatically-detectable "resolved" signal, so it re-alerts once per UTC
day for as long as the underlying problem is still present on the next
check — fixing the task/process is what stops it, not any manual state
reset.

---

## "Leviathan ALERT — PnL integrity: N row(s) mismatched" (from scripts/verify_pnl.py, weekly)

`resolve_outcomes()`'s stored `pnl_if_traded` for one or more resolved
signals no longer matches what the current payoff formula in
`core/logger.py` would compute from the same stored inputs (market price,
entry price/fill data, direction, outcome) — meaning either the formula
changed after those rows resolved and they were never backfilled, or the
stored data is otherwise inconsistent. This has never actually happened
in production; the check exists so a future formula change (like the
2026-09-08 dollar-scaling fix in `get_stats()`, a related but different
column) can't silently leave old rows wrong the way that fix's own
backfill deliberately avoided.

The alert email lists each mismatched `call_id`/ticker with its stored vs.
recomputed value. To fix:
```
python scripts/verify_pnl.py            # confirm the diff table again
python scripts/verify_pnl.py --apply    # backs up the DB, then writes the fix
```
Never run `--apply` without reading the diff table first — it's a
one-time bulk UPDATE across every mismatched row, not scoped to any
particular fix in mind. Re-alerts once per day for a *different* set of
mismatched rows, but not for the same still-unresolved set (state in
`data/verify_pnl_alert_state.json`) — applying the fix is what actually
stops it, not any separate manual acknowledgement.

---

## `weekly_code_audit.py` timed out / raw traceback in logs/weekly_code_audit.log

The Sunday 11am `claude --print` audit run ran out of its time budget
(`TIMEOUT_SECONDS` in scripts/weekly_code_audit.py, currently 3600s) and
no report landed in `reports/code_audits/`. Two ways this surfaces:

- `automation_health_check.py`'s task-health section flags
  `Leviathan-CodeAudit` with a non-zero/non-benign result code (it's one
  of the 7 tasks that script monitors).
- `daily_digest.py`'s weekly-audit-log-tail section shows the failure
  the next day it's included (log modified within the last ~20h).

Both can fire for the same underlying event — that's expected, not a
double-count bug.

**Known root cause (2026-08-23, fixed 2026-08-24):** the first-ever run
had no prior report in `reports/code_audits/` to diff against, and
`weekly_code_audit_prompt.md`'s "real read every diff since the last
audit" instruction had no fallback bound for that case (unlike the
diff `--stat` step just above it, which already fell back to `HEAD~10`)
— so it was effectively unbounded against the project's full history
(412 commits at the time). Fixed by bounding that instruction to the
last 10 commits on a from-scratch run, and by raising the timeout from
1800s to 3600s for real margin on top of that. The subprocess call also
used to crash with a raw Python traceback on timeout instead of a clean
failure message — fixed too (`run_audit()` now catches
`subprocess.TimeoutExpired` and logs a clean one-line failure instead).

**If this recurs anyway:** check `reports/code_audits/` for the most
recent report's date — if one exists, the from-scratch-run bound above
doesn't apply and the timeout is being hit on a normal, already-bounded
week's worth of work. That would mean something else is slow or
genuinely stuck: check whether the full `py -m pytest tests/ -q` step
alone is taking unexpectedly long, or whether a specific `PowerShell`/
`git` call in the checklist might be hanging (e.g. a `Get-WinEvent`
query with no filter narrow enough to return quickly).

**To resolve:** the next Sunday run either succeeds cleanly or fails
with a clear one-line timeout message instead of a crash — no manual
state to reset either way.

**Second, separate bug found by this fix's own live verification run
(2026-08-24/25, fixed same day):** the run completed within the new
3600s budget — exit code 0, no timeout, no crash — and *still* wrote no
report to `reports/code_audits/`. Root cause was in the run's own
stderr: `Write(reports/code_audits/*.md)` in `ALLOWED_TOOLS` is not
matched by the permission system's file-write checks at all (only
`Edit(path)` rules are — per the CLI's own error message, "Edit rules
cover all file-editing tools," Write included). The audit's one Write
call to create its report file was silently denied for the entire life
of this script, and exit 0 gave no hint anything was wrong. Fixed by
expressing the same path scope as `Edit(reports/code_audits/*.md)`
instead, and by dropping the blanket `Edit` that used to sit in
`DISALLOWED_TOOLS` (it would have shadowed the new scoped allow — a
disallow wins over an allow on the same tool name). **If a Sunday run
ever again produces exit 0 with no new file in `reports/code_audits/`,
check `logs/weekly_code_audit.log` for a similar
"is not matched by file permission checks" stderr line before assuming
the audit is just quiet that week.**

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
   this exact failure mode more than once (see `docs/PROGRESS_ARCHIVE.md`,
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
| Check scheduled-task drift + Litestream lag without waiting | `python scripts\automation_health_check.py --dry-run` |
| Re-register the automation health scheduler | `powershell -ExecutionPolicy Bypass -File scripts\setup_automation_health_scheduler.ps1` |
| Check today's LLM daily spend (metered API only) | `python -c "from core.llm import get_daily_cost_usd; print(get_daily_cost_usd())"` |
| Check DB location | `python -c "from core import logger; print(logger.DB_PATH)"` |
| Full test suite | `python -m pytest -q` |
| Check PnL integrity without waiting for the weekly schedule | `python scripts/verify_pnl.py --no-alert` |
| Re-register the PnL integrity scheduler | `powershell -ExecutionPolicy Bypass -File scripts\setup_verify_pnl_scheduler.ps1` |
| Check Task Scheduler event log is enabled | `Get-WinEvent -ListLog "Microsoft-Windows-TaskScheduler/Operational" \| Select IsEnabled` |
| Enable it if not (requires Administrator) | `wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true` — from Git Bash, prefix with `MSYS_NO_PATHCONV=1` or its automatic POSIX-path conversion mangles the `/e:true` argument |
| View recent scheduled-task activity | `Get-WinEvent -LogName "Microsoft-Windows-TaskScheduler/Operational" -MaxEvents 50` |
