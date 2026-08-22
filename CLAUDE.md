# CLAUDE.md

Fast orientation for working in Leviathan. Read this fully before re-deriving
anything below by grepping the codebase or git history — it's already been
done, sometimes at real cost (git archaeology, live API doc fetches, cross-
checking claims against the DB). Where a claim below could go stale, it says
so and names the file that's actually authoritative.

## What this is

A solo, self-directed signal-detection pipeline for Kalshi (regulated US
prediction-market exchange): scans open markets, cross-references 4 external
platforms + smart-money wallets, scores flagged markets with Claude, logs to
SQLite, emails a daily report. **Explicitly read-only / paper-trading only —
no order execution.** See `README.md` for the full 8-step architecture.

## Hard policy constraints (do not relearn these by trial and error)

- **Confirm before running `python main.py`.** It makes real Kalshi + Claude
  CLI calls and sends a real email, every time, even though nothing trades.
  ~5–20 min runtime.
- **The bot may only draw on the Claude Pro subscription (`backend="cli"`),
  never metered Anthropic console API spend**, without fresh explicit
  authorization in the current conversation — a prior session's "yes" does
  not carry over. This is enforced in code, not just policy: every metered
  function in `core/llm.py` (`score_via_api`, `probe_via_api`,
  `score_blind_via_api`, `ground_citations_via_api`) is gated by
  `_check_api_spend_authorized()`, which requires the literal boolean `True`
  on `config.llm.api_spend_authorized` (default `false`). Flipping
  `config.blind_arm.enabled` alone is NOT enough to spend real money.
- **Data-gated features are intentional, not bugs.** Several analyses
  (calibration curve, edge-decay, per-wallet skill-vs-luck, slippage
  tracking) are deliberately blocked behind resolved-signal-count
  thresholds (`backlog/backlog.json`'s `trigger` fields — real numbers,
  check live via `python -m backlog.checker`). Don't propose lowering a
  threshold to unblock something; the whole point is the sample isn't
  trustworthy yet.
- **Never fabricate historical narrative or data.** When backfilling
  anything (dates, decisions, content), derive it from git history or
  another real source and say so explicitly. This project's own git log and
  backlog notes are full of precedent for this discipline — match it.

## Removed/known-bad things — don't reintroduce without reading why

- **The Odds API integration is gone** (`sources/odds_api.py`, deleted
  2026-08-21). It hung a live pipeline run for 11+ minutes with zero
  progress on a single sport's fetch, despite a configured 12s
  per-request timeout (8 sports max ≈96s worst case) — most likely a
  slow/stalled connection not respecting the timeout as expected. Removed
  entirely (config keys, `.env.example`, README mentions, cache file)
  rather than just disabled, since sports bookmaker consensus prices
  aren't load-bearing for the project. If a future run hangs on an
  external-source fetch step (`[ext] Fetching ...`), check that source's
  own timeout/retry handling in `sources/` before assuming it's Kalshi or
  Claude — Manifold/PredictIt/Metaculus/Polymarket haven't been
  specifically audited for the same failure mode.

## Where to look

| Need | File |
|---|---|
| Architecture, 8-step pipeline, setup | `README.md` |
| Current backlog state (Ready/Locked/Blocked/Done) | `BACKLOG.md` — **auto-generated** by `backlog/checker.py`, never hand-edit. Source of truth is `backlog/backlog.json`. |
| Recent narrative / session log | `docs/PROGRESS.md` — newest entries at the **top**, covers 2026-08-01 onward (trimmed from ~150KB to ~11KB on 2026-08-22; older entries moved to `docs/PROGRESS_ARCHIVE.md`, same top-down convention, grep by date/keyword rather than reading either in full). |
| Diagnosing a failed/missing scheduled run | `docs/RUNBOOK.md` |
| Evaluating a monday.com/Liam (PM agent) report before acting on it | `docs/RUNBOOK.md`'s "Triaging Liam" section + `python scripts/verify_liam_report.py` |
| Running/diagnosing the monday.com sync itself | `docs/monday_sync_runbook.md` |
| Plain-language project narrative | `docs/STORY.md` |
| Report/email design system | `leviathan-report-format-decision.md`-derived work — shared editorial tokens live in `core/report.py`'s `_editorial_root_css()`; three consumers: `_SUBSCRIBER_TEMPLATE`, `_TRACK_RECORD_TEMPLATE`, `_WEEKLY_SUBSCRIBER_TEMPLATE`. Not the live daily/weekly email yet — see that file's Phase 4. |
| Human-triaged, never-read-by-an-agent parking lot | `docs/IDEAS.md` — do not treat as direction. |
| Whether token-reduction changes (this file, MCP registration) are actually working | `docs/token_usage_baseline.md` + `python scripts/token_usage_report.py --since 2026-08-21` — measured from real Claude Code session transcripts, not assumed. |

## monday.com

`backlog/backlog.json` is authoritative; the monday.com board is a **one-way
mirror** (`scripts/monday_sync.py --phase3 --live`). If they disagree,
`backlog.json` is right — sync to fix the board, never the reverse.

