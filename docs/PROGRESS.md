# Leviathan — Progress Log

---

## 2026-07-27 — Diagnosed a concurrent-run coincidence; enabled Task Scheduler event log

A manual `python main.py` run and `Leviathan-DailyRun`'s own scheduled
fire landed less than 2 minutes apart, producing two `runs` rows scanning
the same 2,476 markets. Root-caused via `Get-ScheduledTaskInfo`'s
`LastRunTime` (9:49:49 AM local) matching one run's timestamp almost
exactly (converting its UTC `runs.timestamp` via the machine's actual
current offset, CDT/UTC-5): `Leviathan-DailyRun` has `StartWhenAvailable:
True` and `DisallowStartIfOnBatteries: True` set, and the machine has a
battery — consistent with the 6:00 AM slot being skipped (on battery) and
caught up later, coincidentally close to a separate manual invocation.
`MultipleInstances: IgnoreNew` doesn't prevent this: it only stops Task
Scheduler from double-launching *its own* task, not a manually-invoked
process from overlapping with a Task-Scheduler-launched one.

Couldn't get a fully authoritative confirmation at the time because the
Task Scheduler Operational event log (`Microsoft-Windows-TaskScheduler/
Operational`) was disabled on this machine — the timestamp match was
circumstantial, not a logged trigger record. Enabled it (`wevtutil sl
Microsoft-Windows-TaskScheduler/Operational /e:true`, run elevated via
Git Bash with `MSYS_NO_PATHCONV=1` — its default POSIX-path conversion
mangles `/e:true` otherwise) so future occurrences can be root-caused
directly via `Get-WinEvent` instead of by timestamp inference. Documented
both the incident and the new diagnostic path in `docs/RUNBOOK.md`
("Two `runs` rows recorded close together").

---

## 2026-07-27 — Fix: `_score_via_cli` didn't retry on a hung CLI process

A manually-triggered `python main.py` run (2026-07-27) completed end-to-end
(exit 0, report email sent) but produced 0 signals: the Claude CLI scoring
subprocess (10-market batch, web search) hit its 600s timeout and the run
moved on gracefully, per `main.py`'s own try/except around step 6.

**Root cause:** `subprocess.run(..., timeout=600)` raises `TimeoutExpired`
directly — it isn't a nonzero return code, so the existing retry loop
(`if result.returncode == 0: break`, else sleep and retry, `max_retries=2`)
never saw it. A hang failed the whole batch on the very first attempt with
2 unused retries sitting right there, unlike a bad exit code (which did
retry correctly already).

**Fix:** `core/scorer.py`'s `_score_via_cli()` now catches `TimeoutExpired`
per attempt and retries it through the same `max_retries`/5s-backoff loop
already used for nonzero exit codes, tracking a `timed_out` flag so the
final error message is accurate (and so `result` — which stays `None` if
every attempt times out — is never dereferenced). Worst case is now up to
3 attempts × 600s (~30 min) for this one step alone if the CLI is
genuinely stuck every time, versus failing outright on the first hang
before; that tradeoff (a slower worst case in exchange for surviving a
transient hang) wasn't tuned further since retrying a temporary problem is
the actual point here.

**Tests:** 3 new tests in `tests/test_scorer.py` — timeout-then-success
retry, all-attempts-timeout raises a clear `RuntimeError` (not an
`AttributeError` from a still-`None` result), and a mixed
timeout-then-nonzero-exit-then-success case confirming the two failure
modes share one retry budget without interfering. Full suite green (1849
passed, 1 skipped).

---

## 2026-07-26 — Partial verification: replay-instrument-validation (still `ready`)

Continuing through the backlog: the only `ready` item left,
`replay-instrument-validation`, needs the replay corpus at `n>=300`
(currently 0 rows in `replay_signals`) to validate Brier computation and
the `resolved_count>=10` threshold — both genuinely blocked on the broken
Anthropic API key (`401 - authentication_error`, re-confirmed live this
session). But the item's other clause — "grading handles early closes,
voided markets and multi-outcome events" — is checkable right now against
real data already sitting in `leviathan.db`, without any live API call.
Did that instead of waiting idle on the blocker.

**Voided markets:** already correctly filtered. `settled_fetcher.
_row_from_market()` returns `None` for any result not in `(YES, NO)`,
confirmed by an existing test (`test_skips_unresolved_markets_within_a_
settled_event`) — voided markets never reach `settled_markets`.

