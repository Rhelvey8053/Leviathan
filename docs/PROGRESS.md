# Leviathan — Progress Log

---

## Session 8 — 2026-06-17 (autonomous continuation)

### Cross-market force-flag + polymarket test suite

**Feature: CROSS_MARKET flag_path**

Implemented cross-market promotion — unflagged markets with a significant
Polymarket price divergence are now elevated into the scoring queue even if
no heuristic, drift, or edge signal fired.

Architecture:
- `polymarket.fetch_and_build_index(config)` — fetches once, returns shared index
- `polymarket.match_markets(markets, index, config, *, min_gap, min_match_score)` — matches any list
- `polymarket.enrich_flagged()` — now a thin wrapper over the above (backward compat)
- `main.py` step 3: collects `unflagged_markets` pool after scoring
- `main.py` step 4: fetches Polymarket index once, enriches flagged markets, then
  checks top-N unflagged by volume; promotes any with `abs(gap) ≥ cross_market_min_gap`
  into `flagged_markets` with `flag_path = "CROSS_MARKET"`

Config keys added to `polymarket` section:
- `cross_market_promote` (bool, default true)
- `cross_market_min_gap` (float, default 0.15)
- `cross_market_max_candidates` (int, default 50)

**New test file: `tests/test_polymarket.py` (24 tests)**
- `_yes_price`: yes/true/fallback/missing/pre-parsed
- `build_index`: valid, missing price, missing question, multiple
- `find_match`: exact, below threshold, empty index, picks best
- `match_markets`: match, no match, min_gap filter, min_gap passes,
  no mid_price, empty title, min_match_score override
- `fetch_and_build_index`: mock fetch call
- `enrich_flagged`: backward compat
- Cross-market promotion: gap passes, gap rejected

489 → 513 tests total.

### Git commits this session

1. `67e8a94` — PROGRESS.md + README housekeeping (489 count, Session 7 commit 9)
2. `6155573` — polymarket refactor + CROSS_MARKET promotion + 24 new tests (513)
3. `469604f` — CROSS_MARKET flag reason in scorer prompt + 2 scorer tests (515)
4. `fa3deb5` — 5 new heuristic categories + 13 tests: arrested, testimony, approval, strikes, awards (528)
5. `81a91cb` — Fix match_markets gap-floor bug: None-price excluded when gap floor > 0 (530)
6. `454305b` — cross_market_min_match_score guard (0.65) for loose-title false positives (531)
7. `2756d38` — Calibration rules 12-13: legislative (~35%) + price-level (50/50) (533)
8. `e70627b` — Recalibrate shutdown/debt-ceiling heuristics (shutdown avoid→0.85, begins→0.15; ceiling raise→0.70, generic→0.65) (537)
9. `fee5fad` — 5 new heuristics + 17 tests: reelection, diplomatic summits, earnings, stock indices, mortality (554)
10. `11f0980` — Calibration rules 14-16: earnings (50%), diplomatic summit (40%), reelection (52%) + probe sync (557)
11. `b7afbf5` — 4 new heuristics + 9 tests: Nobel (10%), UNSC (15%), SEC/FCC approval (40%), CEO appointment (35%) (566)
12. `83930be` — Calibration rules 17-18: corporate leadership (35%, 8-K evidence required) + UNSC (15%, veto risk) + probe sync (568)
13. `3919208` — 3 heuristic gap fills + 8 tests: Fed pause/hold (0.50), federal budget (0.40), 25th Amendment (0.05) (576)
14. `0ca995e` — Update PROGRESS.md with Session 8 commit list (commits 9-12)
15. `c0c6b89` — 3 more heuristic categories + 7 tests: Pulitzer (0.10), extradition (0.35), primary challenge (0.30) (583)
16. `68b9b11` — Major BR_NONE gap-fill pass: 10 new heuristic categories + 20 tests (599) — covers political withdrawal/ballot-disqualification year-injection bugs, divestiture, stock split, COVID variant, special election, constitutional amendment
17. `0e3cf64` — Signal strength composite indicator (★×N in report header when N≥2 corroborating signals) + 9 tests (608)
18. `2e4ed48` — Signal urgency markers (CLOSING IN Xd), repeat-count labels (REPEAT xN), strength-first sort in _qualifying() + 13 tests (621)
19. `aa70ad0` — Kelly criterion position sizing in report: full + 1/4 Kelly per signal, formulas for YES and NO + 8 tests (628)
20. `ab1dbe2` — 3 legal heuristic categories: pardon (35%), plea deal (45%), acquittal (35%) + 12 tests (640)
21. `95116cf` — HIGH confidence edge gate in main.py: auto-downgrade to MED when abs(edge) < 10pp + 2 tests (642)
22. `f03efba` — Quality-weighted pre-Claude sort: _pre_sort_score() replaces volume-only ordering in top-N selection
23. `ec49a89` — TOP PICKS executive summary: top-3 signals by (confidence, strength, edge) in compact 3-line format at report top + 8 tests (649)

## Session 9 begins here

---

## Session 7 — 2026-06-17 (autonomous continuation)

### Testing + heuristic quality pass

All work continued autonomously from Session 6. Focus: close coverage gaps in
test suite and tighten heuristic base rate accuracy.

**New tests added (383 → 489):**
- `test_logger.py` +11: `get_stats_by_sig()` (zero coverage before), `log_run`, `get_recent_tickers`, `get_week_signals`
- `test_report.py` +14: `_qualifying()` (PASS-direction exclusion, threshold, second_pass bypass, sort order)
- `test_report.py` +9: `_signal_block` additions (spread, whale, OB, watchlist, Polymarket, ext_markets, smart_money)
- `test_scorer.py` +24: liquidity context, CROSS-MARKET, POLYMARKET, WHALE ALERT, REVERSAL SIGNAL, ORDER BOOK, SPREAD SIGNAL, SMART MONEY, multi-market numbering, horizon notes, EDGE flag reason
- `test_report.py` +10: `compile_weekly_digest()` (header, empty, direction counts, dedup, stats, flag_path table)
- `test_report.py` +20: `compile_report()` (header, empty, new/repeat sections, whale, track record, probe stats, flag path, short-term watchlist, run stats, horizon grouping)
- `test_scanner.py` +25: new heuristic categories (fired/dismissed, govt shutdown, debt ceiling, CR/omnibus, antitrust, DPRK)

