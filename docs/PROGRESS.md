# Leviathan — Progress Log

---

## 2026-08-19 — Codebase audit, PowerBI retirement, hard runtime spend guard

Asked for a project-wide look for bloat and conflicting systems. Found and
fixed two real contradictions with the 2026-08-18 Pro-subscription-only
decision (below): `core/scorer.py`'s and `core/llm.py`'s docstrings still
described `backend="cli"` as a "legacy fallback scheduled for deletion" in
favor of `backend="api"` becoming the default — the opposite of current
policy. Rewrote both.

**Bigger finding: `price-blind-arm`'s `score_blind()` was, and is,
hardwired to always call the metered Anthropic API regardless of
`config["llm"]["backend"]`, and nothing in code stopped `config.blind_arm.
enabled` from being flipped to `true` and silently starting real spend on
the next scheduled run.** Added a hard runtime guard:
`core.llm._check_api_spend_authorized()`, checked at the top of every
metered function (`score_via_api`, `probe_via_api`, `score_blind_via_api`,
`ground_citations_via_api`) before `_check_cost_ceiling` and before any
real request — raises `LLMApiSpendNotAuthorized` unless
`config.llm.api_spend_authorized` is the literal boolean `True`. Defaults
to `false` in both `config.json`/`config.example.json`. This is a single
choke point every caller passes through regardless of backend or which
higher-level path (replay-runner, blind-arm, a future caller) reached it.
10 new tests in `tests/test_llm.py` (`TestApiSpendAuthorization`) — blocked
by default, blocked by missing key, blocked by truthy-non-`True` values
(`"true"`, `1`), passes when `True`, checked before the cost ceiling so an
exhausted ceiling can't mask a missing authorization.

**Moved off Power BI entirely** (user decision): removed
`data/Leviathan Dashboard.pbix` (git-tracked, 4 commits of binary churn
from scheduled data refreshes), added `*.pbix` to `.gitignore`, updated
`README.md`'s file-structure table (added a `dashboard/` row that was
missing entirely, reframed the `data/` row), and retargeted the two
not-yet-built backlog items that still named Power BI as their
destination (`calibration-curve-dashboard`, `wallet-tracking-dashboard`)
to the Streamlit dashboard instead. `data/powerbi_export/`'s directory
name and `core/export_to_csv.py` were deliberately left as-is — the
Streamlit dashboard reads the same CSV export, and renaming the directory
for naming purity alone would touch `dashboard/data.py` and its tests for
no functional gain.

