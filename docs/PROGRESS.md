# Leviathan — Progress Log

---

## 2026-09-01/02 — HANDOFF: read this first if picking up a new session

**Start here for continuity.** This entry exists specifically so a fresh
Claude Code session can pick up mid-stream without re-deriving context.
The narrative below it (and the 08-20 gap before this section) covers
what happened; this top block is "what to actually do next."

### Claude's role: PM for Leviathan (new, as of this session)

The user retired the Liam/monday.com PM integration (trial expired
2026-08-30) and, after a direct back-and-forth about scope, designated
Claude as its replacement — not a separate spawned agent, this ongoing
session role. **Hard boundaries, agreed explicitly, apply regardless of
how the request is phrased:**
- Paper-only until profitability is actually proven — no real trade
  execution, ever (also a hard constraint on how Claude operates, not
  just project policy).
- No metered Anthropic API spend without fresh, explicit per-instance
  authorization (unchanged from [[feedback-leviathan-no-api-billing]]
  memory / this file's 2026-08-19 entry below).
- Don't loosen the statistical/sample-size validation gates
  (`resolved_count` thresholds, `edge_threshold`, etc.) just to produce
  results faster — advancing the project means feeding the gates real
  data faster, not moving them. Any such change needs explicit user
  sign-off with the tradeoff stated plainly, not silent implementation.
- Mandate otherwise: proactively advance ready backlog items, run/
  monitor the pipeline, investigate anomalies, run authorized
  experiments to completion, surface recommendations — the autonomy
  the user explicitly wants, just not over money or the gates.

This is also saved to memory (`feedback_leviathan_pm_role_boundaries`)
so it persists even if this file isn't read first.

### What's actively running right now (check these first)

1. **Replay-corpus build toward n=300** — `backtesting.replay_runner
   --max-markets 180`, launched via OS-level `&`/`disown` detachment
   (not the Bash tool's own `run_in_background`, which does not survive
   session/task boundaries — confirmed the hard way, twice, earlier
   this session). PID 263 as of this entry. Last known count:
   **219/300** in `data/leviathan.db`'s `replay_signals` table. Pace has
   been uneven (roughly 9-15 new rows per ~50min check cycle, with
   occasional longer stalls) — check via `python -c "import sqlite3;
   print(sqlite3.connect('data/leviathan.db').execute('SELECT COUNT(*)
   FROM replay_signals').fetchone()[0])"` or the new
   `mcp_server` tool surface (see below). **When it reaches 300**: call
   `backtesting.replay_runner.export_and_report()` directly to
   regenerate `data/replay_export/*.csv` and
   `reports/replay_backtest_report.txt`, then report the Brier/hit-rate
   summary — but do NOT present the raw hit rate as a profitability
   signal. A preliminary check at n=210 found 88.9% raw hit rate on
   directional calls vs. 42.1% on the live paper track record — almost
   certainly look-ahead contamination (Claude may already know how
   these historical, already-settled markets resolved), exactly the
   structural limitation `replay_runner.py`'s own docstring warns about.
   This corpus's real purpose is `replay-instrument-validation` (testing
   the grading machinery itself), not a profitability readout, once
   n>=300 is reached.
2. **Bounded Opus-vs-Sonnet trial** (`trial-stronger-model-main-scoring`,
   user-authorized) — `config.json`'s `llm.cli_model_override` is set to
   `"opus"` (confirmed live-accessible under the Pro plan at no extra
   cost; Fable 5/5.1 was checked and rejected for this — it requires
   separate metered credits not covered by the flat-rate plan). Baseline
   captured from 7 pre-trial Sonnet runs: mean `signals_generated`=0.29,
   `whale_flags`=13.6, `runtime_ms`=650506 (~10.8min), `markets_scanned`
   =2969. **Trial run 1** landed 2026-09-01T22:09:26Z (run_id
   `865ba8a5`): signals_generated=1, whale_flags=7, runtime_ms=339040
   (~5.65min, notably faster than baseline — n=1, could easily be normal
   variance, not a real speed signal). Target is 5-7 total runs,
   accumulated via the **normal daily schedule only** — do not manually
   trigger extra `main.py` runs, since Opus calls share the same Pro/CLI
   usage budget as the concurrent replay-corpus build. After 5-7 runs:
   compare against baseline, then set `cli_model_override` back to
   `null` regardless of outcome (bounded measurement, not a default
   change) — see the item's notes in `backlog/backlog.json` for the
   exact plan.