**New heuristic base rate categories in `scanner.py`:**
- Fired/dismissed (0.25) — before "resign" (0.20); requires word-bounded " fired " to avoid "misfired"
- Government shutdown (0.15) — government shutdown / partial shutdown / avert a shutdown
- Debt ceiling (0.15) — debt ceiling / debt limit / raise the debt ceiling
- Continuing resolution / omnibus (0.40) — placed BEFORE "signed into law" (0.35) to avoid ordering bug
- Antitrust/FTC/DOJ blocking (0.40)
- North Korea / DPRK (0.40) — placed BEFORE "nuclear deal" (0.20); uses " dprk " and explicit phrases

**False positive fixes:**
- "release" and "show" removed from entertainment catch-all — too broad (hit Fed minutes, data reports)
- "season" bare match removed — was hitting "wildfire season", "flu season", etc.
  Replaced with specific: "new season", "season premiere", "season finale", "season 2"..."season 9"
- "fired" bare match replaced with " fired ", "be fired", "get fired", etc. (avoids "misfired")

**Calibration:** all PROGRESS.md open items from Session 6 remain (Prison Break settlement, flag_path outcome data, KXMLBDEBUT-KANDERSON spread, cross-market force-flag architecture).

### Git commits this session

1. `92cb49c` — get_stats_by_sig, log_run, get_recent_tickers, get_week_signals tests (394)
2. `4768a1b` — _qualifying and _signal_block coverage in test_report (408)
3. `5969cf8` — build_prompt coverage for liquidity, ext_markets, poly, whale, OB, spread, smart money (432)
4. `61cc019` — compile_weekly_digest tests (442)
5. `cdae26f` — compile_report tests (457)
6. `b5872ce` — 6 new heuristic categories + 25 test cases, README 380→474
7. `14a67d4` — entertainment false positive fix (release/show)
8. `04f12bf` — fired substring fix, season catch-all fix
9. `5c3e1ed` — FOMC/unemployment/price/IPO/legislative/TikTok expansion (489 tests)

---

## Session 6 — 2026-06-17

### Signal coverage: flagged market count 9 → 15

Added new heuristic base rates and fixed several stale-threshold bugs across analysis scripts.

**`scanner.py` — new base rate patterns:**
- Cabinet departure: `"member of trump's cabinet"`, `"leave the cabinet"`, etc. → 0.65 (high historical turnover)
- Congressional control: `"control the senate"`, `"senate majority"`, `"flip the house"`, etc. → 0.50 (election cycle coin-flip)

**`analysis/filter_stats.py` — diagnostic improvements:**
- Added `--snapshot` flag: reads from `data/snapshots/` instead of live API (~3s vs ~90s)
- Added prompt-time signal counts: `vol_spike` (24h vol ≥20% of total) and `price_jump` (last vs prev ≥20%)
- Fixed drop-reason price calculation to use two-sided bid/ask logic (same as scanner.py)

**`report.py` + `main.py` — flag path surfacing:**
- Signal block header now shows `[HEURISTIC]` / `[DRIFT]` tag so recipients know why a market was flagged
- Fired signals list adds `"Heuristic Base Rate XX%"` when flag_path=HEURISTIC
- `main.py` passes `base_rate` through to the signal dict

**`analysis/drift_diagnosis.py` — correctness fixes:**
- Applied two-sided bid/ask fix (bid>0 AND ask>0) to mid-price calculation; was generating spurious drift for one-sided markets
- Part B `drift?` column and Part C baseline now read `drift_min_abs`/`drift_min_pct` from config instead of hardcoded 0.0/5%

**New tests:** `tests/test_report.py` (8 tests) + 7 new parametrized base rate cases in `test_scanner.py` → 339 total (was 324).

**`scorer.py` — calibration rules expanded:**
- Added Rule 5 (IPO announcement): base rate ~25% per 3-6 month window; "confidentially filed" / "banks hired" are NOT evidence of imminent announcement — only public S-1 filing counts
- Added Rule 6 (cabinet departure): base rate ~65% within first 20 months; market below 50% likely underpriced — weight historical turnover heavily
- Added Rule 7 (sports debut): base rate ~35% for unconfirmed prospects; only active roster assignment + confirmed start date counts as strong evidence
- Renumbered HIGH CONFIDENCE to Rule 9, EDGE REQUIREMENT to Rule 10

**`scorer.py` `build_prompt()` — FLAG REASON line:**
- Each market in the scored prompt now gets a line explaining why it was flagged:
  `FLAG REASON: HEURISTIC base rate mismatch 65% vs market price`
  `FLAG REASON: DRIFT — market price has moved significantly from last traded price`
  `FLAG REASON: WATCHLIST — top Polymarket traders have open positions on this market`

**`scorer.py` `build_prompt()` — SIGNAL CONFLICT detection:**
- When `drift_flag=True` AND `base_rate` is set AND the two signals point in opposite directions, appends:
  `SIGNAL CONFLICT: DRIFT suggests YES (mean revert) but BASE RATE (35%) suggests NO. Weight the base rate over drift for fundamental mispricing; use drift only as a secondary timing cue.`

**`analysis/research_probe.py`:**
- `PROBE_SYSTEM` synced with scorer.py rules 5 (IPO), 6 (cabinet), 7 (sports debut), 8 (HIGH CONFIDENCE), 9 (EDGE REQUIREMENT)

**`analysis/drift_diagnosis.py` + `analysis/flag_mode_compare.py`:**
- Both scripts now read `drift_min_abs`/`drift_min_pct` from config instead of hardcoded values
- `flag_mode_compare.py` report now shows dynamic thresholds instead of stale "86%" / "0.0/0.05" text

**`analysis/threshold_sweep.py`:**
- `classify_flag_path()` refactored to read `m.get("flag_path")` directly (was re-running edge logic from scratch)
- Removed stale `_edge_threshold` tagging loop

**New tests:** `tests/test_report.py` (8 tests) + 7 new base rate cases in `test_scanner.py` + 8 new scorer tests → 347 total (was 324). Session 6 further added 5 SIGNAL CONFLICT tests → 352 total.

### Git commits this session

1. `e625a1c` — Cabinet/senate base rates, filter_stats --snapshot, vol/price signals
2. `0b05818` — flag_path in report header, heuristic base rate in signals, test_report
3. `d08e304` — drift_diagnosis uses config thresholds and two-sided bid/ask fix
4. `3b45bb2` — scorer calibration rules 5-7 (IPO/cabinet/sports debut), FLAG REASON line in prompts, 8 scorer tests (347 total)
5. `4c680eb` — system prompt rule presence tests, test count to 347
6. `3571d91` — DRIFT/HEURISTIC signal conflict warning + 5 unit tests (352 total)

