# Leviathan — Progress Log

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