Liam (monday.com's own native PM agent, posts from `agent.monday.com`) is
useful for external research (regulatory/competitor news — verify before
trusting, but it's been right more than once) and **unreliable on internal
project state**: it repeatedly conflated an item's `depends_on` being
satisfied with its actual `trigger` metric being met, and has no visibility
into policy decisions made in conversation. Never act on a "move to Ready"
recommendation without running `python scripts/verify_liam_report.py` first.
A context doc for Liam exists at `docs/liam_context_doc.md` (also live on
the board) but whether Liam's agent settings actually consume it as context
is unverified — that configuration isn't exposed via the API.

## MCP tools available — use these instead of ad-hoc scripts

- **`leviathan`** (this repo's own server, `mcp_server/server.py`,
  registered via `claude mcp add`): `get_signal_log`, `get_resolved_track_record`,
  `lookup_market` — query the live signal DB conversationally instead of
  writing a fresh `python -c "..."` SQL script every time.
- **monday.com's native MCP** is connected in this environment. Prefer it
  for ad-hoc/exploratory monday.com queries. Use `scripts/monday_sync.py`
  only for the actual deterministic sync — that script's logic (diffing,
  dry-run, verify_phase2) is real and tested, don't bypass it for writes.

## Testing & schema conventions

- Full suite: `python -m pytest tests/ -q` (2295+ tests as of 2026-08-20,
  ~4 min). Run before every commit that touches `core/`, `main.py`,
  `backlog/`, or `scripts/`.
- Schema changes are additive-only: new columns via `_add_col`-style
  migrations in `core/logger.py`, never destructive `ALTER`/`DROP` without
  explicit justification (one exception exists, documented inline, for
  confirmed-dead columns).
- `core/llm.py`'s metered functions and `core/scorer.py`'s
  `backend="cli"`/`backend="api"` split are both fully mocked in tests —
  "No live API calls made" is a stated convention in `tests/test_llm.py`,
  match it for new tests in that file.
- Config changes go in **both** `config.json` (live, gitignored) and
  `config.example.json` (tracked, with a `_..._notes` companion key
  explaining any non-obvious value) — check both stay structurally in sync
  (same keys, `config.example.json` may have extra `_notes` keys).
- **`signals.source` isn't just `'paper'`/`'real_fill'`/`'research_probe'`.**
  A `source='superseded_paper'` value also exists (added 2026-08-20) for
  rows where the same still-open market got re-flagged as an
  "independent" signal weeks apart (a since-fixed repeat-dedup gap — see
  `scoring.repeat_dedup_days` in config) and would otherwise double/
  triple-count a single real-world event in `resolved_count` and every
  other stat. Every stats query in `core/logger.py` already excludes it
  via the shared `_PAPER` constant (`source = 'paper' OR source IS NULL`)
  — if you ever write a NEW query against `signals` by hand instead of
  reusing an existing function, use `_PAPER`/`_NO_PASS`, don't write your
  own `source = 'paper'` filter that misses this.

## Windows environment

PowerShell for Task Scheduler / Windows-specific commands, Bash (Git Bash)
works fine for everything else including `python`. Watch for `cp1252`
console-encoding issues when printing em-dashes/emoji — several scripts
already wrap `sys.stdout` in a UTF-8 `TextIOWrapper` for this
(`main.py`, `scripts/position_reconciliation.py`, `scripts/verify_liam_report.py`)
— follow that pattern in new scripts that print non-ASCII text.

`dashboard/.venv/` is a full Python virtualenv on disk (~19,000+ files,
gitignored via `dashboard/.venv*/`). `Grep` respects `.gitignore` and skips
it automatically, but `Glob` does not — a broad repo-root pattern like
`**/*.py` floods with pip/virtualenv internals instead of real code. Scope
`Glob` calls to a specific directory (`core/`, `scripts/`, `tests/`, etc.)
rather than the repo root.

Claude Code's own project identity on this machine is anchored at
`C:\WINDOWS\system32`, not this repo's own root — Bash commands need a
`cd` (or `cd ... &&` prefix) into `C:\Users\Administrator\Downloads\Leviathan`
every time; there's no persistent cwd inside it across turns. This also
means anything registered as "local to the project" (e.g. the `leviathan`
MCP server, added via `claude mcp add`) is scoped to `C:\WINDOWS\system32`
in `.claude.json`, not to the Leviathan repo itself.

Same limitation hits custom subagents (`.claude/agents/*.md`, present in
this repo — see `subscriber-ux-designer.md`, `subscriber-hosting-billing.md`,
`subscriber-growth.md`), confirmed 2026-08-21. Discovery is documented as
walking from cwd up to the repo root, and the files are correctly in place,
but a background-job session here never actually has cwd inside the repo —
`Agent(subagent_type: "subscriber-ux-designer")` fails with "Agent type not
found" even though nothing is wrong with the file. These agents work
normally from an interactive Claude Code session actually launched with cwd
inside this repo (e.g. a terminal opened here running `claude`) — this is a
background-session-only gap, not a bug in the agent definitions.