### Open items (carried forward)

1. **Prison Break market** (`KXMEDIARELEASEPRISONBREAK-30JAN01-26JUL01`) settles 2026-07-08 — run `resolve_outcomes` after that date.
2. **Flag_path outcome data** — 0 resolved paper signals. Once 10+ markets resolve, `get_stats_by_flag_path()` shows signal path win rates.
3. **KXMLBDEBUT-KANDERSON**: bid=0.55, ask=0.99, mid=0.77 — unusual 44-cent spread worth monitoring.
4. **Cross-market force-flag** — markets with significant Polymarket/Metaculus divergence but no heuristic or drift are not promoted. Requires architecture change: run ext_markets cross-ref on unflagged markets before flagging, or add a post-step-4 second-promotion pass.

---

## Session 5 — 2026-06-17

### Watchlist pipeline direction enrichment

Enriched the watchlist signal pipeline so Claude sees *which way* top Polymarket traders are positioned, not just that they have a position:

**`analysis/smart_money_scan.py`** — `save_signals_cache()` now writes a `ticker_details` dict alongside `kalshi_tickers`:
```json
"ticker_details": {
  "KXABRAHAMSA-29-JAN20": {
    "consensus_direction": "NO",
    "trader_count": 2,
    "total_position_val": 15000.0,
    "kalshi_title": "Will Saudi Arabia join BRICS..."
  }
}
```
Primary source is `grouped_signals`; falls back to flat `kalshi_signals` if grouping produced no data.

**`scanner.py`** — `tag_watchlist_overlap()` gained an optional `ticker_details` parameter. When provided, matching markets are annotated with `watchlist_direction`, `watchlist_position_val`, and `watchlist_trader_count`.

**`main.py`** — Extracts `_ticker_details` from the signals cache JSON and passes it to `tag_watchlist_overlap()`.

**`scorer.py`** — WATCHLIST SIGNAL prompt now surfaces trader count, combined $ position, and consensus direction:
```
WATCHLIST SIGNAL: 2 trader(s) ($15,000 combined) on Polymarket hold significant
open positions on a related market — pointing NO. These are top-20 traders by
monthly PnL — weight this signal; they have demonstrated edge over thousands of trades.
```

**Tests:** 5 new `tag_watchlist_overlap` tests for `ticker_details` parameter (293 total, all pass).

### Git commits this session

1. `70791c7` — Enrich watchlist pipeline with direction/value/trader context (293 tests)

### Open items

1. **Prison Break market** (`KXMEDIARELEASEPRISONBREAK-27JUL01`) settles 2026-07-08.
2. **Run a fresh daily scan** to verify entity contradiction guard eliminates the 3 FPs.
3. **Flag_path outcome data** — 0 resolved paper signals. Once 10+ markets resolve, `get_stats_by_flag_path()` will show signal path win rates.

---

## Goal 1b — pytest suite for logger and scanner

**Branch:** `tests-1b`
**Result:** 81 tests, 0 failures, exit 0

### Coverage

**logger.py — `resolve_outcomes`**
- All 5 payoff cases verified to the cent:
  - YES at 0.30, resolves YES → +0.70 ✓
  - YES at 0.30, resolves NO  → −0.30 ✓
  - NO  at 0.30, resolves NO  → +0.30 ✓
  - NO  at 0.30, resolves YES → −0.70 ✓
  - blank direction           →  0.00 ✓
- Confirmed reads `market_price`, not `edge` (separate test with `edge=0.25, market_price=0.30`; expects +0.70 not +0.75)
- Skips already-resolved rows — only unresolved rows trigger API calls
- Leaves rows untouched when Kalshi API returns unsettled result

**logger.py — `get_stats`**
- `win_rate` is `None` when `resolved == 0`
- Computes correctly with 2 WIN + 1 LOSS (66.7%)
- `log_signal` writes blank `outcome`/`result` → `resolved` count stays 0 until `resolve_outcomes` runs
- Round-trip test confirms blank-outcome convention is consistent between `log_signal`, `resolve_outcomes`, and `get_stats`

**scanner.py — `filter_markets`**
- Price bounds: mid < 0.05 or > 0.95 dropped; boundaries 0.05 and 0.95 kept
- Volume: below per-bucket minimum dropped; above global max dropped
- Close-time: no `close_time` field dropped; `expiration_time` fallback accepted; beyond 180d dropped
- Efficient-market keywords: CPI/Federal Reserve titles dropped

**scanner.py — `classify_time_horizon`**
- 15 boundary cases covering INTRADAY / WEEKLY / MONTHLY / QUARTERLY / LONG

**scanner.py — `score_market` flag logic**
- `base_rate is None` and `mid_price` set → `flag=True` (dominant real-world trigger)
- Edge > 0.08 → `flag=True`
- Edge small but drift > 5% → `flag=True`
- `mid_price is None` (no bid/ask) → `flag=False`

**Regression: per-$1 payoff convention**
- 4 × 7 = 28 parametrized cases covering YES-win, YES-loss, NO-win, NO-loss at prices 0.10–0.90
- Will break loudly if anyone changes notional size or flips the formula

### Bugs found during testing

None. All logic was already correct after Goal 1 remediation. The tests confirm the P&L fix is wired correctly end-to-end.

### Top 3 recommended next steps

1. **Add `test_whales.py`** — `detect_whale_activity` and `scan_all_markets` are completely untested. The whale flag feeds the second signal path in `main.py` and is never covered by any automated check.

2. **Add `test_report.py`** — `compile_report` and `send_report` have no tests. Email sending should be mocked; test that signal cards render correctly and that `[SECOND PASS — LOW CONVICTION]` is stamped on second-pass signals.

3. **Integration smoke test** — a single end-to-end test that mocks all external calls (Kalshi, Polymarket, Claude CLI) and runs `main.main()` to verify the pipeline completes without exception and logs at least one run row to the DB. This would catch wiring regressions that unit tests on individual modules can't see.

---

## Goal 1c — Threshold sweep over live Kalshi snapshot

**Branch:** `sweep-1c`
**Snapshot:** `data/snapshots/markets_20260616_043836.json` — 2,428 markets from 400 events (prod)
**Grid:** 27 combinations (edge × price bounds × volume multiplier)
**Full report:** `reports/threshold_sweep.md`

### Key finding

