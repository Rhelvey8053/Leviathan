# monday.com Sync — Runbook

For running, promoting, and diagnosing `scripts/monday_sync.py`
(`Leviathan-MondaySync` scheduled task). See `docs/monday_sync_discovery.md`
for the design/Phase 0 investigation this was built from.

**Source of truth is always `backlog/backlog.json`.** The board is a
one-way mirror + progress log in v1 — nothing you edit on monday flows
back. If the board and `backlog.json` ever disagree, `backlog.json` is
right; run a sync to fix the board, not the other way around.

---

## Running a dry-run

Never writes anything — prints the full diff and exits.

```powershell
cd C:\Users\Administrator\Downloads\Leviathan
python scripts\monday_sync.py --phase3 --dry-run
```

Read the output: each `would UPDATE`/`would CREATE`/`would post` line
names the item and exactly what would change. An empty diff (just the
`created=0 updated=0 moved=0 completed_stamped=0` summary line) means the
board already matches `backlog.json` — the normal, common case on any day
nothing changed locally.

If you only want to check the sync logic without the progress-log posts,
drop to `--phase2 --dry-run` instead — same diff, no Updates-tab preview
lines.

## Promoting to live

Once a dry-run's diff looks right, use `--live`:

```powershell
python scripts\monday_sync.py --phase3 --live
```

This runs the sync, then automatically re-reads the board and asserts it
now matches `backlog.json` (`verify_phase2`) — if that verification finds
a mismatch, it prints each mismatched item and its diff; the run itself
still exits 0 (nothing crashed), so **check the printed output**, don't
just check the exit code.

**A bare run with neither `--dry-run` nor `--live`** (e.g. just
`python scripts\monday_sync.py`) falls back to `config.json`'s
`monday.dry_run_default` (default `true` if unset) — safe by design, so
forgetting a flag at a clean prompt never accidentally writes. The
scheduled task always passes `--live` explicitly and never relies on
this fallback, so changing `dry_run_default` in `config.json` only
affects manual ad-hoc runs, never the daily automated one.

The scheduled task (`Leviathan-MondaySync`, registered by
`scripts\setup_monday_sync_scheduler.ps1`, daily 9:00am) already runs
this exact live command — you generally don't need to run it manually
unless you just changed `backlog.json` and want the board to reflect it
immediately rather than waiting for the next scheduled run.

## Reading the log

Two places, two different things:

- **`logs/monday_sync.log`** (local, gitignored) — one line per run
  (dry-run or live), with counts and which items were created/moved/
  completed. This is the fast local answer to "did the last run do
  anything." Tail it:
  ```powershell
  Get-Content logs\monday_sync.log -Tail 20
  ```
- **The board's pinned "Leviathan Sync Log" item** (To-Do group) — one
  Updates-tab post per *live* run (dry-runs never post there), including
  `no changes` on a no-op run. This is what to check from inside monday
  itself, without shelling in. Individual backlog items also get their
  own Updates-tab post whenever they're created, change status/group, or
  get marked Done — check that item's own Updates tab for its history.

## Rotating `MONDAY_API_TOKEN`

1. monday.com → avatar → **Developers** → **My Access Tokens** → generate
   a new token.
2. Open `.env` (repo root, gitignored — never commit it, never let it end
   up in a zip) and replace the `MONDAY_API_TOKEN=` line with the new
   value. Nothing else references the token directly — `config.json`
   only stores the *name* of the env var (`api_token_env`), not the token
   itself.
3. Confirm it works before trusting the next scheduled run:
   ```powershell
   python scripts\monday_sync.py --phase3 --dry-run
   ```
   A clean diff (or a real one that looks right) confirms the new token
   has read+write access to the board. An auth error here means the new
   token wasn't generated with the right permissions, or wasn't saved
   correctly — re-check step 2 before assuming anything about the board
   itself is wrong.
4. The old token can be revoked from the same Developers page once the
   dry-run above succeeds.

## Recovering from a failed run

**Task didn't fire at all:**
```powershell
Get-ScheduledTask -TaskName Leviathan-MondaySync
Get-ScheduledTaskInfo -TaskName Leviathan-MondaySync
```
Check `State` is `Ready` (not `Disabled`) and `LastTaskResult` is `0`. If
the task is missing entirely, re-register it:
`powershell -ExecutionPolicy Bypass -File scripts\setup_monday_sync_scheduler.ps1`

**Task fired but the run failed** — Task Scheduler doesn't retain stdout
by default, so re-run manually to see the actual error (matching what the
scheduled task itself runs — `--live`, not the bare/dry-run-defaulting form):
```powershell
python scripts\monday_sync.py --phase3 --live
```
Common causes:
- **`MONDAY_API_TOKEN not set in .env`** — token missing or `.env` not
  loading; confirm the line exists and `python-dotenv` is installed.
- **A GraphQL error mentioning a column or group** — the board's schema
  changed (a column got renamed/deleted on monday's side). Re-run
  `--phase1` first (idempotent — it only creates what's missing and
  re-resolves ids into `config.json`), then retry `--phase3`.
- **429 rate limited** — the script already retries these with backoff
  (5s, 10s, 20s...) automatically; if you see this in output it's not an
  error, just the API asking for a pause. Only worth investigating if it
  exhausts all retries and raises.
- **`monday board is missing expected column/group`** — something on the
  board itself was renamed or deleted (Status label, a group, or a
  Phase-1-created column). This is a fail-loud check by design (the
  handoff's own rule: never silently create a duplicate group/column) --
  fix the board manually to match `docs/monday_sync_discovery.md`
  section 3's expected schema, or decide the naming genuinely changed and
  update `EXPECTED_GROUPS`/`EXPECTED_STATUS_LABELS`/column titles in
  `scripts/monday_sync.py` to match.

**A live run partially wrote, then crashed mid-way** — this is safe to
just re-run. Every write is idempotent (matched by `backlog_id`, only
fields that actually differ get written), so re-running `--phase3` picks
up exactly where the crash left off; nothing gets double-created or
double-posted. If you want to confirm before re-running, `--dry-run`
first to see what's still outstanding.

**The board and `backlog.json` disagree in a way a normal sync won't
fix** (e.g. someone manually edited a monday field) — the next live
`--phase3` run overwrites the board's manually-edited field back to match
`backlog.json`, since `backlog.json` is always authoritative in v1. This
is expected, not a bug — if you want monday to be editable, that's Phase
5 (not built), which is explicitly gated behind its own separate sign-off
per the original handoff.

## Re-resolving board schema (ids changed)

If the board's `id` (URL changes), or any group/column gets recreated
with a new id, re-run Phase 1 to re-resolve and persist fresh ids into
`config.json`:
```powershell
python scripts\monday_sync.py --phase1 --live
python scripts\monday_sync.py --phase1 --live --board-id <new-id>   # only if the board id itself changed
```
Phase 1 is fully idempotent — safe to re-run any time, live, with no
side effects beyond re-confirming/backfilling what's already correct.
