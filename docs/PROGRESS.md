# Leviathan — Progress Log

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