**100% flag rate across all 27 combinations.** Every market that survives `filter_markets` is immediately flagged by `score_market`. No threshold knob changes this.

Flag path breakdown at production settings (edge=0.08, price=[0.05,0.95], vol×1.0):

| Path | Count | Share |
|------|-------|-------|
| `BR_NONE` (base_rate=None fallback) | 14 | 67% |
| `EDGE` (heuristic base rate + edge > threshold) | 7 | 33% |
| `DRIFT` (order-book mid drift) | 0 | 0% |

The `BR_NONE` fallback fires on every market type the scanner has no heuristic for — which is most of them. The filter is the only real gate.

### Recommended config

**Stick with production thresholds.** Moving price bounds to [0.10, 0.90] halves the candidate count (21 → 11) but still flags everything. The candidate volume (21 markets) is already workable for Claude to score. The real improvement is in the flag logic: the `BR_NONE` catch-all should be replaced with a require-signal rule (only flag if `EDGE`, `DRIFT`, or `whale` fired — not just "has a price").

### Top 3 next steps

1. **Fix the BR_NONE catch-all** — require at least one real anomaly signal (edge > threshold, drift > 5%, or whale detected) for a market to be flagged. This turns `score_market` from a pass-through into actual pre-filtering, reducing Claude calls and improving signal quality.

2. **Expand heuristic base rates** — the current list covers ~8 market types (rain, elections, IPOs, etc.). Adding sports outcomes, crypto prices, and legislative events would convert more BR_NONE flags into EDGE flags with real edge estimates.

3. **Re-run sweep after flag logic change** — once BR_NONE is tightened, re-snapshot and re-sweep to confirm the flag rate drops and EDGE/DRIFT flags are the majority path.

---

## Goal 1d — Config-driven flag modes + comparison report

**Branch:** `flag-modes-1d`
**Snapshot:** `data/snapshots/markets_20260616_043836.json` — 2,428 markets (prod, same as 1c)
**Full report:** `reports/flag_modes.md`

### What changed

`scanner.score_market` now reads `config.markets.flag_mode` (default `"passthrough"`) to control the flag condition. Three modes:

| Mode | Flag trigger |
|------|-------------|
| `passthrough` | `raw_edge > threshold` OR `base_rate is None` OR `drift` (original behavior) |
| `strict_anomaly_only` | `drift_flag` only (whale applies post-hoc via main.py) |
| `strict_with_heuristic` | `drift_flag` OR `(base_rate is not None AND raw_edge > threshold)` |

Each scored market now returns `flag_path` (`EDGE` / `BR_NONE` / `DRIFT` / `HEURISTIC` / `None`) and `flag_mode`.

### Candidate counts at production thresholds

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 21 | 21 | 100.0% | 7 | 14 | 0 | 0 |
| `strict_anomaly_only` | 21 | 18 | 85.7% | 0 | 0 | 18 | 0 |
| `strict_with_heuristic` | 21 | 18 | 85.7% | 0 | 0 | 18 | 0 |

> **Note:** These are candidate volumes only. Signal correctness cannot be judged until markets resolve.

### Key finding

**Drift is far more active than Goal 1c suggested.** The threshold_sweep classifier checked `BR_NONE` before `DRIFT`, so drift markets were mis-classified as `BR_NONE` and DRIFT appeared as 0. With the corrected `flag_path` logic, 18 of 21 filtered markets have `drift_flag=True` — the order-book mid is >5% away from last traded price on 86% of candidates.

On this snapshot, `strict_anomaly_only` and `strict_with_heuristic` are equivalent (HEURISTIC=0 because no heuristic-matched market survived filter without also having drift). On a snapshot with more heuristic matches (rain forecasts, political markets with large price gaps), they would diverge.

### Recommendation

**Use `strict_with_heuristic`.** It removes the 14 BR_NONE catch-all flags while keeping markets where a heuristic estimate meaningfully disagrees with price. The config default remains `"passthrough"` — no silent behavior change until Reed switches.

### Tests

17 new tests added to `tests/test_scanner.py` (98 total, all pass). Key pins:
- BR_NONE-no-anomaly market does NOT flag under `strict_anomaly_only`
- Drift-only market flags under all three modes
- Heuristic edge flags as `HEURISTIC` under `strict_with_heuristic`
- Unknown `flag_mode` raises `ValueError`

### Top 3 next steps

1. **Switch config to `strict_with_heuristic`** — the BR_NONE catch-all is confirmed noise. Until this is switched, every run sends 100% of filtered markets to Claude regardless of signal quality.

2. **Add more heuristic base rates** — on today's snapshot HEURISTIC=0 because no heuristic-matched markets survived without drift. Adding sports outcomes, crypto price levels, and legislative events would surface the HEURISTIC path and improve the `strict_with_heuristic` advantage over `strict_anomaly_only`.

3. **Log `flag_path` to the DB** — `log_signal` in logger.py should persist `flag_path` and `flag_mode` alongside each signal so resolved outcomes can be segmented by flag type (e.g., do DRIFT flags resolve correctly more often than HEURISTIC flags?).

---

## Goal 1e — Attribution fix + drift recalibration

**Branch:** `drift-fix-1e`
**Snapshot:** `data/snapshots/markets_20260616_043836.json` — same as 1c/1d
**Full report:** `reports/flag_modes.md` (updated with all four parts)

### Attribution bug (fixed)

`flag_path` recorded only the first branch to fire, so results varied by mode even for the same signal. Under `passthrough`, BR_NONE was checked before DRIFT — markets with both signals were labelled BR_NONE, and DRIFT appeared as 0 in the comparison table.

**Fix:** Each scored market now returns three independent boolean fields:
- `sig_edge`: `raw_edge > threshold`
- `sig_drift`: passes both abs AND pct drift thresholds
- `sig_br_none`: `base_rate is None and mid_price is not None`

These are computed before any mode branching and are identical across all modes. The `flag_path` field still records which branch caused the flag (mode-dependent).

**Corrected signal presence (mode-independent, 21 filtered markets):**

| Signal | Count | % of filtered |
|--------|-------|---------------|
| `sig_edge` | 7 | 33% |
| `sig_drift` | 18 | 86% |
| `sig_br_none` | 14 | 67% |
| Both edge AND drift | 7 | 33% |

Key finding: under `passthrough`, DRIFT=0 in the old flag_path table but `sig_drift`=18. All 7 EDGE markets also have drift. The 14 BR_NONE markets had their drift masked by the BR_NONE branch firing first.