3. **RESEARCH DILIGENCE prompt change** (commit `0fb2148`) — added to
   `core/scorer.py`'s `SYSTEM_PROMPT`, requiring genuine multi-search
   effort before a market gets scored PASS, without lowering the
   evidence bar the existing 47 calibration rules set. **The next
   scheduled `main.py` run is simultaneously Opus-trial run 2 AND the
   first live run under this new prompt** — when it lands, check both
   effects, but don't over-read n=1 on either axis.
4. **`model_used` field is now trustworthy again** (commit `a495ea0`) —
   it used to read a dead cosmetic config key; it now reflects
   `cli_model_override` when the `cli` backend has one set. Trial run
   1's own DB row still shows the stale label (predates the fix) but was
   confirmed via code-path analysis to have actually used Opus.

Query all of the above without one-off SQL by using the MCP server's
new v2 tools (`mcp_server/server.py`, commit `6492ce3`):
`get_run_history`, `get_category_breakdown`, `get_backlog_status`,
`get_pipeline_health` — each wraps logic that already existed
(`backlog.checker.compute_metrics`, `automation_health_check.
check_scheduled_tasks`, etc.), never a separately-computed number.

### What changed this session (2026-08-20 through 2026-09-02) — no PROGRESS.md entries existed for this whole window before now

**Smart Money dashboard redesign** — user feedback that the page didn't
make clear what the most recent trades were and gave no way to act on
actually-winning wallets. Added a Winning Whales leaderboard + live-picks
feed (surfacing wallet data `sources/accounts.py` already computed but
never rendered), made the whale-activity table's tickers readable via
real market titles + clickable Kalshi links. Verified via Streamlit's
`AppTest` harness against both real and synthetic data.

**Dashboard-wide caption accuracy passthrough** (commit `4bcca10`,
prompted directly by the user catching a real inaccuracy: "so the streak
on the table is associated with a wallet correct?" — it wasn't;
`core/whales.py` has zero wallet concept, Kalshi's order book exposes
size/direction only, never identity). Applied the same
verify-against-actual-code rigor to every remaining dashboard page:
fixed an edge-sign implication on Overview, a stale hardcoded resolved-
count on Signal Breakdown, a matching wallet-identity-conflation caption
on Signal Log. **Found a real production bug in the process, not just a
wording issue**: Signal Breakdown's click-to-filter chart crashed on
every load with real data because `st.plotly_chart(on_select=...)`
requires Streamlit >=1.35, but the actually-deployed environment
(anaconda's Python, not the project's own) pins 1.30.0, where
`on_select` silently falls into `**kwargs` and the call returns a plain
`DeltaGenerator` instead of a selection-state dict. Fixed with an
`isinstance` guard.

**Task Scheduler outage, caught and mis-attributed once before getting
it right** (commit `5b76aec`) — `ResolveFirst`, `GateNotifier`,
`PositionReconciliation`, and `AutomationHealthCheck` all silently failed
to launch on 2026-09-01 despite `DailyRun`/`SmartMoneyScan` firing
normally that morning; even `WakeCatchup` (the dedicated safety net for
exactly this) never fired via either of its triggers. Manually ran the
missed scripts directly, bypassing Task Scheduler. **First attributed
this to the machine sleeping through the window — wrong, and corrected
in the same session**: a second background process (the replay-corpus
build) ran continuously through the exact same window and produced 53
real scored rows, proving the machine was awake throughout. Logged as
new evidence on the existing open item
`task-scheduler-manual-trigger-stuck-queued` — confirms the known
flakiness also affects natural/wake-event triggers, not just manual
`Start-ScheduledTask` calls as previously documented.

