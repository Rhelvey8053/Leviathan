You are running as an unattended, scheduled weekly system-and-code health
check on the Leviathan prediction-market bot codebase at the current
working directory, on Windows. Nobody is watching this run live — you
cannot ask questions and must not wait for approval. This is a
report-only run: investigate thoroughly, but do not change anything.

## Hard constraints (never violate these)

- Do not edit, create, or delete any file except the ONE report file you
  write at the end (see "Output" below). No code fixes, no config
  changes, no "while I'm here" cleanups — findings only.
- Never run `git commit`, `git push`, `git add`, or any other command
  that changes repo state. Read-only git commands only (status, log,
  diff, show).
- Never modify data/leviathan.db or any file under data/. Read-only DB
  queries only, via throwaway read-only `py -c` one-liners — never raw
  sqlite3 UPDATE/INSERT/DELETE/ALTER.
- Never modify a Windows Scheduled Task, never start/stop/restart a
  process, never touch Task Scheduler state in any way. If you find a
  task misconfigured (wrong logon type, missing execution time limit,
  a stuck process), report it — do not fix it. A past unattended run of
  a task like this one modified live Task Scheduler state and could have
  made things worse if the diagnosis had been wrong; this run must not
  repeat that risk.
- Never run main.py, scripts/position_reconciliation.py's underlying
  fetch, or anything that calls the live Kalshi API to place or imply a
  trade. Never run core.logger.pull_real_fills for any reason.
- PowerShell system checks (Get-ScheduledTask, Get-ScheduledTaskInfo,
  Get-Process, Get-WinEvent, Get-PSDrive, Get-CimInstance) are read-only
  and fine to run freely.

## What to check this run

### 1. Test suite
Run `py -m pytest tests/ -q`. Report pass/fail/skip counts. If anything
fails, include the failure output in full.

### 2. Scheduled task health
For every task named `Leviathan-*`:
- `Get-ScheduledTask | Where-Object {$_.TaskName -like "Leviathan*"}` —
  confirm State is not stuck in a non-Ready/non-Running state.
- Check `.Principal.LogonType` for each — flag any that are NOT `S4U`
  (this exact regression happened before: a task quietly reverted to
  `Interactive`, meaning it silently stops running whenever nobody is
  logged in, until someone happens to notice). This is the single most
  important check in this section — check it every time, not just when
  something else looks wrong.
- Check `.Settings.ExecutionTimeLimit` for each — flag anything unset,
  `PT0S`, or implausibly long (e.g. `PT72H`) for a task that should be a
  short one-shot job (a truly long-running daemon task like Litestream's
  continuous `replicate` process is the one legitimate exception — verify
  its action is actually a `replicate`/daemon-style command before
  treating `PT0S` there as fine).
- `Get-ScheduledTaskInfo` for each — note LastRunTime/LastTaskResult, but
  don't trust a stale-looking LastTaskResult at face value; if something
  looks wrong, cross-check against
  `Get-WinEvent -LogName "Microsoft-Windows-TaskScheduler/Operational"`
  filtered to that task name for the real recent history before
  reporting it as a problem — a summary field can be stale/misleading in
  ways the actual event log isn't.
- Look for any currently-running process that looks stuck: high wall-clock
  runtime relative to near-zero CPU time is the strongest signal (a true
  hang, not just slow). `Get-CimInstance Win32_Process` with a `CommandLine`
  filter for `Leviathan` is more reliable than `Get-Process` by name alone
  for identifying what a process actually is.

### 3. Data integrity
- `data/leviathan.db`: `PRAGMA integrity_check` via a read-only `py -c`
  one-liner. Should return exactly `ok`.
- Check `data/powerbi_export/signals.csv` and `scan_log.csv` exist and
  are non-empty, and that signals.csv's `direction` column contains only
  YES/NO (no PASS rows — if PASS rows are back in signals.csv, the
  signals/scan_log split has regressed).
- Disk space: `Get-PSDrive C` — flag if free space is below ~10GB.
- Log file sanity: sizes and last-write times of files under `logs/` —
  flag anything that looks frozen for an implausibly long time relative
  to its task's schedule, or that has grown unreasonably large
  (unbounded growth with no rotation).

### 4. Recent code changes
- `git log --oneline -20` and `git diff <last audit's base commit if
  known, else HEAD~10>...HEAD --stat` — get a bounded view of what
  changed since the last audit.
- For commits since the last audit report in `reports/code_audits/`, do
  a real read of the actual diffs (not just commit messages) for
  anything touching `core/`, `main.py`, or scheduled-task setup scripts
  under `scripts/setup_*.ps1` — these are the highest-consequence files.
  Look specifically for: partial wiring (a change applied at one call
  site but not a sibling one — this exact bug class has happened before
  in this codebase), silently-swallowed exceptions that could hide a
  real failure, and any scheduler setup script that registers a task
  without an explicit S4U `-Principal` (this exact gap already caused a
  real regression once — every `scripts/setup_*.ps1` should specify it
  explicitly so re-running the script is safe).
- Confirm `BACKLOG.md`'s Done section header count matches its actual
  row count, and that `backlog/backlog.json`'s item count matches what
  `tests/test_backlog.py`'s count assertion expects. If a commit since
  the last audit looks like real completed work with no corresponding
  backlog entry in either file, flag it — do not add the entry yourself.

### 5. Backlog gates
Read BACKLOG.md's Locked section. For each gated item, check whether its
stated threshold is now met by querying data/leviathan.db read-only.
Flag any item whose gate now appears satisfied.

## Output

Write ONE file: `reports/code_audits/<YYYY-MM-DD>.md` (today's date).
Use this structure:

```
# Weekly Code Audit — <date>

## Test suite
<pass/fail/skip summary>

## Scheduled task health
<per-task logon type / execution time limit / last-run status; any
flagged tasks with specific evidence (event log excerpt, process
runtime vs CPU, etc.), or "all 8 tasks healthy">

## Data integrity
<DB integrity_check result, CSV split sanity, disk space, log file
sanity, or "nothing notable">

## Recent code changes since <last audit date or "project start">
<diff-level findings on core/, main.py, scheduler setup scripts;
partial-wiring or swallowed-exception concerns; backlog doc-sync
status, or "nothing notable">

## Backlog gates
<any newly-unlocked Locked items, or "no gates newly met">

## Findings
<bulleted list of everything worth a human's attention, ranked most
important first, each with enough detail to act on without re-deriving
it — or "nothing to report this week">
```

If genuinely nothing is notable in a section, say so plainly — a thin,
honest "nothing changed" is correct on a quiet week, not a reason to
skip the section or manufacture a finding.

Keep the whole report under 250 lines. This is a status file for a human
to skim and act on, not a full investigation writeup — flag things,
don't resolve them.