### Drift recalibration

**Root cause of 86% drift-fire rate:** `compute_drift_signal` previously used only a percentage threshold (`>5%`). A 0.5-cent move at a 5-cent price = 10% drift — triggering on bid/ask rounding noise.

**Fix:** `compute_drift_signal` now requires BOTH:
- `abs_drift > drift_min_abs` (new config key, default `0.0`)
- `pct_drift > drift_min_pct` (was hardcoded `0.05`, now from config)

Config keys are at current-behavior defaults (`drift_min_abs=0.0, drift_min_pct=0.05`) — pending Reed's choice from the grid.

**Drift-by-price-bucket diagnosis:**

| Bucket | N | Drift% (pct>5%) | Avg abs |
|--------|---|----------------|---------|
| Low [0.05-0.15) | 11 | 100% | 0.027 |
| MidLo [0.15-0.35) | 3 | 67% | 0.028 |
| Mid [0.35-0.65) | 5 | 80% | 0.034 |
| High [0.65-0.95] | 2 | 50% | 0.055 |

**Threshold sweep (drift-fire rate as % of 21 filtered markets):**

| drift_min_abs | pct>5% | pct>7% | pct>10% | pct>15% | pct>20% |
|---------------|--------|--------|---------|---------|---------|
| abs>0.01 | 71% | 71% | 57% | 38% | 38% |
| abs>0.02 | 67% | 67% | 52% | 33% | 33% |
| abs>0.03 | 52% | 52% | 38% | 24% | 24% |
| abs>0.04 | 38% | 38% | 24% | 14% | 14% |
| abs>0.05 | 10% | 10% | 5% | 0% | 0% |

**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to 11 drift flags (52%), eliminating 0.5-1.5 cent noise moves. The (0.03, 0.10) cell (38%) is the next step. No mode is selective until drift is tightened — currently 86% of filtered markets pass the pct-only gate.

### Tests

9 new tests added to `tests/test_scanner.py` (107 total, all pass). Key pins:
- Market with both edge AND drift: `sig_edge=True` AND `sig_drift=True` under all three modes, regardless of which sets `flag_path`
- `sig_br_none=True` only on markets with no heuristic match
- Tiny abs move (0.005) at low price suppressed when `drift_min_abs=0.02`
- Both conditions required: large abs + small pct = no flag; small abs + large pct = no flag

### Top 3 next steps

1. **Reed sets `drift_min_abs` and `drift_min_pct`** — the sweep grid is in `reports/flag_modes.md`. Recommended start: (0.03, 0.05). This is the one change that makes the drift signal meaningful rather than noise-dominated.

2. **Re-run `flag_mode_compare.py` after choosing drift thresholds** — the report regenerates automatically from config. The updated drift-fire rate will determine whether `strict_with_heuristic` is actually selective or still forwards most filtered markets.

3. **Log `sig_*` fields to the DB** — now that `sig_edge`, `sig_drift`, and `sig_br_none` are always computed, logging them alongside each signal enables future analysis of which signal types predict correct outcomes (once markets resolve).

---

## Goal 2a — Real Kalshi fills pulled into ledger

**Branch:** `real-fills-2b`
**DB backup:** `leviathan.db.bak_2b` (taken before any schema changes)

### What changed

**kalshi.py:** `fetch_fills()` (GET /portfolio/fills, cursor-paginated) and `fetch_positions()` (GET /portfolio/positions) added. Both are GET-only, mirror the existing RSA auth pattern exactly, and warn on empty response without crashing.

**logger.py schema:** 11 new columns added via idempotent, PRAGMA-checked `ALTER TABLE` (never drops or truncates). Existing 24 signals tagged `source='paper'`. New columns: `source`, `from_signal`, `signal_call_id`, `direction_aligned`, `entry_price`, `fill_count`, `fill_fee`, `contract_type`, `segment`, `resolution_date`, `logged_under`.

`pull_real_fills()`: fetches all fills, inserts as `source='real_fill'`, matches each fill's ticker against prior paper signals, sets `from_signal`, `signal_call_id`, and `direction_aligned`.

`resolve_outcomes()`: extended to handle real fills — uses `entry_price` (actual trade price) instead of `market_price`; subtracts `fill_fee / fill_count` per unit from P&L.

`get_stats()`: now filters to `source='paper'` only. New `get_stats_real()` for real fill stats. Paper and real fill rows never blend in any stats query.

### Fills pulled (live run 2026-06-16)

| Metric | Value |
|--------|-------|
| Total fills pulled | 15 |
| Matched a prior Leviathan signal | 5 |
| Direction-aligned with signal | 4 |
| Direction-contradictory | 1 |
| Already resolved | 1 |
| Open (pending July settlement) | 14 |

**The 1 resolved fill:** KXAAAGASD-26APR13-4.125, direction=YES, resolved NO → LOSS, net P&L = -$0.73 (including fee). This is the only bet with a settled outcome so far.

**The 14 open fills** are mostly July 2026 expiry markets (KXUSAEXPANDTERRITORY-26JUL01, KXTAIWANLVL4-26JUL01, KXIMPEACHCABINET-26JUL01 ×3, KXMEDIARELEASEPRISONBREAK-27JUL01 ×3, KXTVSEASONRELEASETHELASTOFUS-27JUL, KXIMPEACHCABINET-27JAN01 ×2, KXAILEGISLATION-27JAN01 ×2). They will auto-resolve when `resolve_outcomes` is called after market settlement.

**Signal-matched fills:**
- KXUSAEXPANDTERRITORY: NO fill, signal NO → aligned
- KXTAIWANLVL4: NO fill, signal NO → aligned
- KXIMPEACHCABINET-26JUL01: 2 YES fills (aligned) + 1 NO fill (contradictory — covers both sides)

> **Note:** The real-bet sample (15 fills, 5 signal-matched) is far too small for statistical inference. It provides directional evidence that trades are being tracked correctly, not proof of model quality. Signal correctness requires resolved outcomes across many bets.

> **Note on fee units:** Kalshi's `fee_cost` in the fills API response appears to be total dollar cost per fill event (not per contract). If `fill_count=1` understates actual contract count, fee-per-unit in resolved P&L may be overstated. Recommend verifying fee amounts against Kalshi's fee schedule before drawing P&L conclusions.

### Tests