**Multi-outcome events:** queried the real table (12,600 rows) and found
both patterns genuinely present — mutually-exclusive events (e.g.
`KXLIUSACOUPLE-26AUG31`, 182 sibling tickers, exactly 1 YES) and
legitimately non-exclusive ones (e.g. `KXPGATOP10-THOC26`, 156 tickers, 13
YES — "will player X finish top 10" isn't mutually exclusive). `replay_
runner._row_from_scored()` grades strictly per-ticker with no event-level
aggregation anywhere in the code, so both are handled correctly by
construction, not by luck — already covered by existing tests, none of
which reference sibling tickers at all (there's nothing for them to
reference).

**Early closes:** this one started as a real hypothesis, not a checkbox.
If Kalshi's `close_time` field for an early-closed market retained the
*originally scheduled* deadline rather than the actual closure moment,
the as-of reconstruction's lookback windows (`close_time - 30/14/7/3/1
days`) could land AFTER the market had already resolved — leaking
look-ahead price data into what's supposed to be a pre-resolution
snapshot, which would be a worse contamination than the already-documented
training-data kind. Checked it against all 12,600 real rows:
`settlement_ts` never precedes `close_time` (min gap +22s, max +3.4 days,
zero negative gaps) — `close_time` already reflects the real closure in
every observed case. The risk doesn't materialize in this data.

No code changes: no bug found, and (1)/(2) already have regression tests
in place — adding more would have duplicated existing coverage. Backlog
item stays `ready` (only the grading-edge-cases clause is resolved; the
Brier/threshold clauses still need the live corpus). Addendum recorded in
`backlog/backlog.json`.

---

## 2026-07-26 — Shipped: price-blind-arm (backlog item, done)

Implemented the shadow-scoring counterfactual: does the anchored scorer
(core/scorer.py) actually add information beyond the market price it's
shown, or would a blind estimate do just as well?

**Not just deleting the price line.** core/scorer.py's live prompt leaks
price-derived information in several places beyond the literal "Current
market price" line: FLAG REASON compares a heuristic base rate to the
market price, SIGNAL QUALITY is the Leviathan Score grade (itself derived
from net_edge), and the HEURISTIC/POLYMARKET/CONSENSUS conflict warnings
are all keyed off `heuristic_direction`, which is computed by comparing
`base_rate` to Kalshi's own `mid_price`. A blind mode that only hid the
price line but kept these would still be indirectly anchored. `core/
blind_scorer.py`'s `build_prompt_blind()` shows only ticker, title,
horizon, close date, and WHALE ALERT (informed-trader positioning is
independent evidence, not a price comparison) — everything else is
omitted.

**System prompt: an override layer, not a hand-copied duplicate.**
`SYSTEM_PROMPT_BLIND` is `scorer.SYSTEM_PROMPT` plus an appended override
block, not a rewritten copy of all ~47 calibration rules. Most rules are
pure category base-rate/evidence-quality guidance that never mentions
price and carry over unchanged — duplicating them would just create a
second copy that silently drifts the next time someone edits a rule in
the live prompt. Only the rules that are *structurally* about comparing
to market price get a real, concrete, price-free replacement instruction
in the override block (not just a "disregard the price part," which would
leave nothing coherent behind for a rule like #1, whose entire content
*is* "if price is below 15%..."):
- Rule 1 (tail probability) → apply the same skepticism to your own
  extreme estimates instead of the market's price.
- Rule 11 (edge requirement) / Rule 30 (anchoring guard) → don't apply;
  there's no price to compute an edge against. Report a raw estimate and
  confidence only, no direction/edge field.
- Rule 13 (price/level markets) → judge from the question wording alone
  (default near 50% absent a specific unpriced catalyst).
- Rule 28 (short-horizon decay) → weight only recent, dated evidence; no
  edge threshold since there's no price to compare against.
- Rule 29 (LV grade edge scaling) → doesn't apply; no SIGNAL QUALITY line
  exists in this mode.
- Rule 32 (sports) / Rule 35 (Fed rate decisions) → drop the "gap vs.
  another platform/CME FedWatch vs. Kalshi" mechanic; for Rule 35,
  report the FedWatch-implied probability directly instead.

**Always metered, never CLI.** `core/llm.py` gained `RECORD_BLIND_SCORES_
TOOL` (ticker/estimate/confidence/reasoning/sources_checked — no
market_price/edge/direction, since there's nothing to diff against) and
`score_blind_via_api()`, called unconditionally regardless of
`config["llm"]["backend"]`. This shares `_check_cost_ceiling`/
`_finalize_token_info` with `score_via_api`/`probe_via_api`, so blind-arm
spend counts against the same `daily_cost_ceiling_usd` total, not a
separate budget — verified with a test that runs one anchored and one
blind call back-to-back and checks the accumulated total is their sum.

**Structurally isolated from signal selection.** Results are logged to a
new, separate `blind_scores` SQLite table (`core.logger.log_blind_score`)
— never `signals` — so the shadow arm is architecturally unable to feed
signal selection, not just unused by convention. `main.py` wires it in as
a non-fatal step after `final_signals` is already decided:
`_sample_for_blind_arm()` picks up to `config.blind_arm.sample_size`
(default 3) markets — deterministic first-N in existing priority order,
not random, so a run is reproducible if re-executed — from those the
anchored scorer actually scored. Any failure is caught and printed, never
raised; the main run's own signals are never at risk from this.

**Off by default.** `config.blind_arm.enabled` defaults to `false` in both
`config.json` and `config.example.json`. Unlike the main scan (Pro
subscription, no per-token bill), every run this fires spends real
metered API cost — it needs a deliberate opt-in, not silent activation as
a side effect of shipping this item.

**Verified:** `build_prompt_blind()` smoke-tested against real live Kalshi
market dicts (KXCANADACUP-30, KXNFLENDSTREAK-40NYJ-3031/2930) — renders
cleanly, no KeyErrors on real production field shapes. `score_blind_via_
api()` itself cannot be validated end-to-end: the real Anthropic API key
still returns `401 - authentication_error` on every live call, the same
blocker as `replay-instrument-validation`. 18 new tests across `tests/
test_blind_scorer.py`, `tests/test_llm.py`, `tests/test_blind_arm_main.py`,
`tests/test_logger_blind_scores.py`, all passing. Full suite green (1844
passed, 1 skipped). Marked `done` in the backlog per this session's
`replay-runner` precedent: implemented and tested is `done`; a broken
external API key blocks live execution, not shippability.

The one remaining `ready` backlog item, `replay-instrument-validation`,
needs the replay corpus at `n>=300` (currently 0 rows) and real
`backend="api"` scoring calls — both blocked on the same key.

**Post-review fixes:** a second pass caught two gaps before either could
affect real data. (1) `_sample_for_blind_arm()` originally took the first
N markets from `flagged_markets`, which is pre-sorted by pre-signal
strength before scoring — always sampling the head would have meant the
blind arm only ever saw the highest-conviction markets, exactly the slice
where the anchored scorer's use of price is most likely already
justified, defeating the point of a representativeness check. Changed to
`random.Random(run_id).sample(...)` — still reproducible if a run is
re-executed (same `run_id` → same sample), but not systematically biased
toward one slice of the distribution. (2) The override block dropped Rule
30 (anchoring guard) without giving Claude a landing spot for weak/
ambiguous evidence in blind mode — every other rule either kept its own
base rate or got a specific replacement, but the general "evidence is thin
and none of the specific rules pin down a number" case had nothing to
anchor to. Added: default to the category base rate stated in the
applicable rule above, only move away from it on something concrete and
specific. Both fixed in `core/blind_scorer.py`/`main.py`; tests updated
accordingly. Full suite still green.

---

## 2026-07-26 — Shipped: unattended-ops (backlog item, done)

User authorized working through remaining `ready` backlog items. This was
the one with no unmet dependencies. Three pieces, per the item's action
text:

**1. Alert on absence.** `scripts/heartbeat_check.py` reads the most
recent `core.logger` `runs` row and emails an alert if none exists within
`max_silence_hours` (default 30.0) — or if the table is empty outright.
Uses the same fire-once JSON state-file pattern as `scripts/gate_notifier.py`:
state is keyed on the last-seen `run_id` and only persisted *after* a
successful `send_report()` call, so a failed send doesn't get silently
swallowed — the next check retries it. Composes its own subject/body and
sends through the existing `send_report()` rather than new email plumbing,
same as every other alerting mechanism in this codebase. Registered as its
own Windows Task (`Leviathan-Heartbeat`, 2:00 PM and 8:00 PM daily) via
`scripts/setup_heartbeat_scheduler.ps1` — deliberately *not* chained after
the main run, since its entire job is noticing that the main run's own
scheduler stopped firing. Smoke-tested against the real DB:
`python scripts/heartbeat_check.py --dry-run` → `OK — last run 8.9h ago`.
11 tests in `tests/test_heartbeat_check.py`.

**2. Graceful degradation on API shape change.** `main.py` gained
`_validate_market_shape(markets)`, checking every fetched market for the
five fields the rest of the pipeline assumes exist
(`ticker`, `close_time`, `yes_bid_dollars`, `yes_ask_dollars`, `title`).
Wired into step 2 right after the existing fetch/fallback try/except: if
at least `config.markets.shape_anomaly_min_sample` (default 20) markets
were fetched and more than `shape_anomaly_threshold` (default 50%) of them
are missing a required field, the run aborts *before scoring* and sends an
ALERT email, instead of silently treating what's likely a Kalshi response
shape change as normal (empty/garbage) data. This is the same failure
class as the 2026-07-25 bug sweep (`fetch_orderbook`/`fetch_trades`
assumed response shapes that turned out not to exist) — `docs/RUNBOOK.md`
references that precedent directly. New config keys `shape_anomaly_threshold`
/ `shape_anomaly_min_sample` added to `config.json` and `config.example.json`.
6 tests in `tests/test_market_shape_validation.py`.

**3. Runbook.** `docs/RUNBOOK.md` — diagnosing a failed/missing run
without reloading full project context: the three things a heartbeat
alert can't distinguish on its own (scheduler didn't fire / task fired but
the run failed early, most commonly Kalshi auth / crashed uncaught
mid-pipeline), the empty-runs-table case, and the shape-anomaly-abort
case, plus a quick-reference command table.

Full suite green after all three pieces: 1823 passed, 1 skipped. Backlog
item marked `done`; see `backlog/backlog.json` for the full SHIPPED note.

Both remaining `ready` items (`replay-instrument-validation`,
`price-blind-arm`) are blocked by the same external factor, not by design
or code: the real Anthropic API key still returns
`401 - authentication_error: API key is invalid` on every live call
(re-confirmed directly this session), and both items require real
`backend="api"` scoring calls to produce anything to validate.
`replay-instrument-validation` additionally needs the replay corpus at
`n>=300` (currently 0 rows, itself blocked on the same key). Neither is
something further code changes can route around.

---

## 2026-07-26 — Fix: BACKLOG.md corrupted on GitHub by a self-authored test

User noticed the backlog on GitHub wasn't showing completed/future items —
it showed exactly one fake item ("test-item") and zero Done/Locked/Blocked.

**Root cause:** the `--email` persistence fix shipped earlier
(`backlog/checker.py run()`) added `tests/test_backlog_checker.py::test_email_mode_persists_newly_unlocked_status_to_disk`,
which runs the real `checker.py --email` via subprocess against a
synthetic one-item backlog to verify the fix. `--file`/`--db` were
correctly isolated to tmp paths, but `run()`'s call to `write_markdown(backlog, metrics)`
had no destination argument at all, so it always targeted the hardcoded
real repo-root `BACKLOG.md` (`write_markdown(..., dest=BACKLOG_MD)`)
regardless of which backlog.json was actually being processed. Every
local test-suite run silently overwrote the real `BACKLOG.md` with that
synthetic test content, and the corrupted file was committed and pushed
in the next batch without being noticed (pytest was green — the bug is
invisible to a pass/fail check, only visible by reading the file's actual
content, which nothing did).

**The real `backlog/backlog.json` was never touched** — `save_backlog()`
was already correctly parameterized on `backlog_path` from the earlier
fix, so all 43 real items and their statuses were intact throughout. Only
the rendered `BACKLOG.md` markdown was corrupted.

**Shipped:**
- `backlog/checker.py`: `run()` now takes a `markdown_path` parameter
  (default: the real `BACKLOG.md`, so production/scheduled behavior is
  unchanged) and passes it through to `write_markdown(..., dest=markdown_path)`.
  Added a `--markdown` CLI flag so this is controllable from the command
  line too.
- Regenerated the real `BACKLOG.md` from the actual (never-corrupted)
  `backlog.json` — 43 items, 26 done / 3 ready / 9 locked / 5 blocked,
  correctly reflecting everything shipped this session.

**Tests:** `tests/test_backlog_checker.py` — the two existing subprocess
tests now pass `--markdown` pointed at a tmp path (closing the hole), plus
a new direct regression guard,
`test_email_mode_never_writes_real_repo_backlog_md`, that snapshots the
real `BACKLOG.md`'s content before running the checker against a
synthetic "canary" backlog and asserts it's byte-for-byte unchanged after
— this is the test that would have caught the original bug immediately,
and prevents this exact failure mode (a test with real subprocess/file-
write side effects that aren't fully isolated) from recurring here again.
Full suite: 1806 passed, 1 skipped, 0 failed.

**Lesson repeated from this same session:** this is the second time a new
test's real subprocess/file-write side effects weren't fully isolated
from the actual repo (the first was `analysis/resolve_first.py`'s
snapshot fallback, caught before it was pushed). This one shipped because
verifying "does the intended file get written correctly" doesn't verify
"does anything unintended also get written" — the latter needs an
explicit before/after diff of files the test didn't mean to touch, not
just asserting the intended output looks right.

---

## 2026-07-26 — Weekly digest HTML styling + whale activity section

User-requested (not a backlog item): style the weekly digest email the
same way the daily report already is, and surface whale-flagged markets
with position/EV detail.

**Real bug found and fixed along the way:** `core/logger.py log_pass()`
hardcoded `whale_detected=0` and `whale_direction=None` on every PASS row
regardless of the actual signal dict, even though the caller (`main.py`)
always has the real values in scope at the call site. Since most
whale-flagged markets end in a Claude PASS (no confident edge), this
meant the large majority of whale flags silently never reached the
database at all — `get_stats_by_confidence`'s whale/no_whale split
(`core/logger.py:1567`) has been systematically wrong for every PASS row
since this code was written. Fixed to read the real values from `signal`.

**Shipped:**
- `core/report.py render_weekly_html()` (new): renders the weekly digest
  in the same visual system as the daily report's `render_html()` — dark
  theme, IBM Plex Mono, 600px table-based container, same header banner
  and color palette. Unlike the daily HTML (which intentionally drops the
  Track Record section since Power BI covers that for daily use), the
  weekly HTML keeps it — the weekly digest's whole purpose is a
  track-record-style summary.
- `core/report._week_whale_rows()` (new, shared by text + HTML): whale-
  flagged markets this week, deduplicated by ticker. EV is computed
  assuming the **whale's own direction**, not Claude's final call —
  reusing the same real `market_price`/`our_estimate` already scored, just
  with the direction assumption swapped, since computing EV from Claude's
  direction would be empty for nearly every row (most end in PASS).
  Position size (`whale_max_trade_size`, new column, threaded from
  `main.py`'s existing `whale.get("max_trade_size")`) is `None` — not
  fabricated as 0 — on any row logged before this fix.
- `compile_weekly_digest()` (text): added a matching "WHALE ACTIVITY THIS
  WEEK" section using the same `_week_whale_rows()` data.
- `main.py`: weekly-digest send block now renders and sends both bodies
  via `send_report(..., html_body=...)`, and now also passes
  `lv_stats=logger.get_stats_by_leviathan_score()` (previously never fed,
  so the digest's own LV-grade table silently never rendered even though
  the code path for it already existed).

**Known limitation, not fixed (can't be):** this week's actual whale
flags (11 from the 2026-07-26 run, plus any earlier this week) were
logged *before* the `log_pass()` fix, so their real `whale_detected`
value was already discarded before reaching the DB — refiring the digest
correctly shows "no whale-flagged markets this week" for that population,
which is the honest answer, not a bug in the new code. Only whale-flagged
markets that end up as actionable YES/NO signals (not PASS) were ever
persisted correctly, since `log_signal()` didn't have this bug. Every
future PASS row is unaffected going forward.

**Tests:** `tests/test_weekly_html.py` (new, +17: whale-row filtering/
dedup/sort, the whale-direction-not-Claude's-call EV design decision,
position None-vs-fabricated-zero, HTML structure, empty-state messages,
escaping), `tests/test_logger.py` (+1: `log_pass` whale persistence
regression). Full suite: 1805 passed, 1 skipped, 0 failed.

**Refired:** the weekly digest was resent directly (`compile_weekly_digest`
+ `render_weekly_html` + `send_report` against the real DB/config) rather
than re-running the full pipeline — no new Kalshi/Claude calls, just a
re-send with the new styling.

---

## 2026-07-25 — Full codebase bug sweep: 11 HIGH-severity fixes

User-requested full sweep ("leave nothing unturned"), not a backlog item.
Six parallel agents each combed a disjoint slice of the ~18,600-line
non-test codebase for concrete, verifiable bugs (crashes, silent wrong
behavior, corrupted stats) — not style. Combined with my own review of
`main.py` and `backtesting/*.py` (the code freshest from today's work),
this surfaced roughly 30 verified findings, ranked and reported to the
user. Fixed all 11 rated HIGH severity; the ~19 MEDIUM/LOW findings were
reported but left unfixed pending a future pass.

**Own finding, fixed first:** `backtesting/asof_reconstruction.py`'s
`_find_candlestick()` had a genuine look-ahead leak — it could select a
candle whose period ends up to ~24h *after* the requested as-of instant
(confirmed against a real ticker: requesting 2026-05-18T00:00 returned a
candle covering through 2026-05-18T04:00), and didn't skip candles with
missing `price.close_dollars` (a no-trade day), which would silently flow
`None` into `scanner.score_market()`'s `float(x or 0)` coercions as an
indistinguishable fabricated zero. Both fixed: the function now only
selects the latest candle with `end_period_ts <= as_of_ts` and a real
price. 3 new tests added on top of the existing 19.

**11 HIGH-severity fixes, each with new regression tests and verified
against real Kalshi data where applicable:**

1. **`core/kalshi.py fetch_orderbook()`** — read a nonexistent `"orderbook"`
   envelope key; real key is `orderbook_fp`. The order-book-imbalance
   signal (`ob_flag`) was permanently `False` for every market, silently,
   forever. Fixed in `core/scanner.py compute_orderbook_signal()` to read
   the real shape (`yes_dollars`/`no_dollars` level arrays).
2. **`core/kalshi.py fetch_trades()`** — called a nonexistent
   `/markets/{ticker}/trades` endpoint (real one: `/markets/trades` with a
   `ticker` query param, same path `fetch_recent_trades()` already used).
   Whale detection silently saw zero trades for every market, forever.
3. **`core/llm.py` cost ceiling undercount** — when Claude doesn't call
   the tool on the first attempt, `_force_tool()`'s second billed call was
   the only one priced; the first call's tokens (typically the larger of
   the two — full system prompt + web search) were dropped from both the
   returned cost and the persisted daily ceiling total. Reproduced: a
   50k-input/2k-output first call + a 200/100 forced call reported
   `$0.003` instead of the true `$0.183`. Fixed `_finalize_token_info` to
   sum both calls.
4. **`core/scorer.py _score_via_cli()`** — the DEFAULT backend
   (`config.llm.backend` defaults to `"cli"`) never validated the parsed
   response shape, unlike the API path. A response missing `"ticker"`
   would crash the entire scheduled run via an uncaught `KeyError` in
   `main.py`'s unguarded `{s["ticker"]: s for s in claude_scores}`. Fixed
   by reusing `core.llm._validate_scores()`.
5. **`core/logger.py pull_real_fills()`** — matched real Kalshi fills
   against the *most recent* paper signal per ticker regardless of
   direction, so a later PASS decision could displace the actual YES/NO
   call a fill should confirm, marking correct real-money fills
   "contradictory". Fixed by reusing the existing `_NO_PASS` filter
   (already used elsewhere in this file, just not here).
6. **`core/whales.py`** — `signal_trades = large_trades or block_trades`
   silently dropped every block trade from the direction vote whenever any
   large trade existed, contradicting its own comment ("majority side of
   large + block trades"). Reproduced: one 310-contract YES trade + two
   290-contract NO block trades (580 combined) incorrectly voted "YES".
   Fixed to vote over the deduplicated union of both sets.
7. **`backlog/checker.py run(email_mode=True)`** — mutated the in-memory
   backlog dict and rendered `BACKLOG.md` from it, but never called
   `save_backlog()` in the `--email` branch (only the interactive C/M path
   did). Since this mode is the one actually scheduled via Windows Task
   Scheduler per the module's own docstring, `backlog.json` on disk never
   advanced past `"locked"`, so the same gate got re-reported as "Newly
   Unlocked" in every subsequent scheduled run, forever. Fixed to persist
   immediately after `compare_statuses()`, in both modes.
8. **`analysis/filter_stats.py` + `net_edge_analysis.py`** —
   `scanner.score_markets()` returns `(scored_list, hp_filtered_count)`;
   both scripts used the return value unpacked, crashing on every real run
   the moment `dedup_by_event`/flag-filtering touched the tuple. The real
   `config.json` has `dedup_by_event: true`, so this fired unconditionally.
9. **`analysis/drift_diagnosis.py`, `flag_mode_compare.py`,
   `threshold_sweep.py`** — all three still did `import scanner`, a
   pre-`core/` package reorg style; `ModuleNotFoundError` on the first
   line executed. The latter two also had the same `score_markets`
   unpacking bug as #8 underneath.
10. **`analysis/resolve_first.py load_snapshot()` fallback** — wrote a
    bare JSON array instead of the `{"header":...,"markets":...}` envelope
    `analysis/snapshot_markets.py` writes and `backtesting/asof_reconstruction.py`
    requires from the *same shared directory*. A resulting file would
    break historical-state reconstruction for every ticker/date via an
    uncaught `AttributeError`, not just corrupt itself. Fixed to write the
    matching envelope; also hardened `asof_reconstruction._load_snapshot_index()`
    to defensively skip any wrong-shaped file rather than crash on it, as
    a second line of defense regardless of what wrote it.

**Tests:** ~29 new tests across the fixes above (`test_scanner.py`,
`test_kalshi_trades_orderbook.py` [new], `test_llm.py`, `test_scorer.py`,
`test_logger.py`, `test_whales.py`, `test_backlog_checker.py`,
`test_filter_stats.py` [new], `test_net_edge_analysis.py` [new],
`test_analysis_scanner_scripts.py` [new], `test_asof_reconstruction.py`,
`test_resolve_first.py`), each reproducing the exact failure scenario
before confirming the fix. One test-hygiene catch along the way: my first
fix for finding #10 called `analysis.snapshot_markets.save_snapshot()`
directly, which uses its own hardcoded `SNAPSHOT_DIR` rather than
`resolve_first.py`'s patchable one — running the existing test with that
fix in place wrote a real stray file into the actual `data/snapshots/`
directory (caught immediately via the existing test's assertion failing
for the wrong reason, and cleaned up before finalizing). Reworked to
construct the envelope inline against `resolve_first.py`'s own
`SNAPSHOT_DIR` instead.

Full suite: **1787 passed, 1 skipped, 0 failed** (95.9s), up from 1758
before this sweep.

**Not fixed, reported to the user for a future pass (~19 MEDIUM/LOW
findings):** `core/logger.py resolve_outcomes()` silently resolving PASS
rows; `core/report.py`'s EV-floor-filtered count including
`research_probe` rows; `get_stats_by_close_horizon()` bucketing negative
day-deltas as "urgent"; `sources/odds_api.py` caching a failed/partial
fetch as valid for 6 hours; `sources/polymarket.py`'s first-index
fallback on 3+-outcome markets; `sources/accounts.py`'s asymmetric
"up"/"down" outcome handling; `sources/metaculus.py`'s falsy-zero `q2`
coalescing; `scripts/verify_pnl.py`'s PnL formula diverging from
`core/logger.py`'s real one; `backlog/checker.py evaluate_triggers()`'s
missing-metric-defaults-to-0 landmine; `backlog/engine.py validate_item()`
not validating the `status` field; `scripts/gate_notifier.py` pruning
gates that could reappear; `core/scanner.py estimate_base_rate()`'s
antitrust-before-chip-export keyword ordering contradicting its own
comment; `core/scanner.py dedup_by_event*()` crashing on an explicit
`event_ticker: None`; `analysis/research_probe.py _mid()`'s one-sided
fallback; `analysis/eval_rescore.py` joining to the wrong signal instance
on a duplicate ticker; `main.py`'s hardcoded `event_count=0` in every
live snapshot header.

---

## 2026-07-25 — replay-runner: retroactive replay driver over the settled corpus

Next `ready` item (priority 3, unlocked once both replay-asof-reconstruction
and llm-cost-ceiling were done).

**As-of date selection, verified against real data first:** pulled full
candlestick lifetimes for several real `settled_markets` tickers before
choosing a rule. Findings: some tickers show genuine multi-week price
uncertainty before close; others are already near-certain (0.9+/0.1-) days
out; some sparse/thin markets have `None` (no-trade) candles on many days;
and one long-running tennis-futures ticker showed a 5-day round-trip to
0.08 and back to 0.85 that looks like a data anomaly rather than real
sentiment — exactly the kind of thing that would have silently corrupted a
naively-chosen fixed lookback. Landed on: try lookback windows
30/14/7/3/1 days before `close_time`, furthest first, and accept the first
one whose reconstructed `mid_price` falls inside
`config.markets.min_market_price`/`max_market_price` — the same price band
`core.scanner.filter_markets()` already applies on the live pipeline. This
reuses an existing, already-defensible threshold instead of inventing a new
number, and directly prevents "backtesting" a market that's already been
telegraphed by consensus price.

**Shipped:**
- `backtesting/replay_runner.py` (new module): `run_replay(config,
  max_markets)` samples settled tickers not yet replayed, finds a
  qualifying as-of state via the rule above, scores it through
  `core.scorer.score_markets()` with `llm.backend` forced to `"api"` (real
  metered billing + the daily cost ceiling — the CLI/Pro path has no cost
  concept and would defeat the ceiling entirely), and persists to a new
  `replay_signals` table — idempotent `INSERT OR IGNORE` on `ticker`, never
  joined with live `signals`. A ceiling-triggered stop is a pause (partial
  results already committed), not lost work.
- `export_and_report()`: writes `replay_signals`/`replay_resolutions` CSVs
  to `data/replay_export/` (never mixed into the live
  `data/powerbi_export/` directory) and drives the existing, **unmodified**
  `backtesting.harness.BacktestRunner` over them.
- `core/scorer.py`: additive `now` parameter on `build_prompt()` and
  `score_markets()` (default `None` → real `datetime.now()`, so every live
  caller is unaffected) — without it, the prompt's "days remaining" text
  would silently describe the wrong horizon for a replayed historical
  market. Verified directly: building a prompt with `now` pinned to
  2026-06-01 for a market closing 2026-06-20 correctly renders "19d
  remaining", not a number based on today's real date.

**LOOK-AHEAD CONTAMINATION — permanent, structural, stated plainly in the
module docstring, not something this item fixes:** Claude's training data
and any live web search may already know how a replayed market resolved.
Schema separation protects downstream analysis from silently pooling
replay results with live ones; it cannot remove the contamination itself.

**KNOWN SIMPLIFICATION:** does not replicate `main.py`'s post-hoc
confidence-downgrade business rules (HIGH→MED gates) — grades Claude's raw
response as returned. Extracting those rules into a shared helper would
mean touching the live pipeline's own logic, a larger change than this
item's scope.

**Verified against real data, cost held back deliberately:** a full dry run
against the real `settled_markets`/candlestick data (only the actual paid
Claude call stubbed) correctly found 5 of 9 sampled candidates
reconstructable and price-band-legitimate, with `now` correctly reflecting
each historical as-of date. **No real (metered) scoring call has been made
yet** — every other real-cost action this session (the live `main.py` run)
got an explicit heads-up first, and this module forces metered billing
where the live path normally has none, so the same courtesy applies before
the first genuine invocation.

**Tests:** `tests/test_replay_runner.py` (+19: table init, candidate
selection and its already-replayed/series_ticker exclusions, price-band
bounds, as-of lookback preference and its data/band-exhausted None paths,
hit/miss/PASS grading, forced-API-backend confirmation, persistence,
cost-ceiling stop behavior, idempotency across repeated calls, max_markets
bound, CSV export shape). `core.scorer.score_markets` mocked throughout —
no real API spend in the test suite itself. Full suite: **1758 passed, 1
skipped, 0 failed** (99.3s).

**Unlocked:** `replay-instrument-validation` (both its dependencies —
this item and `market-baseline-brier` — are now done).

**Top 3 next steps:**
1. Get explicit go-ahead to run a small real batch (e.g. `--max-markets 3`)
   to confirm the API scoring loop behaves correctly against genuine
   Claude responses, not just mocks — and observe real per-market cost to
   replace the `$20/day` ceiling's "starting guess, not a measured figure"
   placeholder with an actual number.
2. Once real cost is observed, size a sensible default `max_markets` for
   unattended/scheduled replay runs (if any get scheduled) against the
   daily ceiling.
3. `replay-instrument-validation` (newly unlocked) is next in priority
   order once real replay data exists to validate instruments against.

---

## 2026-07-25 — fix-fetch-market-history-endpoint: live price-trend feature repaired

The bug found while building replay-asof-reconstruction (below), filed as its
own item and picked up next since it was already highest-priority `ready`.

**Fix:** `main.py`'s step-5 loop (was line 364) called the now-deleted
`core.kalshi.fetch_market_history()`, which hit a dead endpoint
(`/markets/{ticker}/history`) and had silently returned nothing for every
call site since it was written. Replaced with `fetch_market_candlesticks()`
(added for replay-asof-reconstruction). No new plumbing was needed for
`series_ticker` — it's already merged onto every market dict at fetch time
(`main.py` line ~155), so the item's own action text overstated that part
of the work. `period_interval` is chosen per horizon: 60 (hourly) for
INTRADAY's 1-day lookback so there are enough points to show a trend, 1440
(daily) for every longer horizon. Candlestick `price.close_dollars` sits on
the same 0-1 probability scale the old code assumed for `yes_price`, so the
existing `start*100`/`end*100` trend-percentage formatting needed no
rescaling.

**Verified against real data:** `KXCITRINI-28JUL01` (MONTHLY horizon) — 7
daily candles, produced `-5.0% (24.0% → 19.0%) — declining (7d)`, where the
old code silently produced nothing.

`fetch_market_history()` itself was deleted outright rather than left as
dead code — zero remaining callers, zero dedicated tests, no reason to keep
it as a landmine for a future caller to accidentally revive.

**Tests:** no new test file — `core.kalshi.fetch_market_candlesticks()` was
already covered by `tests/test_kalshi_settled_fetch.py` from the prior item;
`main.py`'s orchestration logic has no existing unit-test harness (it's
verified by actually running the pipeline, which this fix was smoke-tested
against directly). Full suite still green after the edit.

---

## 2026-07-25 — replay-asof-reconstruction: historical market-state reconstruction

Next `ready` item (priority 2, unlocked by replay-settled-fetcher above).

**Correction to the record first:** this item's own action text and
replay-settled-fetcher's note above both assumed `data/snapshots` only
reaches back to 2026-07-08 — that claim was never actually checked against
the files on disk. A directory listing today shows **92 snapshot files
starting 2026-06-16**, three weeks earlier than believed. `core/kalshi.py`
and `settled_fetcher.py`'s docstrings have been corrected; the
replay-settled-fetcher entry above is left as-is (historical record).

**Architecture, verified against real data before writing any code** (per
the advisor's push-back on the first pass — verify, don't assume):
1. `core.kalshi.fetch_market_history()` — the function this item's design
   assumed would serve as the "Kalshi history beyond snapshots" fallback —
   is **broken**. It calls `/markets/{ticker}/history`, which doesn't exist
   on Kalshi's API: every ticker tried, including active high-volume
   markets, returned a plain-text `404 page not found`, never JSON. The
   function's `if status==404: return []` has silently swallowed this for
   every call site since it was written, meaning `main.py:364`'s live
   price-trend/drift-history report text **has never actually produced
   data**. Filed as its own backlog item (`fix-fetch-market-history-endpoint`)
   rather than fixed here — different blast radius (live pipeline call site,
   not this new backtesting module).
2. The *real* endpoint is `/series/{series_ticker}/markets/{ticker}/candlesticks`
   — confirmed working, returns real OHLC + yes_bid/yes_ask + volume/open_interest
   per period. `period_interval` only accepts 1, 60, or 1440 minutes
   (confirmed empirically; other values reject with a validation error).
3. `core.scanner.score_market()` and everything it calls
   (`compute_drift_signal`, `compute_whale_reversal`, `compute_spread_signal`,
   `estimate_base_rate`, `get_heuristic_label`, `kalshi_fee`) are pure
   functions of the market dict + config — confirmed via grep that none of
   them call `datetime.now()`. `filter_markets()` does, but replay never
   calls that function. So calling `score_market()` on a reconstructed
   historical dict is safe: no wall-clock leakage from today into a replayed
   date.

**Shipped:**
- `core/kalshi.py`: `fetch_market_candlesticks()` (new function, does not
  touch the broken `fetch_market_history()` or its live call site).
- `backtesting/asof_reconstruction.py` (new module):
  `reconstruct_market_state(config, ticker, as_of_date)` — two source tiers,
  tried in order: **EXACT** (latest `data/snapshots/*.json` at or before
  `as_of_date` that contains the ticker — verbatim bid/ask/last-price, no
  aggregation) then **APPROXIMATE** (`fetch_market_candlesticks()`, daily
  close-of-period candle — a real precision loss to "within one day," named
  explicitly via a `reconstruction_tier` field rather than blurred). Returns
  `None` if neither tier has data for that ticker/date — never fabricates,
  same discipline as every other missing-data decision this session.
  Reuses `scanner.score_market()` **verbatim** on the reconstructed raw
  dict — no scanner/heuristic logic duplicated — so the output is
  guaranteed byte-for-byte the same shape `core.scorer.score_markets()`
  already consumes, plus three additional transparency keys
  (`reconstruction_tier`/`reconstruction_source`/`reconstruction_as_of`)
  that downstream `.get()`-based consumers simply ignore.
- Static metadata (`series_ticker`/`event_ticker`/`category`/`title`/
  `close_time`) sourced from `settled_markets` if present, else merged
  across every snapshot occurrence of the ticker (newest-first, first
  non-empty value per field wins) — a single nearest-snapshot lookup was
  tried first and silently produced blank `series_ticker` for tickers whose
  earliest captured snapshot predated that field being populated; caught by
  a real smoke test, not by inspection.
- Permanent, structural limitation (documented in the module docstring, not
  a bug): whale/watchlist/Polymarket cross-reference enrichment (main.py
  steps 4-6) has no historical archive, so those keys are simply absent on
  every replayed market.

**Verified against real data**, not just mocked tests: reconstructed
`KXNFLENDSTREAK-40NYJ-3031` as of today via the exact/snapshot tier, and
`KXLIRRSTRIKE-26-MAY19` (a real settled market, pre-snapshot-floor) as of
2026-05-18 via the approximate/candlestick tier — both produced fully
scored, correctly-shaped output; an as-of date before the market's actual
open correctly returned `None`.

**Tests:** `tests/test_asof_reconstruction.py` (+19: snapshot indexing,
exact-tier lookup and its at-or-before boundary, static-metadata merge and
its settled_markets-priority, candlestick-tier candle selection and its
exception/empty handling, full end-to-end integration for both tiers,
confirms exact tier wins when both are available, confirms `time_horizon`
derives from `as_of_date` not wall-clock `now()`), plus
`tests/test_kalshi_settled_fetch.py` (+5: candlestick endpoint shape,
default `period_interval`, 404 handling, and a regression guard that the
new function hits a genuinely different path than the broken
`fetch_market_history()`). Full suite: **1739 passed, 1 skipped, 0
failed** (199.8s).

**Unlocked:** `replay-runner` (both its dependencies — this item and
`llm-cost-ceiling` — are now done).

**Top 3 next steps:**
1. Fix `fetch_market_history()`'s dead endpoint (`fix-fetch-market-history-endpoint`,
   now `ready`) — a real, currently-live bug independent of replay.
2. Build `replay-runner`: drive `backtesting.harness` over `settled_markets`
   using `reconstruct_market_state()`, grading each replayed score against
   the known outcome in a schema-separated table. Note `core.scorer.build_prompt()`
   computes `days_left` via `datetime.now(timezone.utc)` (line 623) for its
   own prompt text — out of scope for this item, but `replay-runner` will
   need to pass an as-of-relative day count instead, or accept degraded
   prompt text for replayed markets.
3. Consider whether candlestick `period_interval=60` (hourly) gives replay-runner
   meaningfully better precision than the current daily default, now that a
   real corpus exists to measure it against.

---

## 2026-07-25 — replay-settled-fetcher: Kalshi settled-market corpus

Next `ready` item (priority 2). The local snapshot archive (`data/snapshots`)
only reaches back to 2026-07-08, capping any backtesting corpus built from it
alone well below what's useful for the eventual replay pipeline.

**Finding before writing any persistence code:** naively querying
`/markets?status=settled` directly returns the same KXMVE parlay flood
`fetch_events()`'s docstring already warned about for open markets — sampled
999 of 1000 results were KXMVE multi-game-extended parlays. Querying
`/events?status=settled` instead (then each settled event's markets)
returned 0 of 400 KXMVE — confirmed empirically before building on it,
rather than assumed.

**Shipped:**
- `core/kalshi.py`: `fetch_settled_events()` and `fetch_settled_event_markets()`
  — the settled-status counterparts to the existing open-only `fetch_events()`/
  `fetch_event_markets()`, which are left untouched so no existing caller's
  behavior changes. `max_fetch` is independent of `config.markets.max_events`
  (that value bounds the live per-run scan budget; this is for corpus depth).
- `backtesting/settled_fetcher.py` (new module): fetches settled events,
  excludes KXMVE, fetches each event's settled markets, and persists to a
  new `settled_markets` table via idempotent `INSERT OR IGNORE` keyed on
  `ticker`. `series_ticker`/`category` are carried over from the event object
  (same lesson as the earlier Kalshi-link work — they don't exist on the raw
  market object). **Read-only against `signals`/`runs`** — confirmed by a
  test that the module never creates or touches either table.
- CLI entry point: `python -m backtesting.settled_fetcher [--max-events N]`.

**Real backfill run** (not just a mocked-test proof): 1999/2000 events
scanned (one transient connection reset on a single event, caught and
skipped rather than aborting the whole run), 12,681 markets fetched, 12,593
newly inserted, 81 skipped as unresolved (voided/no clean YES-or-NO result).
`settled_markets` now holds **12,600 rows** across 1,203 events / 656
series, `close_time` spanning **2026-05-19 to 2026-07-25** — **4,505 rows
(36%) predate the 2026-07-08 snapshot floor**, the concrete depth problem
this item exists to fix.

**Tests:** `tests/test_kalshi_settled_fetch.py` (+7: status/param
correctness, pagination, max_fetch cap, empty-page stop, a regression guard
proving the existing open-only `fetch_event_markets()` is unchanged),
`tests/test_settled_fetcher.py` (+9: table creation and isolation from
signals/runs, persistence, series_ticker/category carry-through, KXMVE
exclusion, unresolved-market skip, idempotent re-run, missing-ticker
graceful skip). Full suite: **1715 passed, 1 skipped**.

**Side effect:** marking this item `done` and re-running the checker's
trigger evaluation unlocked `replay-asof-reconstruction` (its only
dependency) — now `ready`.

### Top 3 next steps

1. `replay-asof-reconstruction` (newly ready) — the next link in the replay
   chain; its hard requirement is matching `core.scorer.score_markets`'s
   existing input shape exactly, per its own notes.
2. `price-blind-arm` (ready) — shadow-scoring arm, unblocked since
   2026-07-25's `llm-cost-ceiling` work.
3. Investigate the single connection-reset failure
   (`KXSECAG-26DEC31`) if a re-run is convenient — likely transient, but
   worth confirming that ticker resolves on retry rather than silently
   staying absent forever.

---

## 2026-07-25 — llm-cost-ceiling: daily spend cap in core/llm.py

Next `ready` item off the backlog (priority 2). Real Anthropic API spend
via `score_via_api`/`probe_via_api` was unbounded — unlike `main.py`'s
CLI/Pro-subscription scan path, which has no per-token bill. Matters most
once `replay-runner` starts calling these at volume.

`_check_cost_ceiling(config)` reads `daily_cost_ceiling_usd` from
`config.json`'s `llm` section (default $20/day, `DEFAULT_DAILY_COST_CEILING_USD`
if unset) and is called **before** issuing each API request in both
`score_via_api` and `probe_via_api` — a pre-flight check, not a post-hoc one,
so a caller looping over many markets (`replay-runner`) stops making new
calls the instant the ceiling is hit rather than only finding out after the
call that breached it already ran. Raises `LLMCostCeilingExceeded`.

Running total persists in `data/llm_daily_cost.json` (gitignored, same
pattern as `scripts/gate_notifier.py`'s `data/gate_state.json`) so the
ceiling holds across process restarts and across every caller — research
probes, eval re-scoring, and the future replay-runner all share the same
counter, not per-process state that resets on every invocation. Resets on
UTC day rollover; a stale prior-day total is never carried into today's
figure. `_finalize_token_info()` is the single point both API functions
route through before returning, so accumulation can't be skipped by one
code path and not the other.

Surfaced in **both** report footers (`core/report.py`, text and HTML) via
`core.llm.get_daily_cost_usd()` — shown alongside, not instead of, the
existing "Cost (est.)" line, since that line is a notional Pro-subscription
equivalent for the main scan and this is a different, real number from a
different code path. Verified via `--dry-run` in both bodies.

**Side effect:** running the checker's `evaluate_triggers`/`compare_statuses`
after marking this item `done` correctly unlocked `price-blind-arm` (its
other dependency, `market-baseline-brier`, already shipped 2026-07-23) —
now `ready`. `multi-sample-scoring` also depends on this item but stays
`blocked` (its own `resolved_count>=25` trigger isn't met yet at
`resolved_count=8`) — the engine only flips blocked→ready in one step when
both conditions clear together, so it correctly doesn't move partway.

**Tests:** `tests/test_llm.py` (+13: state helpers including stale-day
reset, ceiling check pass/raise/default-ceiling, integration tests proving
the API client is never called once already breached, cost accumulates
across successive calls, `daily_total_usd` present and correct). Added an
autouse fixture isolating every test in the file from the real state file
on disk. Full suite: **1699 passed, 1 skipped**.

### Top 3 next steps

1. `price-blind-arm` (newly ready, priority 4) — now unblocked; a
   shadow-scoring mode with no market-price line, the natural complement to
   `preregistration`'s falsification test.
2. `replay-settled-fetcher` (ready, priority 2) — read-only, extends corpus
   depth past the 2026-07-08 local snapshot floor.
3. Once `replay-runner` exists and makes its first real API calls, revisit
   `daily_cost_ceiling_usd`'s $20 default — it's a starting guess, not a
   measured figure, per its own config note.

---

## 2026-07-25 — preregistration: pre-registered kill criterion (no code)

Next `ready` item off the backlog, priority 1 for a reason: worthless if
written after n=50's data is seen, so it had to happen before any further
data accumulates. No code changed — pure documentation, per the item's own
scope.

Wrote `docs/PREREGISTRATION.md`. Registers, in advance and dated
(2026-07-25), a falsification criterion for the scorer edge hypothesis:

- **Metric:** a *paired* per-row comparison — `brier_market_i - brier_scorer_i`
  for every resolved paper signal with both `our_estimate` and `market_price`
  present — not a naive comparison of the two aggregate Brier scores as if
  independent samples, which would understate the evidence needed and
  overstate apparent significance.
- **Checkpoint:** first paired n ≥ 50.
- **Pass/fail bar:** the 95% CI (z=1.96, matching `core.report._wilson_ci()`'s
  existing convention) on the mean paired delta must exclude zero on the
  positive side. A positive point estimate alone is explicitly insufficient
  — noise that happens to land favorably at n=50 is not evidence.
- **On FAIL:** precisely scoped — new heuristics/scoring changes halt;
  `price-blind-arm`, replay validation, and this document's own dated
  amendment are explicitly *not* halted (the tools needed to diagnose *why*
  must stay available). Resuming requires a written post-mortem first, not
  just more elapsed time.

Recorded current state for dating purposes only, explicitly labeled as
context and not a checkpoint result: paired n=8 today, resolution rate
suggests n=50 is a long way off — which is exactly why locking the
criterion in now (genuinely blind to the eventual outcome) rather than
waiting was the point.

Backlog item marked `done`, `BACKLOG.md` regenerated, zero validation
errors. Full suite re-run as a sanity check (no code touched): **1686
passed, 1 skipped**, unchanged.

### Top 3 next steps

1. `llm-cost-ceiling` (ready, priority 2) — prerequisite for both
   `replay-runner` and `price-blind-arm`; unblocks the most items of
   anything currently ready.
2. `replay-settled-fetcher` (ready, priority 2) — read-only, extends corpus
   depth past the 2026-07-08 local snapshot floor.
3. When paired n approaches 50, evaluate the checkpoint in
   `docs/PREREGISTRATION.md` and append the dated result — do not
   pre-emptively peek at `ci_95_low` before n is actually reached.

---

## 2026-07-24 — powerbi-schema-hardening: run_id FK, source audit, blank-vs-zero audit

Implements the `powerbi-schema-hardening` backlog item appended (but
deliberately not implemented) in the prior session — its own handoff
explicitly scoped this to "a separate session after," now that one. Order
followed the item's own ordering.

### 1 — `run_id` foreign key

`run_id` added as an explicit `signals.csv` column (`core/export_to_csv.py`
`WHITELIST`), joining to `run_id` in `runs.csv`. Ran `backfill_run_id()`
(new, `core/logger.py`) against the real DB: **0 backfilled, 17
unrecoverable**. All 17 are `real_fill`/`research_probe` rows — `log_probe()`
never writes `run_id` at all, and `pull_real_fills()` hardcodes `run_id=''`
regardless of match status, because a fill is an execution event, not a
scan run. One genuine (non-guessed) recovery path exists and is checked:
a `real_fill` row's `signal_call_id`, if it points to a matched paper
signal, can borrow that signal's real `run_id` — a true FK traversal.
Nothing on the current data hits this path (none of the 17 blank rows have
a populated `signal_call_id`), but it's implemented and tested for when
future data does. **No nearest-timestamp inference was used or considered
viable** — the pipeline runs twice daily, so timestamp-proximity would
misattribute rows to the wrong run.

### 2 — `source` discriminator

**Corrected an assumption in the item's own notes.** The notes claimed "no
value other than paper currently appears" — false: `audit_source_discriminator()`
(new) found three distinct values on the real DB (`paper` 165, `real_fill` 7,
`research_probe` 10), and `source` is populated on all 182 rows (0 blank).
The discriminator is still reliable despite the wrong value-count
assumption: every paper-only aggregate in `core/logger.py` already filters
via `source='paper' OR source IS NULL`, confirmed already excluding the
other two sources (pre-existing tests). This generalizes correctly to a
future `replay-runner` source value, provided it picks something distinct
from `'paper'` — the mechanism was verified, not just assumed.

### 3 — Blank-vs-zero audit

Written to `docs/POWERBI_EXPORT_SCHEMA.md` — a decision record, not a code
change, per the item's own instruction. Full column-by-column table for
every column with a nonzero blank rate (19 columns), each with a meaning and
a zero-safety verdict. **Headline finding: no column in this export
currently uses blank to mean zero** — every blank means "not computed" or
"not applicable," confirming the pipeline's existing convention rather than
finding a contradiction. The two highest-stakes cases, both already handled
correctly and now explicitly documented: `result`/`is_win`/`pnl_if_traded`
(blank = pending, never LOSS or "broke even") and `market_price`/
`brier_scorer`/`brier_market` (blank = no data, never 0.5 or "perfect
calibration"). No existing column value was altered.

**Do-not list honored:** no column renamed or dropped, no existing value
changed to eliminate a blank, scorer prompt/heuristics/thresholds untouched.

**Tests:** `tests/test_logger.py` (+9: backfill recovery via matched
signal_call_id, unrecoverable when no link or the linked row is also blank,
already-populated rows left alone, coverage/discriminator audits broken
down by source, blank-source detection), `tests/test_export_to_csv.py`
(+4: run_id column present, passes through for paper rows, blank-not-"None"
for real_fill, joins correctly to runs.csv; `_DROPPED_COLS` updated since
run_id is no longer excluded plumbing). Full suite: **1686 passed, 1
skipped**.

### Top 3 next steps

1. `title-scraping-fix` (existing backlog item) — the one blank column in
   the audit that's a genuine defect rather than confirmed-expected
   behavior; everything else in the table turned out to be working as
   intended.
2. Re-run `audit_source_discriminator()` before `replay-runner` ships its
   first row, to confirm its new source value doesn't collide with
   `'paper'` — the concrete risk this item's notes were guarding against.
3. Carried over: `preregistration`, re-run the scorer-vs-baseline Brier
   comparison once `resolved_count` is large enough to matter, and
   `llm-cost-ceiling` to unblock the backtesting chain.

---

## 2026-07-23 — Power BI Export Schema: our_estimate, brier_scorer, brier_market

Handoff task 02 (`LEVIATHAN_TASK_02.md`), run immediately after the
market-baseline-brier work above. Two parts.

### Part 1 — Amend `market-baseline-brier` + ship the tightened export

The item's original `action` text ("expose both in the resolved-signal
export") didn't name columns. `signals.csv` carried `market_price` and
`edge` but not the scorer's probability — Brier is `(outcome -
probability)^2`, so without `our_estimate` a dashboard would have to derive
probability as `market_price + edge`, which breaks on the 18 rows where
`edge` is blank. Replaced the `action` field verbatim with the text
specifying `our_estimate`, `brier_scorer`, `brier_market` as explicit
columns.

**Single-source-of-truth refactor** (the actual point of this task, not
just a rename): extracted `brier_component(value, direction, result)` in
`core/logger.py` — the `(value - outcome_binary)^2` building block both
`get_brier_score()` and `get_market_baseline_brier_score()` already computed
inline, now factored into one function both call. Verified behavior-
preserving before touching anything else (same arithmetic order: sum raw
terms, divide, round only the final mean) — ran `test_logger.py` +
`test_calibration.py` in isolation first, 178 passed, before moving on to
the export changes, specifically to isolate which layer broke anything if
something had.

`core/export_to_csv.py` now imports `brier_component` directly and computes
`brier_scorer`/`brier_market` at export time from the same raw columns
(`our_estimate`/`market_price`, `direction`, `result`) analysis/calibration.py's
aggregates read — not from the `market_baseline_brier` DB column persisted
by last goal's `resolve_outcomes()`/backfill (that column still exists and
is still written, just no longer the export's source, so a missing backfill
run can never silently produce a stale CSV number). `our_estimate` added to
`WHITELIST` as an explicit raw column (previously excluded as "pipeline
plumbing" — now a deliberate exception since Brier needs it directly).

Renamed the prior session's `scorer_brier`/`market_baseline_brier` CSV
column names to `brier_scorer`/`brier_market` per the task's explicit
naming — safe because those two names were invented in-session, never
committed, never wired into the `.pbix` (unlike `market_price`/`edge`/etc.,
which this task explicitly forbids renaming).

### Part 2 — New item: `powerbi-schema-hardening`

Appended to `backlog/backlog.json` (42 items total), bumped `updated`,
regenerated `BACKLOG.md`. `python -m backlog.engine status`: zero validation
errors. **Implementation intentionally deferred** — the handoff explicitly
scopes `run_id` FK backfill, the `source` discriminator audit, and the
blank-vs-zero column audit to "a separate session after"; only the backlog
entry itself was added this session.

**Tests:** `tests/test_export_to_csv.py` — renamed/rewrote the Brier-column
test class for the new names, added `our_estimate` presence test and a
cross-check test asserting the per-row CSV values equal `brier_component()`
called directly on the same inputs (the literal single-source guarantee).
Extended `_COMPUTED_COLS_EXPECTED`, removed `our_estimate` from
`_DROPPED_COLS` (it's now intentionally whitelisted). One more pre-existing
hardcoded item-count assertion bumped (41→42), same direct consequence as
the 30→41 fix from the prior entry — not a regression. Full suite: **1674
passed, 1 skipped**.

### Top 3 next steps

1. `powerbi-schema-hardening` itself — `run_id` FK first (per its own
   ordering), since `signals.csv`/`runs.csv` share no join key today and
   Power BI can't relate a signal to its run's cost/token/runtime data.
2. Re-point the `.pbix` at the renamed `brier_scorer`/`brier_market` columns
   if any visual already referenced the prior session's names (unlikely —
   that work was never committed — but worth a quick check before this
   merges).
3. Same three carried over from the prior entry: `preregistration`,
   re-run the scorer-vs-baseline comparison once `resolved_count` is large
   enough to matter, and `llm-cost-ceiling` to unblock the backtesting chain.

---

## 2026-07-23 — Backlog Intake (11 items) + Market-Price Baseline Brier

Handoff task (`LEVIATHAN_TASK.md`), two phases, executed in order, nothing
else touched.

### Phase 1 — Backlog intake

Added `"infra"` to `VALID_AREAS` in `backlog/engine.py` (needed by
`llm-cost-ceiling` and `unattended-ops` — `execution` already means trade
execution, not pipeline operations, so a new area was warranted rather than
overloading an existing one). Appended 11 new items to `backlog/backlog.json`
(30 → 41), bumped `updated`, regenerated `BACKLOG.md`. `python -m
backlog.engine status` reports zero validation errors. Every supplied
`status` value matched what `determine_status()` independently computes
(precedence: `blocked` > `locked` > `depends_on` wins over a `trigger` even
when the trigger itself would resolve to `locked` — e.g.
`multi-sample-scoring` has a `resolved_count>=25` trigger but also
`depends_on`, so it lands on `blocked`, matching the item as authored) — no
mismatches to report, no status hand-edited.

New items: `market-baseline-brier`, `preregistration`, `llm-cost-ceiling`,
`replay-settled-fetcher` (ready); `replay-asof-reconstruction`,
`replay-runner`, `replay-instrument-validation`, `price-blind-arm`,
`methodology-writeup`, `multi-sample-scoring` (blocked, pending
dependencies); `unattended-ops` (ready). 5 ready / 6 blocked among the new
items, exactly as the handoff predicted. These items chart a path from the
current single-pass, price-anchored scorer toward a validated backtesting
and replay pipeline — most are gated behind `market-baseline-brier` and
`llm-cost-ceiling` specifically because a replay corpus scored at volume
needs a cost cap, and any of it needs an honest baseline before "edge" means
anything.

### Phase 2 — `market-baseline-brier` (only item implemented; everything
else above stays untouched per the handoff's explicit scope)

**The problem:** `core/scorer.py:649` injects the current market price into
every scoring prompt, and `core/scorer.py:245-253` (the ANCHORING GUARD)
explicitly instructs the model to move its estimate toward that price absent
strong contrary evidence. This is intentional and reasonable scorer design —
but it means the existing scorer Brier score (`get_brier_score()`) cannot by
itself distinguish "the scorer found real edge" from "the scorer echoed the
price back close enough to look calibrated." A baseline that scores the raw
market price with the identical formula, over the identical row population,
is the only way to tell those apart.

**What shipped**, read-only against signal generation (no scorer prompt,
threshold, or pipeline code touched):

- `market_baseline_brier REAL` — new additive column on `signals`
  (`core/logger.py`, existing idempotent `_add_col` migration pattern).
- `_market_baseline_brier(market_price, direction, outcome)` — shared helper,
  `(market_price - outcome_binary)^2`, `outcome_binary` derived the same way
  `get_brier_score()` already derives it from `direction`+`result` (so the
  two scores are apples-to-apples over the same rows). Returns `None` —
  never `0.5` — when `market_price` is missing; a market baseline test
  proved this isn't hypothetical (one of the 11 real resolved rows,
  `fcd6dbc8`, genuinely has no logged `market_price`).
- `resolve_outcomes()` now computes and persists this column at the same
  UPDATE that already writes `outcome`/`result`/`pnl_if_traded` — the natural
  (only) point where a row's real outcome becomes known. This is a
  resolution/settlement-path change, not a signal-generation change.
- `backfill_market_baseline_brier()` — idempotent one-off backfill for rows
  resolved before this column existed (only touches rows still `NULL`, skips
  rows with no `market_price`). Run once against the real DB: 10 of 11
  resolved rows backfilled, the 11th correctly left `NULL`.
- `get_market_baseline_brier_score()` — aggregate, mirrors `get_brier_score()`
  exactly (same `_PAPER` filter excluding `real_fill`/`research_probe`, same
  EXCELLENT/GOOD/FAIR/POOR labels), substituting `market_price` for
  `our_estimate` and adding the "exclude missing price" filter.
- `analysis/calibration.py` — prints Market Baseline Brier directly below the
  existing scorer Brier line, plus a verdict line (`scorer beats` /
  `scorer WORSE than` / `scorer ~=` the baseline) so the comparison is
  explicit, not left for a reader to compute by hand.
- `core/export_to_csv.py` (the resolved-signal export) — `market_baseline_brier`
  added to `WHITELIST` (persisted column, passes through as-is; blank, not
  `"0.5"`, when `NULL`); `scorer_brier` added as a new computed column
  (mirrors `is_win`'s pattern — computed at export time from `our_estimate`,
  blank when unresolved or `our_estimate` missing) so both numbers sit next
  to each other per row, not just in the aggregate.

**Real finding on the current n=8 paper population** (after backfill):
scorer Brier = 0.0578 (EXCELLENT by the 0-0.25 scale) vs market-baseline
Brier = 0.0022 (also EXCELLENT, but ~26x lower) — the scorer is currently
**worse** than just using the market price directly. At n=8 this is far too
small to be conclusive, but it's exactly the failure mode this metric was
built to catch, and it's now visible instead of hidden inside a
misleadingly-good-looking scorer Brier number.

**Tests:** `tests/test_logger.py` (+17: pending/perfect/random/excludes-null-
price/excludes-probe-rows for the aggregate, helper unit tests, resolve_outcomes
persistence including the missing-price case, backfill fill/skip/idempotent/
unresolved-rows-untouched), new `tests/test_calibration.py` (+6: line
presence, PENDING cases, both verdict directions, both scores shown
together), `tests/test_export_to_csv.py` (+6 dedicated Brier-column tests,
+1 existing computed-columns-list extended for `scorer_brier`). One
pre-existing test (`tests/test_backlog.py`, hardcoded item count) updated
from 30 to 41 to reflect Phase 1's intake — a direct, correct consequence of
adding items, not a regression. Full suite: **1672 passed, 1 skipped**
(unchanged network-gated test from an earlier goal).

### Top 3 next steps

1. `preregistration` (also `ready`, priority 1) — write
   `docs/PREREGISTRATION.md` now, before more resolved data accumulates,
   per its own append-only/pre-commitment discipline.
2. Re-run this comparison once n is large enough to matter (the
   `brier-tracking` gate itself sits at `resolved_count>=25`) — 8 resolved
   paper signals is not enough to act on the scorer-vs-baseline gap yet,
   only enough to know the instrument is now watching for it.
3. `llm-cost-ceiling` (ready, priority 2) — prerequisite for `replay-runner`
   and `price-blind-arm`, both blocked on it; next logical item to unblock
   the backtesting chain.

---

## 2026-07-23 — Kalshi Market-Link Pattern Confirmed (supersedes 2026-07-22 finding)

**Trigger:** after the HTML email render shipped (entry below), the user
reported the "Trade on Kalshi" links weren't rendering as real hyperlinks.
That was expected per the 2026-07-22 investigation's conclusion (no
confirmed URL pattern, so `kalshi_market_url` always returned `None`) — but
the user asked to look into it again rather than accept that as final. This
entry corrects that finding: a real pattern IS confirmed, via evidence the
2026-07-22 pass didn't have.

### What changed since 2026-07-22

The earlier investigation only ever tested URLs *we constructed and
requested*, and correctly found that meaningless — kalshi.com's
`/markets/[...slug]` route is a Next.js client-rendered catch-all that
returns HTTP 200 for literally any path, real or fabricated (146-byte body
spread, identical headers). No amount of additional guessing against that
endpoint would have changed the answer.

This pass instead looked for **Kalshi-originated** confirmation instead of
testing our own guesses, and found three independent sources agreeing on
the same shape:

1. **`https://kalshi.com/AGENTS.md`** — Kalshi's own documentation written
   for AI agents states the market-page URL shape directly.
2. **`sitemap-markets.xml`** (Kalshi's own crawled sitemap) — independently
   shows the same `markets/{series_ticker}/{event_ticker}` structure
   (optionally with a cosmetic title-slug inserted in the middle).
3. **A genuine server-side redirect**, unlike the client-rendered catch-all:
   `https://kalshi.com/events/{event_ticker}` issues a real 308 redirect
   chain, and — critically — it behaves *differently* for real vs. fake
   tickers. Live test output (`pytest tests/test_kalshi_url.py --network -v -s`):
   a real ticker's redirect chain resolves into `markets/{series}/{event}`
   matching its known series; a fabricated ticker does not resolve the same
   way. This is the first genuinely distinguishing signal found across both
   investigations — the `/events/` endpoint does real server-side lookup,
   unlike `/markets/` which never rejects anything.

**Confirmed pattern:** `https://kalshi.com/markets/{series_ticker}/{event_ticker}`
(both lowercased). Requires `series_ticker`, which — unlike `event_ticker` —
lives only on the **event** object returned by `fetch_events()`, never on a
raw market object. `main.py`'s event-fetch loop now captures it per event
and attaches it to every market dict from that event
(`m["series_ticker"] = series_ticker`), the same way `event_ticker` already
flows through. Threaded end to end: `main.py` (both signal-construction
sites) → `analysis/resolve_first.py:log_selected` → `core/logger.py` schema
(`series_ticker TEXT DEFAULT ''`, additive `_add_col` migration, new
`log_signal` column) → `core/kalshi.kalshi_market_url(series_ticker,
event_ticker)` (signature changed, now returns a real URL instead of always
`None`) → `core/report.py` (`_rank_top_picks`, `_betting_queue_data`'s SQL
SELECT, both `_kalshi_link_or_bare` call sites, `_synthetic_dry_run_signals`)
so both the text and HTML renderers pick it up automatically via the
existing shared-computation functions from the prior goal — zero divergence
risk, no new code path duplicated between renderers.

**Known, accepted gap:** `sitemap-markets.xml` has ~0/14 coverage of this
project's actual tracked markets (low-liquidity/niche), so it cannot serve
as a live per-ticker verification lookup for real use. The implementation
trusts the confirmed *format* (backed by the 3-source evidence trail above)
rather than verifying each individual ticker resolves — genuinely
unverifiable per-ticker via plain HTTP given the client-render issue that
still holds. Documented directly in `kalshi_market_url`'s docstring so this
tradeoff isn't lost. Rows logged before this change have `series_ticker=''`
and correctly render as bare ticker text, never a broken link.

**Tests:** rewrote `tests/test_kalshi_url.py` (the old file asserted "always
returns None" — true before, false now) and the Kalshi-link section of
`tests/test_report_html.py` (5 cases: real-unmocked positive, mocked
resolver, missing event_ticker, missing series_ticker, and an upgraded
404-regression-guard that also confirms the correct link shape appears).
Added 4 `series_ticker` schema/migration/round-trip tests to
`tests/test_logger.py` mirroring the existing `event_ticker` tests. Updated
the throwaway `signals` schema helpers in `tests/test_4c.py`, `test_4d.py`,
and `test_report.py` to add the `series_ticker` column (same fix pattern as
`event_ticker` before it). Full suite: 1648 passed, 1 skipped by default
(the network-gated live test, run explicitly with `--network`).

### No number changed

Same as the prior goal: this is presentation/linking only. No scoring,
threshold, filter, or config value changed anywhere.

### Top 3 next steps

1. Do the HUMAN TESTING CHECKLIST item 5 from the 2026-07-23 HTML-report
   entry below — click a real link in a live-sent email and confirm it
   resolves to the actual market page, not a 404.
2. Backfill `series_ticker` for historical rows only if a concrete use case
   needs it (mirrors the same open item for `event_ticker`) — not required
   for new signals, which capture it going forward.
3. If `sitemap-markets.xml` ever gains coverage of this project's tracked
   markets, it could become a genuine per-ticker verification source rather
   than just format confirmation — revisit if that changes.

---

## 2026-07-23 — Email-Safe HTML Report (multipart/alternative)

**Goal:** the daily report email was plain monospace text with weak hierarchy and
mid-word truncation (a real rendered line hit 111 chars). Render it as an
email-safe HTML body matching a pre-built, signed-off design
(`leviathan_report_email_v2.html` — dark theme, table-based, inline CSS, 600px
container, Kalshi links, Track Record intentionally excluded since it lives in
Power BI) and send `multipart/alternative` (HTML primary, existing text as
fallback). **Presentation-layer only** — no computed value, threshold, or
scoring changed anywhere; the text and HTML bodies of one email render from
the exact same computed numbers, by construction, not by convention.

### PART A — data/render separation (the load-bearing part, above styling)

Chose **share the already-computed values**, not a full report-model refactor
(structured section objects). Reasoning: `compile_report` builds most of its
output as inline strings interleaved with computation, and a full refactor
would have touched every section (Signal Block, Short-Term Watchlist, Smart
Money, Run Statistics, Track Record) — sections the HTML email doesn't even
render. Extracting a full model for sections that stay text-only forever
would be scope creep; sharing computation only where BOTH renderers actually
need the same numbers is the targeted fix.

Extracted three shared, pure computation functions — `compile_report` was
refactored to call them too (not just `render_html`), so this is provably
shared, not merely duplicated with good intentions:
- `_rank_top_picks(signals, n=3)` — ranking + every per-pick stat (Market/Est/
  Edge/EV/Kelly, confidence, flag, strength, close date, repeat label).
- `_betting_queue_data(db_path, top_n, config)` — the ONE SQL query, EV-floor
  filter, and urgency sort. Both renderers call this single query; there is
  no second query path that could silently diverge.
- `_header_data(signals, whale_only, run_meta, config, ...)` — New/Repeat/
  Whale counts and next-resolution date (with its date-parsing try/except
  written once, not copy-pasted).
- `now_utc` is now an optional param on both `compile_report` and
  `render_html` (default: fresh `datetime.now()`, preserving existing
  behavior/tests exactly) so a caller can pass one shared timestamp and
  guarantee the header date/time can't differ between the two bodies by even
  a few seconds. `main.py`'s real send site does this.

Verified zero divergence risk end-to-end in tests (`tests/test_report_html.py`):
edge value, header counts, and betting-queue contents from a real SQLite DB
are asserted present and IDENTICAL in both bodies for the same input.

### PART B/C — HTML renderer + Kalshi links

`render_html(...)` mirrors `compile_report`'s full signature. Sections, in
v2's order: header status readout, summary strip (New/Repeat/Whale/Smart-
Money/Next-Resolution/Model), up to 3 TOP PICKS cards, BETTING QUEUE table
(up to 5 rows) with a filtered-count footer line, and a run-stats footer. No
Track Record. All dynamic text (titles, tickers) is HTML-escaped
(`html.escape`) — verified against a real title containing an apostrophe
(Trump's Cabinet) rendering correctly as `&#x27;`.

Kalshi links reuse goal_1's `core.kalshi.kalshi_market_url` as the single
source of truth — the report layer never constructs a URL itself. Since that
helper currently always returns `None` (no confirmed URL pattern — see the
2026-07-22 entry above), every pick and queue row in the live HTML renders as
plain ticker text with no `<a>` tag right now; the link markup exists and is
tested (with a mocked resolver) so it activates automatically the day
`kalshi_market_url` gets a real pattern, with zero code changes here.

**Known cosmetic divergence, deliberately not fixed:** EV/Kelly dollar
formatting inherited from the shared value renders as `$+7.33` (dollar
before sign) rather than v2's `+$7.33` (sign before dollar). This is the
exact same shared number, not a different one — reformatting it only for
HTML would mean a second formatting path that could drift from the text
renderer's, which is precisely the risk PART A exists to eliminate. Flagged
here rather than silently "fixed" with a parallel formatter.

Size check: a real 3-pick render is ~19–28KB depending on content — well
under Gmail's ~102KB clip threshold with no trimming needed.

### PART D — multipart send

`send_report(..., html_body=None)`: omitted (existing default), sends exactly
as before — every existing caller (weekly digest) is provably unaffected.
Provided, sends `multipart/alternative` (text/plain fallback + text/html
primary). Subject and recipient logic untouched either way.

`python -m core.report --dry-run [--output path.html]` renders both bodies
from one shared `now_utc`, writes the HTML to a file, prints both bodies plus
a "SHARED VALUES CHECK" section, and makes no SMTP call — this is how a human
(or a test) verifies output without `GMAIL_APP_PASSWORD`.

**Wired into `main.py`'s real daily-report send** (not listed in the goal's
literal scope line, which named only `core/report.py` — corrected here the
same way goal_1's scope line was corrected after tracing the actual signal-
construction site: without this the feature would be fully built and tested
but never actually fire in the real daily email). `render_html` is wrapped in
its own try/except separate from `compile_report`'s — an HTML rendering bug
degrades to a text-only send rather than blocking the whole daily report.

15 new tests (`tests/test_report_html.py`): shared-value assertions, Kalshi
href present/absent, multipart structure with both parts present and the
text part non-empty, Track Record absence guard (and a companion test
proving it's still present in text), dry-run file write + no-SMTP guard. All
existing `tests/test_report.py` / `test_4c.py` / `test_4d.py` tests pass
unchanged (their DB schema helpers were extended with an `event_ticker`
column to match the real schema — no test logic changed). Full suite: 1641
passed, 1 skipped (the network-gated Kalshi URL test from goal_1).

### HUMAN TESTING CHECKLIST (code cannot verify this)

Send the real report to yourself (`python main.py`, or point `--dry-run`'s
output at a real send) and confirm in each client:

1. **Gmail web** — dark background is not force-inverted by Gmail's own dark-
   mode color adjustment; rounded corners and borders on cards/tiles survive;
   IBM Plex Mono loads (or degrades cleanly to the monospace fallback stack).
2. **Apple Mail / iOS Mail** — same dark-background and corner-radius checks;
   confirm the hidden preheader text is the one that shows in the inbox
   preview line, not stray leftover markup.
3. **Accenture Outlook** — Outlook's rendering engine (Word-based on desktop)
   is the strictest target; confirm the table layout doesn't collapse, the
   MSO conditional comment doesn't leak visible text, and colors aren't
   flattened to default black/white.
4. **Plain-text fallback** — open the email in a text-only view (or check the
   raw MIME source) and confirm the text/plain part is the familiar existing
   report, complete and readable on its own.
5. **Kalshi links** — once `kalshi_market_url` ever returns a real pattern,
   click through and confirm it resolves to the actual market page, not a
   404 or the homepage (the same check that failed for the naive ticker-only
   form in the 2026-07-22 investigation).

### No number changed

Every figure in the HTML — New/Repeat/Whale counts, Market/Est/Edge/EV/Kelly,
betting-queue rows, run stats — is read from the exact same shared
computation the text renderer already used before this goal. No scoring,
threshold, filter, or config value changed.

### Top 3 next steps

1. Confirm the dark theme survives the three real clients above (checklist
   items 1–3); fall back to a light theme if any of them force-invert or
   flatten colors badly enough to hurt readability.
2. Reconcile the "resolved" scoping label between the email (paper-only,
   currently n=8) and Power BI (all sources, n=11) so the two public-facing
   surfaces don't quietly contradict each other.
3. Do the full report-model refactor (structured section objects → text
   renderer + HTML renderer) if text/HTML duplication starts to drift as more
   sections get added to either surface — not needed yet; the three shared
   functions cover every value both renderers currently show.

---

## 2026-07-22 — Kalshi Event-Ticker Capture + Market-Link Investigation

**Goal:** the signals table stored no link to the underlying Kalshi market —
only a bare `ticker`. `event_ticker` is a native field on Kalshi's own raw
market JSON (confirmed via live fetch — present on every market object
returned by `/markets`), already read by the scanner's dedup functions
(`core/scanner.py:126`, `:173`) but never persisted. This was a "surface a
field that's already fetched but discarded" data fix — no scoring, edge,
threshold, or filter changed — plus one genuinely new step: empirically
confirming the real kalshi.com market-page URL pattern, since the naive
`kalshi.com/markets/{market_ticker}` form is confirmed to 404.

**PART A trace (stated before writing code):** the signal dict not built in
`core/scanner.py` as the goal's scope line assumed — traced to **`main.py`**,
in two places: first-pass construction at `main.py:625-654` and second-pass
(low-confidence widen) at `main.py:723-753`, both `signal = {**cs, ...}`
inside a loop over `flagged_markets` (the market dict, `m`, still has
`event_ticker` in scope there — it's additive through the whole pipeline).
Neither block copied `m.get("event_ticker")` into `signal`; that's the drop
point. A third site, `analysis/resolve_first.py:170` (`log_selected`), builds
its own signal dict from the same kind of market object with the identical
gap. All three now thread `event_ticker` through.

### Confirmed finding: NO URL pattern reliably resolves to a real market page

Per PART C.5's explicit instruction, this is a STOP: `core.kalshi.kalshi_market_url()`
always returns `None` — no link is shipped.

Investigation (2026-07-22, live Kalshi + kalshi.com):
1. Neither the Kalshi market object nor the event object exposes a slug or
   canonical-URL field (event object fields: `available_on_brokers`,
   `category`, `collateral_return_type`, `event_ticker`, `last_updated_ts`,
   `mutually_exclusive`, `series_ticker`, `settlement_sources`, `strike_date`,
   `strike_period`, `sub_title`, `title` — no URL/slug anywhere).
2. `https://kalshi.com/markets/{event_ticker}` returns **HTTP 200, no
   redirect to the homepage** for real markets — passing the narrow, literal
   proof bar. But: constructing the identical URL for a **fabricated**
   ticker (`ZZZZNOTAREALTICKER99999`) returns the **same** 200, no redirect,
   near-identical (146-byte spread out of ~148KB) HTML body, and identical
   response headers (`X-Matched-Path: /markets/[...slug]` — a Next.js
   catch-all route matching literally any path). kalshi.com's market pages
   are a client-rendered SPA; the actual market data (and any "not found"
   state) loads via client-side JS after the initial HTML paint, which a
   plain HTTP request cannot see. Status code and redirect target give
   **zero signal** distinguishing a real market from a made-up one.
3. Live output (`pytest tests/test_kalshi_url.py --network -v -s`):
   ```
   REAL  KXBAA-28JANDELIV               status=200 final=https://kalshi.com/markets/kxbaa-28jandeliv redirected_home=False body_len=147876
   REAL  KXISRNORMCOUNT-27DEC31         status=200 final=https://kalshi.com/markets/kxisrnormcount-27dec31 redirected_home=False body_len=147888
   FAKE  ZZZZNOTAREALTICKER99999        status=200 final=https://kalshi.com/markets/zzzznotarealticker99999 redirected_home=False body_len=148022
   Body length spread real-vs-fake: 146 bytes (near-identical HTML regardless of ticker validity)
   ```
4. A Next-router RSC-header request (attempting to hit the same JSON data
   endpoint the site's own client uses for hydration) was also tried as a
   non-browser way to check page identity — returned an empty body for both
   real and fake tickers, inconclusive. No headless browser was available in
   this environment to render and inspect actual client-side content.

**No threshold, sample size, scoring, or gate was changed.** `event_ticker`
was already fetched at scan time (native Kalshi API field) and is merely
persisted now. All rows written before this change (156 existing rows as of
the goal's writing) fall back to the bare ticker with no href until
re-scanned — `event_ticker` defaults to `''` via the existing idempotent
`_add_col` migration pattern, and `kalshi_market_url` returns falsy for any
empty/None/unresolvable input, so no dead link or `href=""` is ever emitted.

`core/logger.py`'s separate `log_pass` INSERT (PASS-direction rows) was
intentionally left untouched — it wasn't named in the goal's scope
(only `log_signal` was), so PASS rows get the column's default `''` rather
than a captured value; a future goal can extend this if a use case needs it.

**Live pipeline verification:** a real `python main.py` run (2026-07-22) found
0 new signals this run (1 repeat, correctly not re-logged — the existing
7-day dedup skips `log_signal` entirely for repeats), so it didn't produce a
fresh non-PASS row to inspect directly. Verified the actual wiring instead by
replicating the exact `main.py:625-627` signal-construction line against a
real market fetched live from Kalshi (`KXMVESPORTSMULTIGAMEEXTENDED-...`) and
confirming its `event_ticker` persists through `logger.log_signal` unchanged
— end-to-end with real data, independent of whether today's scan happened to
produce a new signal.

10 new tests (schema/migration, `log_signal` round-trip, `kalshi_market_url`
behavior including a regression guard against ever reintroducing the
confirmed-404 form, and one live `@pytest.mark.network` integration test —
skipped by default so `pytest -q` stays fully offline, run explicitly with
`--network`). Full suite: 1626 passed + 1 skipped by default (1627 passed
with `--network`).

### Top 3 next steps

1. The email-render goal can now consume `event_ticker` (it's on every new
   signal row) — but there is currently nothing to link to; that goal should
   either render the bare ticker only, or wait on next step 3.
2. Backfill `event_ticker` for historical rows only if a concrete use case
   needs it — not required for new signals, which capture it going forward.
3. If a market link is still wanted, the honest next step is what
   `sources/accounts.py:112` already does for Polymarket: capture a slug (or
   whatever field Kalshi's site itself uses to resolve pages client-side) at
   scan time, directly from a source that's actually authoritative about
   page identity — not derive one from the ticker and hope. This would
   likely require inspecting kalshi.com's own client-side API calls (browser
   devtools / a headless browser), which wasn't available in this
   environment.

---

## 2026-07-18 — Gate Unlock Notifier

**Goal:** a bounded, deterministic notifier — not an agent. It forms no opinions,
changes no threshold, and takes no action beyond sending one batched email when
a BACKLOG.md gate transitions locked/unknown -> unlocked. Reuses
`core.report.send_report` as-is; computes no new metric (a gate whose metric
isn't already computed by an existing `core/logger.py` function is classified
"not yet measurable," full stop).

Added `scripts/gate_notifier.py` (parser + known-metric registry + fire-once
state machine + email composition, all in one file — matches the existing
`scripts/position_reconciliation.py` precedent of testing logic directly out
of a `scripts/` module rather than splitting a separate library module) and
`scripts/setup_gate_notifier_scheduler.ps1`. State persists in the git-ignored
`data/gate_state.json`.

**Gate parsing:** a fixed grammar (`METRIC OP NUMBER`, regex-based — no
`eval()`/`exec()` on anything pulled from the markdown) with a known-metric
registry. A Locked-table row whose Gate cell doesn't match the grammar fails
the run loudly (`GateParseError`, non-zero exit), rather than being silently
dropped.

**Dependency gates (Blocked table, PART A.5):** deferred from v1. Several
Blocked rows depend on multiple comma-separated IDs (e.g.
`sample-size-gates, brier-tracking`), which needs AND-logic across each ID's
Done-table membership — real complexity beyond this notifier's core
single-metric-gate pattern. v1 reports Blocked rows as "dependency-tracked
(not evaluated in v1)" rather than half-building evaluation for them.

### Gate snapshot at build time (2026-07-18, live DB)

| Gate ID | Status | Metric | Value | Threshold |
|---|---|---|---:|---|
| brier-tracking | locked | resolved_count | 8 | >= 25 |
| confluence-detection | locked | resolved_count | 8 | >= 25 |
| per-heuristic-scorecard | locked | resolved_count_per_category_max | 7 | >= 15 |
| per-wallet-track-record | **not yet measurable** | resolved_count_per_wallet_max | — | >= 10 |
| calibration-curve | locked | resolved_count | 8 | >= 50 |
| edge-decay-analysis | locked | resolved_count | 8 | >= 30 |
| heuristic-sunsetting | locked | resolved_count_per_category_max | 7 | >= 15 |
| skill-vs-luck-weighting | **not yet measurable** | resolved_count_per_wallet_max | — | >= 10 |
| slippage-tracking | locked | fills_count | 7 | >= 20 |

**No threshold, sample size, or gate was changed by building this.** Every row
above is the existing BACKLOG.md gate, evaluated as configured.
`resolved_count_per_wallet_max` is intentionally not-measurable — no
`core/logger.py` function computes per-wallet resolved counts today
(`per-wallet-track-record`, the item that would ship one, is itself locked) —
and it will stay that way, correctly, until that ships.

Moved `gate-unlock-notifier` to Done in BACKLOG.md (it didn't previously
exist as a Ready item; added and completed in the same pass).

24 new tests (grammar parsing, malformed-row loud failure, metric mapping,
UNKNOWN-never-fires, fire-once across two runs, unknown->unlocked transition
via a simulated registry update, send-failure state-rollback safety,
dry-run repeatability). Full suite: 1614 passed, 13 subtests passed.

### Top 3 next steps

1. Once `per-wallet-track-record` ships a per-wallet resolved-count function,
   add its key to `KNOWN_METRICS` in `scripts/gate_notifier.py` — the next run
   after that will pick up `resolved_count_per_wallet_max` automatically
   (unknown -> unlocked is a valid notifying transition, proven by test).
   Confirm this path when that item ships.
2. Decide whether dependency gates (Blocked table) are worth wiring given they
   were deferred in v1 — needs AND-logic across comma-separated Done-table IDs.
3. Keep gate-parsing tolerant of BACKLOG.md format edits, or move gates to a
   small structured gates source that BACKLOG.md renders from, if the markdown
   parse proves brittle over time.

---

## 2026-07-18 — Smart-Money Discovery Funnel Diagnostic

**Goal:** instrumentation only — figure out *why* the winning-trader discovery gate
(`sources/accounts.py: discover_winners -> _score_wallet -> _is_winner`) has
promoted zero wallets across multiple runs, without changing any threshold,
sample size, or gate. Added `sources.accounts.diagnose_discovery()` /
`format_diagnostic_report()` and `scripts/diagnose_discovery.py`. Extracted a
shared `_classify_wallet(stats, config) -> str` (first failing gate name, or
`"PASS"`) that both `_is_winner` and the diagnostic call, so the two can never
silently disagree — proven by a 13-case regression battery over
`_is_winner`'s current boundary behavior.

### Real run (2026-07-18, live Polymarket API, sample=1000 recent trades)

| Stage | Survivors | % of prior |
|---|---:|---:|
| 0. trades fetched | 1000 | — |
| 1. unique wallets | 524 | 52.4% |
| 2. positions returned | 495 | 94.5% |
| 3. scored | 495 | 100.0% |
| 4. resolved_count >= 1 | 166 | 33.5% |
| 5. gate resolved_count>=min (10) | 97 | 58.4% |
| 6. gate win_rate>=min (55.0) | 0 | 0.0% |
| 7. gate position_count>=min (5) | 0 | — |
| 8. gate pct_pnl>=min (10.0) | 0 | — |
| 9. gate cash_pnl>=min (100.0) == WINNERS | 0 | — |

**Biggest single drop-off, stated bluntly:** two-thirds of scored wallets (329/495,
66.5%) never reach `resolved_count >= 1` at all — their only visible positions are
open, or resolved-but-coinflip/sports (excluded from scoring by design). Of the
minority that clear that bar, another 41.6% die at `resolved_count>=10`. But the
single most dramatic number in this run is stage 6: **100% of the 97 wallets that
cleared `resolved_count>=10` still failed `win_rate>=55%` — every one of them.**

**Distribution at the gate where the mass dies:**
`resolved_count` among all 495 scored wallets — min 0, **median 0**, p90 48.6, max
489. Only 19.6% of scored wallets ever reach the resolved_count>=10 bar. Most
sampled wallets simply don't have enough visible resolved (non-coinflip/sports)
history to be evaluated on skill at all.

`win_rate` among the 97 wallets that reached that gate — min 0.00%, median 0.00%,
p90 0.00%, max 0.00%. Zero variance across 97 independent wallets is itself a
finding: manually inspecting six of these wallets' resolved positions (not a code
change, a diagnostic spot-check) found every one of them dominated by systematic
long-shot bucket bets — "will player X be top scorer" style questions where a
wallet buys the YES side of many mutually-exclusive single-outcome contracts
(election-candidate lists, chess/esports tournament-winner lists, exact-score
buckets, temperature/tweet-count range buckets). Percent PnL on these clusters
tightly around -100% by construction — only one bucket in a large N-way partition
can resolve YES — and this bet pattern is not currently caught by
`_is_coinflip`/`_is_sports_title`.

### Verdict (world we are in, not a fix)

**Both mechanisms are active, compounding in sequence.** The dominant failure is
sample mis-specification (world (a)): recent-trade sampling pulls in wallets with
too little visible resolved history to evaluate at all — median resolved_count
across every scored wallet is literally zero, and 80%+ never reach the
resolved_count>=10 bar. But the wallets that *do* clear that bar are not a random
subset of "experienced traders" — they disproportionately got there by placing a
high volume of structurally-near-guaranteed-loss long-shot bets across large
partitioned markets, a bet style the resolved_count gate rewards (it counts
resolved positions, not skill) but that is unrelated to forecasting skill and
happens not to be filtered the way coinflip/sports markets already are. Zero
winners in this run is not strong evidence that skill is rare in the broader
trading population (world (b), as originally framed) — it is better read as
"recent-trade sampling, once past the resolved-count floor, currently surfaces a
long-shot-bucket-betting subpopulation whose win rate is artificially near zero
by construction." Whether skill is *also* genuinely rare in the population this
sampling method misses is not answered by this run.

**No threshold, sample size, or gate was changed.** This run only measured the
existing gate as configured (`min_resolved_count=10`, `min_win_rate=55.0`,
`min_positions=5`, `min_pct_pnl=10.0`, `min_cash_pnl=100.0`).

### Top 3 next steps (decisions this unblocks, not taken here)

1. **Sourcing decision:** evaluate drawing candidate wallets from a
   resolved-history/leaderboard-style endpoint instead of (or in addition to)
   recent-trades sampling, vs. accepting the current recent-trades gate as-is
   knowing it structurally favors high-volume long-shot bettors past the
   resolved_count floor.
2. **Filtering decision (only after (1)):** whether the long-shot single-outcome
   large-N-partition bet pattern observed above is common enough across the
   broader wallet population to warrant its own exclusion category (alongside
   `_is_coinflip` / `_is_sports_title`) — not decided here; this run doesn't
   establish prevalence outside the 97-wallet spot-check.
3. Only after a non-empty verified winners list exists from either decision above:
   design smart-money-as-a-tagged-input to the scorer (input, never trigger —
   every signal records whether smart money touched it, so whale-confirmed vs.
   model-only can be compared at resolution). Separately: revisit whether this
   track is worth further investment at all if a corrected sourcing/filtering
   pass still yields zero or near-zero winners.

No threshold is recommended for adjustment — the numbers above point at sample
composition (who gets sampled, and what bet style survives resolved_count), not
at a mis-calibrated number.