**Resolve-first acceleration + model-override wiring** (landed just
before this visible window, referenced by later work) —
`resolve_first_picks_per_bucket` raised 1→3, near-dated fetch ceiling
raised 200→300 markets, and a real bug fixed: `core/scorer.py`'s live
CLI-backend scoring call never passed `--model`, so `config.llm.model`
was completely dead for the live pipeline (only fed the disabled
metered-API backend). Added the new, separate `config.llm.
cli_model_override` key (defaults `null`, live-verified that a bare
`claude --print` already defaults to `claude-sonnet-5`, so leaving it
unset changes nothing) — this is the mechanism the Opus trial above now
uses.

**`resolve_first.py` was silently dropping category on every signal it
logged** (commit `6c06b02`, found from user feedback that category
diversity "felt low") — 100% of RESOLVE_FIRST-flagged signals (39/39)
had a blank category despite the source snapshot having it on all 3037
markets; `log_selected()` built its signal dict field-by-field and
simply never included `category`. One-line fix. Separately confirmed
(not a bug): Kalshi's real taxonomy has 15 categories, already sourced
directly with no synthetic mapping anywhere in this codebase; the 4
categories never seen in `signals.category` (Social, Health, World,
Transportation) are just thin market slices (<=19 open markets each)
that haven't produced a flagged signal yet.

**Confidence-scaled bet sizing already exists** — investigated in
response to the user asking for it; turned out `core/sizing.py` was
already built 2026-07-27, correctly gated behind
`resolved_count>=30 AND resolved_count_per_category_max>=15` (currently
~22/3, not yet eligible) plus a manual opt-in flag (off). No new
engineering needed on the mechanism itself, just more resolved samples —
which is exactly what the resolve-first acceleration above, and the PM
role generally, are already working toward.

**MCP server v2** (commit `6492ce3`) — see "what's running now" above.
Prompted by the user, as PM, asking what new plugins/connectors could
help. Third-party Kalshi/Polymarket MCP connectors were researched and
explicitly rejected (trade-execution-capable, conflicts with paper-only
discipline, would hand a real API key to an unverified third party).

**Research diligence prompt change** — see "what's running now" above.
Prompted by the user asking to stop passing on bets; that request was
split before building anything into "research harder before PASS"
(built) vs. "never pass, always call a side" (declined — `core/
sizing.py`'s own docstring already documents the same failure mode:
edge-magnitude sizing made hypothetical P&L ~4x worse on real data at
low sample size; forcing a call with no real edge would inject the same
noise into the exact track record this project uses to measure whether
skill exists at all).

**Unrelated side task, same session**: a 10-repo investigation for the
user (`C:\Users\Administrator\Downloads\repo-investigation-handoff.md`),
reports written to `..\repo-investigation\reports\`. Top picks:
[herdr](https://github.com/herdrdev/herdr) (persistent cross-platform
runtime for coding-agent terminals — solves the exact background-
process-survives-disconnect problem hit twice this session with the
replay-corpus build and an earlier dashboard-server restart), claudex-
loop (Claude Code skill, adversarial cross-model plan review before code
exists), and deepseek-harness (novel "everything is a plugin" agent-
harness architecture, worth a read even if never adopted). **herdr was
then actually installed** at `C:\Users\Administrator\AppData\Local\
Programs\herdr\` and added to the user's PATH, after a clean security
sweep (byte-exact match to the official GitHub release, Windows Defender
full scan found nothing, Microsoft-signed ConPTY components verified).
Not yet used for anything in this project — worth trying against
Leviathan's own background-job-persistence pain point directly.

Backlog stands at **111 items** as of this entry (6 ready / 6 locked /
6 blocked / 93 done). Current HEAD commit: `0fb2148`.

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