13 new tests added to `tests/test_logger.py` (120 total, all pass). Key pins:
- Schema migration idempotent (safe to run twice)
- Existing rows survive migration and get tagged `source='paper'`
- Fill matching known ticker → `from_signal=1`, correct `direction_aligned`
- Fill on unknown ticker → `from_signal=0`, `signal_call_id=NULL`
- Real-fill P&L net of fees with correct binary payoff (YES win/loss)
- Unresolved real fill stays unresolved without error
- `get_stats()` excludes real fills; `get_stats_real()` excludes paper signals

### Top 3 next steps

1. **Verify fee_cost units with Kalshi** — the resolved trade shows a large fee relative to contract price. Confirm whether `fee_cost` is total dollars for the fill or per-contract, and whether `count=1` reflects actual contract count. This affects real P&L accuracy.

2. **Set drift thresholds and re-run the scanner** — the open July fills show active real bets. Once drift is recalibrated (Goal 1e recommendation: abs>0.03), re-run the scanner with `strict_with_heuristic` to see which of those open positions Leviathan would re-flag today.

3. **Add `flag_path` and `sig_*` to log_signal** — now that real fills are tracked, logging the signal attribution (which signal type triggered the paper call) enables apples-to-apples comparison: do DRIFT-flagged signals outperform BR_NONE ones once both sets resolve?

---

## Goal 2b — Research-probe experiment (research-probe-2c)

**Branch:** `research-probe-2c`
**Result:** 133 tests, 0 failures, exit 0. First live run: 3 markets probed.

### Architecture

`analysis/research_probe.py` implements a four-part experiment:

**Part A — Stratified sample:** 5 volume tiers (thin <500, light 500-5k, medium 5-50k, heavy 50-150k, liquid >150k) with per-tier quotas summing to ~50 markets. Deliberately includes filter_markets rejects — the point is to test for edge *outside* the funnel. Each market annotated with `filter_pass` (bool) and `vol_tier`.

**Part B — Claude+websearch probe:** Claude CLI called with `--allowedTools WebSearch` per market. Expects JSON response: `{ticker, claude_estimate, predicted_direction, confidence, rationale}`. ANTHROPIC_API_KEY excluded from env so CLI uses Pro OAuth. Bounded by `max_probe_markets` (config: `scoring.max_probe_markets`, default 10). Sequential, not parallel. Call count and total runtime printed per run.

**Part C — Research-probe segment in DB:** Each result inserted via `logger.log_probe()` with `source='research_probe'` and `segment='research_probe'` — a fourth bucket that never blends with paper, real_fill, or edge_thesis in stats queries. Fields: `ticker`, `market_price_at_probe`, `claude_estimate`, `divergence` (estimate − price), `predicted_direction`, `confidence`, `rationale`, `runtime_ms`.

**Part D — Forward scoring:** `resolve_outcomes()` handles probe rows naturally (queries blank outcome, matches ticker to Kalshi API). `get_stats_probe(high_divergence_threshold=0.10)` reports total probes, resolved count, hit rate, high-divergence subset stats (|div| ≥ threshold), and a plain-language verdict (PENDING / PARTIAL / COMPLETE).

### First live run — 2026-06-16

**Snapshot:** 2026-06-16T04:38:36Z (2428 markets)

**Stratified sample composition (50 markets):**

| Volume tier       | Count | Filter survivors |
|-------------------|-------|-----------------|
| thin (<500)       | 12    | 0               |
| light (500-5k)    | 12    | 0               |
| medium (5-50k)    | 10    | 0               |
| heavy (50-150k)   | 8     | 0               |
| liquid (>150k)    | 8     | 0               |
| **Total**         | **50**| **0/50**        |

Note: 0 filter survivors is expected — the snapshot was from an early morning fetch when most liquid markets had thin spreads; all 50 markets failed min_volume or other filters at that timestamp.

**Markets probed (3 of 10 — run halted by monthly spend limit):**

| Ticker | Market price | Claude estimate | Divergence | Direction | Confidence |
|--------|-------------|----------------|------------|-----------|------------|
| KXNEXTDNCCHAIR-45-FSHA | 0.195 | 0.080 | −0.115 | NO | MED |
| ECMOV-28NOV07-DEM211T538 | 0.045 | 0.050 | +0.005 | PASS | MED |
| KXVPRESNOMD-28-ZMAM | 0.005 | 0.005 | +0.000 | PASS | HIGH |

**Call count:** 3 CLI invocations, avg 61s/market, total 183.6s
**Probe rows in DB:** 3 (source='research_probe'), unresolved — awaiting market settlement

### Core assumption being tested

Can AI+websearch research find mispricings that the market hasn't priced? The answer is **PENDING resolution** — not yet known. The divergences logged above are hypotheses only. Once the probed markets settle, `get_stats_probe()` will report whether Claude's predicted directions were correct and whether high-divergence calls (|div| ≥ 0.10) beat the market at a statistically meaningful rate.

### Test suite

13 new tests in `tests/test_research_probe.py` (all offline, tmp DB, CLI mocked):
- `test_stratified_sample_includes_filter_rejects` — thin-volume markets appear with filter_pass=False
- `test_stratified_sample_respects_target_n` — sample never exceeds target_n
- `test_stratified_sample_annotates_filter_pass` — every market has filter_pass bool
- `test_stratified_sample_covers_multiple_volume_tiers` — ≥3 tiers represented
- `test_log_probe_inserts_research_probe_row` — source='research_probe', correct fields
- `test_probe_rows_excluded_from_paper_stats` — get_stats() ignores probe rows
- `test_probe_rows_excluded_from_real_fill_stats` — get_stats_real() ignores probe rows
- `test_resolved_probe_direction_correct_scores_win` — YES predicted + YES resolved → WIN
- `test_resolved_probe_direction_wrong_scores_loss` — YES predicted + NO resolved → LOSS
- `test_unresolved_probe_stays_pending` — blank result stays blank
- `test_get_stats_probe_pending_verdict` — before resolution, verdict contains "PENDING"
- `test_get_stats_probe_high_divergence_subset` — |div|≥0.10 subset computed correctly
- `test_run_probe_respects_max_probe_cap` — run halts at max_probe_markets

### Next steps

1. **Re-run the probe** once Claude Pro spend limit resets — target the full 10-market cap to build a larger hypothesis set, then repeat weekly as markets resolve.
2. **Add `filter_pass` context to probe output** — the current run shows 0 filter survivors; investigate whether the snapshot timing (4am UTC) explains this or whether the filter thresholds are too strict.
3. **Track divergence calibration** — once 20+ probe rows resolve, compute calibration error (average |claude_estimate − actual_outcome|) vs. the market's calibration error to measure research alpha.