Also this session: shipped `citations-provenance-grounding` (a second,
non-forced API call using `web_fetch_20260318`'s native citations to
ground a shortlisted pick's reasoning in specific cited passages — gated
behind the same dormant `backend=="api"` path as the rest of shortlist
re-scoring, so it's inert under Pro-only operation); found and fixed a
real crash bug (`main.py`'s end-of-run `winsound.PlaySound()` call was
unguarded and raised under Task Scheduler's non-interactive S4U session,
making every healthy scheduled run report as failed — `LastTaskResult
0x8007042B` — despite the actual pipeline work completing correctly) and
a dead Gmail app password (SMTP auth had been silently failing since
2026-08-17, main.py degrades gracefully so it wasn't the cause of the
crash, but no daily report had actually reached the inbox); re-imported
`graphify-skill-evaluation` into `backlog.json` using a new
"sentinel trigger metric" pattern (a trigger condition on a metric
`backlog/checker.py` never computes, so it can only be cleared by a human
editing `backlog.json` again) — this is the same mechanism used to
re-block `replay-instrument-validation` after the user's explicit
no-metered-API-spend decision, and it fixed the exact schema gap that had
forced `graphify-skill-evaluation` out of `backlog.json` entirely on
2026-08-17 (see that day's entry below); recovered and wrote real
per-item `Completed On`/`Start Date`/`Timeline` dates onto the monday.com
board via git archaeology (every item had been showing the same
2026-08-17 board-seed date regardless of actual completion date); and
built `scripts/verify_liam_report.py` plus a documented triage process
(`docs/RUNBOOK.md`) after monday.com's built-in PM agent ("Liam")
recommended the same two wrong "stale block" fixes two days running,
confirmed to be conflating `depends_on`-satisfied with the item's real
trigger metric, and blind to policy decisions made in conversation.

Full suite green throughout (2248 passed, 1 skipped, 13 subtests passed).

## 2026-08-18 — Pro-subscription-only policy decision; net-edge depth check and heuristic split ship

User explicitly decided the bot may only draw on the Claude Pro
subscription (`backend="cli"`), never metered Anthropic console API spend,
reversing an earlier in-session "yes, authorize it" for
`replay-instrument-validation`'s real metered corpus build. Re-blocked
that item in `backlog.json` behind a new sentinel trigger metric
(`api_spend_authorized`, the backlog-tracking precursor to the runtime
guard added 2026-08-19 above) rather than leaving it showing as `ready`;
confirmed via a live `count_tokens` call (no inference cost) that the
Anthropic console API key is also still invalid regardless. Verified no
Windows Task Scheduler job invokes any `backend="api"` code path — the
scheduled pipeline was already Pro-only, this just fixed the backlog
item's own status to stop implying it was actionable.

Also shipped `net-edge-fee-depth-model` (ex-ante order-book depth check —
`check_liquidity()` downgrades confidence when the required side's
book can't fill `unit_size`, wired into both the primary and second-pass
signal loops; new `ob_bid_depth`/`ob_ask_depth`/`liquidity_checked`/
`liquidity_thin` columns) and `heuristic-sunsetting` (split laddered
executive-order-count markets, rate 0.20, out of the generic "executive
order" heuristic, rate 0.45, which was overpricing them). Found and fixed
an unrelated monday.com data bug along the way: the board's `long_text`
column silently strips content matching an HTML-tag pattern on write —
the literal string `<date>` (used as a placeholder in backlog action
text) was being dropped; fixed by rewording, not by changing
`monday_sync.py`.

## 2026-08-17 — monday.com sync built end-to-end (Phases 0–4); roadmap reconciliation

*(Backfilled from git history and `backlog.json`'s own notes — Claude
was not present for this session in real time, so this entry is
compressed from commit messages and decision records rather than a
first-hand investigative narrative.)*

Built the full one-way `backlog.json` → monday.com sync in four phases,
each live-verified before moving to the next: Phase 0 discovered the
board had drifted from `backlog.json` (20 items existed only in an
earlier hand-written `BACKLOG.md`, never in the JSON — backfilled from
each item's original git commit, not the board's own already-truncated
~450-char Detail previews) and backfilled all 20, including
`graphify-skill-evaluation`; Phase 1 prepped the board schema
(`backlog_id`, `Completed date` columns); Phase 2 was the live push sync
itself, and its own dry-run caught `graphify-skill-evaluation` triggering
a real bug — `status="blocked"` with an empty `depends_on`/`trigger`
vacuously satisfied `evaluate_triggers()` and auto-promoted it to
`ready` against the real `backlog.json` before the dry-run caught it —
Reed's call at the time was to drop the item back out entirely rather
than misrepresent it or invent a fake dependency (this is the same gap
the 2026-08-19 sentinel-trigger pattern later closed, allowing the item
to be safely re-added); Phase 3 added the Updates-tab progress-log
posts; Phase 4 added scheduler registration and a runbook, fixing a
`dry_run_default` config value that was never actually being read.

Same day: a roadmap-reconciliation pass added 3 new backlog items
(`citations-provenance-grounding`, `net-edge-fee-depth-model`,
`cross-venue-expansion`) and a `ROADMAP.md`, dropping a fourth proposed
item (`scorer-websearch-grounding`) as already-implemented after
verifying web search was already live on both scoring backends.

## 2026-08-15 to 2026-08-16 — Reporting scorecards, Streamlit dashboard, a real metric bug

*(Backfilled from git history and `backlog.json`'s notes.)*

Found and fixed `resolved-count-metric-desync`: `backlog/checker.py`'s
`compute_metrics()` computed `resolved_count` with a SQL query that
diverged from `core/logger.py`'s `get_stats()['resolved']` (the number
`scripts/gate_notifier.py`'s real gate-unlock emails actually use) in two
ways — no `direction != 'PASS'` filter, and no `source='paper'` filter.
Live data went 47 (buggy) → 16 (direction fix alone) → 13 (both fixes,
now matching `get_stats()` exactly). **This means the 2026-08-03 "3 items
unlock at resolved_count>=25" event below was measured against the
buggy, inflated count** — worth knowing if anything was reasoned about
using that number in the interim.

Also shipped: a per-heuristic win/PnL scorecard and a whale-flagged
vs. not win-rate scorecard on both daily and weekly reports; a fix for
`analysis/calibration.py` crashing on Windows consoles (cp1252 encoding);
a `signals.csv`/`scan_log.csv` split with a `pre_scoring_era` flag; and
`dashboard/` — a free local Streamlit dashboard (Overview / Signal
Breakdown / Signal Log pages) built as a Power BI alternative, reading
the same `data/powerbi_export/` CSV export, with its own data contract
(`dashboard/data.py`) written from a live inspection of the real export
rather than assumed from the export code. (Power BI itself was retired
entirely on 2026-08-19, above — Streamlit is now the only dashboard.)

## 2026-08-01 to 2026-08-04 — CI pipeline, Kalshi SDK migration, first gate unlocks

*(Backfilled from git history and `backlog.json`'s notes.)*

Added GitHub Actions CI (`pytest`, pinned Python 3.13) along with the
fixes that took to get it green (dummy Kalshi auth env vars for a clean
checkout, pinning an unpinned `mcp[cli]` dependency, guarding
`main.py`'s `winsound` import behind `sys.platform == "win32"` since CI
runs on `ubuntu-latest`). Also: a full DB audit that found and fixed
several signal-logging data gaps; Litestream continuous backup; an
Expected Calibration Error metric added to `analysis/heuristic_backtest.
py`; a down-ballot-election heuristic recalibration (split many-way
fields from genuine 2-way races); and an evaluation of the `graphify`
Claude Code skill (logged as "not worth adopting yet" — later
re-evaluated 2026-08-19, see above).

2026-08-03: `resolved_count` crossed 25 (per the since-found-buggy metric
above), unlocking `confluence-detection` and `brier-tracking`; `multi-
sample-scoring` (N independent scoring passes, majority-vote + mean
aggregation) was added the same day.

2026-08-04: migrated to the Kalshi SDK and fixed whale flags not
reliably reaching Claude scoring.

**No commits 2026-08-05 through 2026-08-14** — consistent with the
project's own data-gated design (several features are deliberately
blocked on resolved-signal-count thresholds that hadn't moved).


---

Entries before 2026-08-01 live in `docs/PROGRESS_ARCHIVE.md` (moved
2026-08-22 to keep this file's size manageable — grep the archive by
date/keyword the same way you would this file).