---

## Session 3 — Smart money signal quality + pipeline wiring (2026-06-17)

**Test count:** 230 → 231 (all passing)

### Problem: cross-reference was producing ~127 noisy signals

The Polymarket-to-Kalshi title matching was generating false positives because:
1. SequenceMatcher uses character-level similarity — "Will Saudi Arabia join the Abraham Accords?" scored 52% against a Scottie Scheffler golf market purely because both titles share the phrase structure "Will ... win the ..."
2. Sports game bets (outcome="No" on "Will Germany win on 2026-06-25?") passed through `_is_binary_position()` because the outcome itself is binary — the problem was in the title, not the outcome
3. No minimum word-overlap gate; single-word matches (e.g., "mayoral") could score above threshold

### Fixes applied

**`analysis/smart_money_scan.py`:**

Added `_SPORTS_TITLE_PATTERNS` and `_is_sports_title(title)` to detect soccer game lines, competition winner bets, and major sports tournaments by title keywords (`" vs. "`, `"win on 202"`, `"world cup"`, `"nba"`, `"super bowl"`, etc.).

Added a **2-keyword minimum gate** in `_match_to_kalshi()`: before computing any score, `len(poly_words & kalshi_words) < 2` skips the candidate. This eliminates all character-similarity false positives where the underlying topic is completely different.

Raised match threshold from `0.30` → `0.50`.

Added `kalshi_title` to the signal dict so the matched Kalshi market name is available in reports (previously only the ticker was stored).

Result: 127+ noisy signals → **15 clean cross-references** on the 2026-06-17 live run.

**Signal quality (2026-06-17 live run):**

| Kalshi ticker | Trader | Position | Match |
|---|---|---|---|
| KXMAKERFIELDBY-27JAN01-GRE | 0x2c3350 | $253k NO | 72% |
| KXLONDONMAYOR-28MAY01-ZPOL | wan123 / denizz | $82k / $26k NO | 62% |
| CONTROLS-2028-R/D | wan123 | $22k / $21k | 59% |
| KXRTICKET-28NOV07-JVANEKIR | wan123 | $21k YES | 65% |
| KXABRAHAMSY-29-JAN20 | denizz | $12k / $7k / $2k NO | 66–72% |
| KXABRAHAMSA-29-JAN20 | denizz | $4k / $1k NO | 51–83% |
| SENATEUT-28-R/D | wan123 / Erasmus | $6k / $2k | 68–70% |
| SENATEFL-28-R | wan123 | $1k NO | 100% |
| EUEXIT-30 | denizz | $13k NO | 60% |

### Bug fix: watchlist markets were silently dropped

`scanner.score_markets()` sorts watchlist-tagged markets first but does not set `flag=True`. `main.py` then filtered `flagged_markets = [m for m in scored_markets if m.get("flag")]`, which silently dropped any watchlist market that lacked drift or heuristic edge.

**Fix in `main.py`:** After `score_markets()`, any market with `watchlist_signal=True` and `flag=False` is explicitly force-flagged with `flag_path="WATCHLIST"` and prepended to `flagged_markets`. These markets now reliably reach Claude scoring. `watchlist_signal` is also passed through to the final signal dict so it appears in the email report.

### Probe calibration sync

`research_probe.py`'s `PROBE_SYSTEM` prompt had no calibration rules — it was a single sentence. A live Prison Break market probe returned 82% YES on a 5.5% market, demonstrating the risk of uncalibrated tail overconfidence.

Synced the full **6-rule CALIBRATION RULES** block from `scorer.py` into `PROBE_SYSTEM`:
1. Tail probability — require extraordinary evidence to set estimate >30% on a sub-15% market
2. Source chain — verify sources are truly independent before citing "multiple sources confirm X"
3. Announced vs completed — "announced" is not "completed" by the deadline
4. Entertainment/media — treat streaming release dates with extreme skepticism (~25% base rate)
5. High confidence threshold — only HIGH when primary-source evidence directly addresses the deadline
6. Edge requirement — only call YES/NO if estimate differs from market by ≥10pp

Also added per-market time-horizon context to probe prompts (`closes within 7 days — near-term catalysts most relevant`, etc.) so Claude weights evidence appropriately.

### Research probe: watchlist prioritisation

`run_probe()` now loads `data/smart_money/latest_signals.json` and force-inserts watchlist tickers at the front of the probe queue before filling remaining slots from the stratified sample. The divergence table annotates watchlist markets with `[WL]`. This ensures confirmed smart money positions are always probed first within the `max_probe_markets` cap.

### Report improvements

- **`_smart_money_section()`**: cross-references now sorted by `match_score × position_val` (quality-weighted); shows `kalshi_title` below each row so both the Polymarket title and matched Kalshi market are visible; column added for `match_score`
- **Signal block**: `watchlist_signal=True` adds "Watchlist: Top Polymarket Trader" to the Signals fired line
- **Header**: "Smart Money" line now shows count of Kalshi cross-references (`N Kalshi x-refs from top Polymarket traders`)
- **Track record**: probe stats block added (`probe_stats` parameter passed from `main.py`)

### Other fixes

- `daily_smart_money.py`: now calls `save_signals_cache()` after each scan (was missing — ticker cache was not being updated by the scheduled job); fixed Windows cp1252 crash caused by `→` character in print statement
- `logger.py` `resolve_outcomes()`: added 3-attempt exponential backoff (1.5s, 3.0s) for Kalshi API overload errors; rate-limit sleep bumped 0.25s → 0.3s
- `main.py`: snapshot saved after every market fetch so analysis scripts always have a fresh catalog
- `analysis/smart_money_scan.py`: markdown report table now includes `kalshi_title` column; sorted by quality-weighted position size

### Tests

1 new test: `test_watchlist_flag_path_override` — verifies that an unflagged watchlist market gets `flag=True` and `flag_path="WATCHLIST"` when the main.py force-flag logic runs. (231 total, all passing)

### Open items

1. **Prison Break market** (`KXMEDIARELEASEPRISONBREAK-27JUL01`) settles 2026-07-08 — run `resolve_outcomes({})` after that date to write the official result. Currently manually scored as LOSS.
2. **Senate/Makerfield/Abraham Accords tickers** — these are 2027–2029 markets; they are now in the scoring queue via watchlist boost but will not resolve for months. Monitor for price drift as confirming signal.
3. **`filter_pass` context in probe** — stratified sample showed 0 filter survivors in the 4am UTC snapshot. Consider sampling from a fresher snapshot or relaxing filter thresholds in the probe path.

---

## Session 4 — Signal quality hardening + flag_path analytics (2026-06-17)

**Test count:** 231 → 286 (all passing)

### Entity contradiction guard

Remaining false positives from the Session 3 live scan (15 signals, 3 known FPs):

| FP ticker | Mismatch type | Example |
|---|---|---|
| EUEXIT-30 | Org group | "leave OPEC" matched "leave EU" |
| SENATEUT-28-R/D | US state | "Texas Senate" matched "Utah Senate" |
| KXLONDONMAYOR | City | "LA Mayor" matched "London Mayor" |

**Fix:** `_entity_contradiction(poly_title, kalshi_title)` in `analysis/smart_money_scan.py`:
- **US state check**: extracts state words from both titles using a 46-word frozenset (omits Carolina/Dakota to avoid treating NC vs SC as contradictory). Padded matching (` texas ` not `texas`) prevents "jersey" from false-matching "New Jersey".
- **City check**: 25 major world cities; both must name a city AND they must differ.
- **Org check**: 6 exclusive organization groups (OPEC, EU/Brexit, NATO, IMF, WTO, ASEAN). Each group is a frozenset; titles that reference organizations from different groups are rejected.

Called inside `_match_to_kalshi()` before computing score — no overhead if 2-keyword gate fails first.

**Tests:** 18 new tests in `tests/test_smart_money.py` covering all three mismatch types and verifying legitimate matches are NOT rejected.

### Signal grouping

`_group_signals_by_ticker(signals)` added. Multiple traders pointing at the same Kalshi market produce one aggregated entry with:
- `total_position_val`: sum of all trader positions
- `trader_count`: unique traders (deduplicated by name)
- `directions`: `{"YES": N, "NO": M}` vote tallies
- `consensus_direction`: "YES" / "NO" / "MIXED" / "UNKNOWN"

Grouped summary printed to console after the flat signal list. The `result` dict includes both `"kalshi_signals"` (flat per-trader list) and `"grouped_signals"` (aggregated by ticker).

**Tests:** 7 new tests covering aggregation, direction voting, MIXED consensus, trader dedup, empty input, and UNKNOWN direction.

### Expanded heuristic base rates (35 categories, was 18)

Added 10 new market category groups to `scanner.estimate_base_rate()`:

| Category | Base rate | Rationale |
|---|---|---|
| Legislative passage | 0.35 | Bills with Kalshi presence have some momentum but most don't pass |
| Presidential veto | 0.20 | Vetoes are rarer than passage |
| Executive orders | 0.45 | Fairly common executive action |
| Senate confirmation | 0.55 | Most nominees confirmed historically |
| Resign/step down | 0.20 | Rare for sitting officials |
| Presidential pardon | 0.35 | Relatively common during administrations |
| Lift sanctions | 0.20 | Harder to lift than impose (checked first in order) |
| Impose sanctions | 0.45 | Fairly common US/EU action |
| Nuclear deal/accord | 0.20 | Hard diplomatic outcomes |
| Supreme Court / rulings | 0.50 | Genuinely uncertain |
| Economic indicators (CPI/GDP/jobs) | 0.50 | 50/50 for specific threshold questions |
| Crypto price markets | 0.50 | Symmetric around any given level |
| Hurricanes / tropical storms | 0.45 | Uncertain once tracked |
| Diplomatic recognition | 0.30 | Uncommon unilateral actions |
| Lawsuit settlement | 0.40 | Moderate base rate |
| NATO/EU accession | 0.25–0.35 | Hard but possible within 5-year horizons |

**Order note:** "lift sanctions" checked before "impose sanctions" because "will the EU lift sanctions on Russia" contains "sanctions on" which would otherwise match the impose group. More-specific patterns always precede less-specific ones.

**Tests:** 25 new tests in `tests/test_scanner.py` pinning each new category.

**Impact:** More markets now get a `base_rate` in `strict_with_heuristic` mode, enabling the `HEURISTIC` flag_path for markets where Claude+websearch would find real edge. Previously these markets were dropped entirely in strict mode.

### `flag_path` and `watchlist_signal` in logger

Two new columns added via additive schema migration (safe on existing `leviathan.db`):
- `flag_path TEXT` — EDGE / BR_NONE / DRIFT / HEURISTIC / WATCHLIST
- `watchlist_signal INTEGER DEFAULT 0`

`log_signal()` now persists both. Both signal dicts in `main.py` (primary and second-pass) forward `flag_path` from the market object.

**New function:** `get_stats_by_flag_path()` — returns win rate, total calls, wins, and hypothetical P&L broken down by flag path. Only paper signals with a resolved outcome. Enables outcome segmentation once markets settle.

**Tests:** 6 new tests covering column presence, flag_path persistence (three cases), and `get_stats_by_flag_path` (basic, resolved-only, excludes real fills).

### Report: win rate by flag_path

`compile_report()` and `compile_weekly_digest()` now accept `flag_path_stats` parameter and render a compact table in the track record section:

```
  Win Rate by Signal Path  (resolved only):
    Path            Total  Wins    Win%       P&L
    --------------  -----  ----  ------  --------
    WATCHLIST           3     3    100%     $2.10
    HEURISTIC           5     3     60%     $0.85
    DRIFT               8     4     50%     $0.20
    EDGE                2     1     50%     $0.40
```

`main.py` fetches and passes `logger.get_stats_by_flag_path()` in both normal and fallback report paths.

### Git commits this session

1. `461a7a5` — Entity contradiction guard + signal grouping + 24 new tests (255 total)
2. `92fc8f5` — Expanded heuristic base rates, 25 new tests (280 total)
3. `9d8bde0` — flag_path/watchlist_signal schema + get_stats_by_flag_path + 6 tests (286 total)
4. `5f6fcc4` — Flag_path win-rate table in daily and weekly reports

### Open items

1. **Prison Break market** (`KXMEDIARELEASEPRISONBREAK-27JUL01`) settles 2026-07-08 — unchanged.
2. **Run a fresh daily scan** to verify entity contradiction guard eliminates the 3 FPs (EUEXIT/SENATEUT/KXLONDONMAYOR).
3. **Flag_path outcome data** — currently 0 resolved paper signals. Once 10+ markets resolve, `get_stats_by_flag_path()` will show whether WATCHLIST or HEURISTIC has higher win rate than raw EDGE/DRIFT.
