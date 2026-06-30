# Leviathan — Progress Log

---

## Current State (2026-06-28)

**What Leviathan does today:**
Kalshi prediction market intelligence bot. Scans the full Kalshi catalog, scores flagged markets via Claude+websearch, cross-references Polymarket smart money, and emails a structured daily report.

**Test suite:** 1342 tests, 0 failures (248 commits)

**Pipeline steps:**
1. Fetch Kalshi markets → filter by volume, price band, close time, high-price gate (≥0.85 excluded)
2. Score markets: heuristic base rates (35+ categories), drift detection, whale alerts, Polymarket cross-ref
3. Claude+websearch scoring with calibration rules and signal convergence pre-sort
4. Compile report: TOP PICKS, BETTING QUEUE (urgency-sorted), EV/contract per signal, UPCOMING RESOLUTIONS
5. Email via Gmail SMTP; export to Power BI CSVs after each run

**Active features:**
- 35+ heuristic base rate categories; EDGE/DRIFT/HEURISTIC/WATCHLIST/CROSS_MARKET flag paths
- Smart money watchlist with daily drift alerts and direction enrichment
- Research probe experiment (stratified sample, forward scoring into DB)
- Real fill tracking separated from paper signals in all stats
- CSV export for Power BI (whitelist-filtered `signals.csv` + `runs.csv` with computed cols)
- Machine-readable backlog (`backlog.json`, 25 items: 11 done, 9 locked, 3 blocked)
- High-price filter (≥0.85 mid_price excluded pre-scoring)
- Betting queue: top 5 unplaced signals sorted by `(edge×0.6) + (1/days×0.4)`
- EV/contract displayed in signal blocks and top picks

**Sample size (2026-06-28):** ~4 resolved paper signals.
- Next gate: Brier tracking at n≥25, edge decay at n≥30
- Per-category gates fire at n≥15 resolved per heuristic category
- Per-wallet gates fire at n≥10 resolved per smart-money wallet

---

## Goal 1b — pytest suite for logger and scanner

**Branch:** `tests-1b` | **Result:** 81 tests, 0 failures

### Coverage

**logger.py — `resolve_outcomes`**
- All 5 payoff cases verified to the cent (YES-win, YES-loss, NO-win, NO-loss, blank direction)
- Confirmed reads `market_price`, not `edge`
- Skips already-resolved rows; leaves rows untouched when Kalshi returns unsettled

**logger.py — `get_stats`**
- `win_rate` is None when `resolved == 0`
- Round-trip confirms blank-outcome convention between `log_signal`, `resolve_outcomes`, `get_stats`

**scanner.py — `filter_markets`**
- Price bounds, volume limits, close-time window, efficient-market keyword drop

**scanner.py — `classify_time_horizon`**
- 15 boundary cases: INTRADAY / WEEKLY / MONTHLY / QUARTERLY / LONG

**Regression: per-$1 payoff convention**
- 28 parametrized cases at prices 0.10–0.90 for all four payoff cases

---

## Goal 1c — Threshold sweep over live Kalshi snapshot

**Branch:** `sweep-1c` | **Snapshot:** 2,428 markets (prod, 2026-06-16)

**Finding:** 100% flag rate across all 27 threshold combinations. The `BR_NONE` fallback fires on every market the scanner has no heuristic for. Flag path at production settings (edge=0.08): BR_NONE 14/21 (67%), EDGE 7/21 (33%), DRIFT 0/21 (0%). `strict_with_heuristic` mode recommended to eliminate the BR_NONE catch-all.

---

## Goal 1d — Config-driven flag modes + comparison report

**Branch:** `flag-modes-1d`

`scanner.score_market` now reads `config.markets.flag_mode`:
- `passthrough` — original: flag if edge OR BR_NONE OR drift
- `strict_anomaly_only` — drift-only (whale applies post-hoc)
- `strict_with_heuristic` — drift OR (heuristic base rate AND edge > threshold)

Each scored market returns `flag_path` (EDGE / BR_NONE / DRIFT / HEURISTIC / None) and `flag_mode`.

**Finding:** Drift is far more active than Goal 1c suggested — `flag_path` ordering bug had masked DRIFT under BR_NONE. 18 of 21 filtered markets have `drift_flag=True` at pct-only threshold. `strict_with_heuristic` recommended.

17 new tests (98 total).

---

## Goal 1e — Attribution fix + drift recalibration

**Branch:** `drift-fix-1e`

**Attribution bug fixed:** Each scored market now returns independent booleans `sig_edge`, `sig_drift`, `sig_br_none` computed before mode branching. `flag_path` still records which branch fired (mode-dependent).

**Drift recalibration:** `compute_drift_signal` now requires BOTH `abs_drift > drift_min_abs` AND `pct_drift > drift_min_pct` (both from config). Config keys default to current behavior (abs=0.0, pct=0.05).

Drift-fire rate at abs>0.03, pct>0.05: 52% (down from 86% pct-only). Recommended starting point.

9 new tests (107 total).

---

## Goal 2a — Real Kalshi fills pulled into ledger

**Branch:** `real-fills-2b` | **Backup:** `leviathan.db.bak_2b`

**kalshi.py:** `fetch_fills()` (GET /portfolio/fills, cursor-paginated) and `fetch_positions()` added. GET-only.

**logger.py schema:** 11 new columns via idempotent `ALTER TABLE`. Existing rows tagged `source='paper'`. New columns: `source`, `from_signal`, `signal_call_id`, `direction_aligned`, `entry_price`, `fill_count`, `fill_fee`, `contract_type`, `segment`, `resolution_date`, `logged_under`.

`pull_real_fills()`: inserts as `source='real_fill'`, matches tickers against paper signals.
`resolve_outcomes()`: uses `entry_price` for real fills; subtracts `fill_fee / fill_count` per unit.
`get_stats()`: paper-only. `get_stats_real()`: real-fill-only. Never blended.

**Live run (2026-06-16):** 15 fills pulled, 5 matched paper signals, 4 direction-aligned, 1 contradictory. 1 resolved (KXAAAGASD-26APR13-4.125, YES→NO, LOSS, −$0.73). 14 open (July 2026 expiry).

13 new tests (120 total).

---

## Goal 2b — Research probe experiment

**Branch:** `research-probe-2c` | **Result:** 133 tests, first live run 3 markets probed

**Architecture:**
- Part A: Stratified sample across 5 volume tiers (thin <500 to liquid >150k), quotas summing to ~50 markets. Includes filter_markets rejects. Annotated with `filter_pass` bool and `vol_tier`.
- Part B: Claude+websearch probe per market, bounded by `max_probe_markets`. Expects JSON response with `{ticker, claude_estimate, predicted_direction, confidence, rationale}`.
- Part C: `logger.log_probe()` inserts `source='research_probe'`, never blends with paper or real_fill stats.
- Part D: `resolve_outcomes()` handles probe rows. `get_stats_probe()` reports hit rate for high-divergence calls.

**First live run (2026-06-16):** 3 markets probed (run halted by spend limit), 3 probe rows logged, all pending resolution.

---

## Session 3 — Smart money signal quality + pipeline wiring (2026-06-17)

**Test count:** 230 → 231

### Problem: cross-reference noise (127 noisy signals → 15 clean)

`analysis/smart_money_scan.py` fixes:
- Added `_SPORTS_TITLE_PATTERNS` and `_is_sports_title()` — filters soccer game lines, tournament bets
- 2-keyword minimum gate in `_match_to_kalshi()` eliminates character-similarity false positives
- Match threshold raised from 0.30 → 0.50
- `kalshi_title` added to signal dict

**Watchlist force-flag bug fixed:** `main.py` now explicitly force-flags `watchlist_signal=True` markets with `flag_path="WATCHLIST"` and prepends them to `flagged_markets`. Previously they were silently dropped if no drift or edge fired.

**Probe calibration sync:** Full 6-rule CALIBRATION RULES block from `scorer.py` synced into `PROBE_SYSTEM`. Per-market time-horizon context added to probe prompts.

**Other fixes:** `daily_smart_money.py` now calls `save_signals_cache()` after each scan; Windows cp1252 crash fixed; `resolve_outcomes()` adds 3-attempt exponential backoff for Kalshi API overload; snapshot saved after every market fetch.

---

## Session 4 — Signal quality hardening + flag_path analytics (2026-06-17)

**Test count:** 231 → 286

### Entity contradiction guard

`_entity_contradiction(poly_title, kalshi_title)` in `smart_money_scan.py`:
- US state check (46-word frozenset, padded matching prevents "jersey" → "New Jersey" false match)
- City check (25 major world cities)
- Org check (6 exclusive org groups: OPEC, EU/Brexit, NATO, IMF, WTO, ASEAN)

Called before score computation — no overhead if 2-keyword gate fails first. 18 new tests.

### Signal grouping

`_group_signals_by_ticker(signals)` aggregates per-ticker: `total_position_val`, `trader_count`, `directions` vote tallies, `consensus_direction`. 7 new tests.

### Heuristic base rates expanded (35 categories, was 18)

10 new groups: legislative passage (0.35), presidential veto (0.20), executive orders (0.45), senate confirmation (0.55), resign/step down (0.20), pardon (0.35), sanctions (lift 0.20, impose 0.45), nuclear deal (0.20), Supreme Court (0.50), economic indicators (0.50), crypto price (0.50), hurricanes (0.45), diplomatic recognition (0.30), lawsuit settlement (0.40), NATO/EU accession (0.25–0.35). 25 new tests.

### flag_path and watchlist_signal in logger

Two new columns via additive migration: `flag_path TEXT`, `watchlist_signal INTEGER DEFAULT 0`.
`get_stats_by_flag_path()`: win rate + P&L segmented by signal path (resolved paper only).
Flag_path win-rate table added to `compile_report()` and `compile_weekly_digest()`. 6 new tests.

**Commits:** `461a7a5`, `92fc8f5`, `9d8bde0`, `5f6fcc4`

---

## Session 5 — Watchlist pipeline direction enrichment (2026-06-17)

**Test count:** 286 → 293

`save_signals_cache()` now writes `ticker_details` dict alongside `kalshi_tickers`:
```json
"ticker_details": {
  "KXABRAHAMSA-29-JAN20": {
    "consensus_direction": "NO", "trader_count": 2,
    "total_position_val": 15000.0, "kalshi_title": "..."
  }
}
```
`tag_watchlist_overlap()` uses `ticker_details` to annotate `watchlist_direction`, `watchlist_position_val`, `watchlist_trader_count`. WATCHLIST SIGNAL block in scorer shows trader count, combined $ position, and direction. 5 new tests.

---

## Session 6 — Signal coverage: flagged market count 9 → 15 (2026-06-17)

**Test count:** 293 → 352

**New heuristic categories:** Cabinet departure (0.65), congressional control (0.50).

**`analysis/filter_stats.py`:** `--snapshot` flag reads cached snapshots (~3s vs ~90s). Added `vol_spike` (24h vol ≥20% of total) and `price_jump` (last vs prev ≥20%) prompt-time signals.

**Report improvements:** Signal block header shows `[HEURISTIC]`/`[DRIFT]` tag; `"Heuristic Base Rate XX%"` in fired signals list when `flag_path=HEURISTIC`.

**Calibration rules 5-7:** IPO announcement (25% base, only S-1 filing counts as strong evidence), cabinet departure (65%, weight historical turnover heavily), sports debut (35%, only confirmed roster + start date counts).

**`build_prompt()` — FLAG REASON line:** Each market explains why it was flagged. **SIGNAL CONFLICT detection:** When drift and base rate point opposite directions, appends conflict warning.

**Commits:** `e625a1c`, `0b05818`, `d08e304`, `3b45bb2`, `4c680eb`, `3571d91`

---

## Session 7 — Testing + heuristic quality pass (2026-06-17)

**Test count:** 352 → 489

**New test coverage:** `test_logger.py` +11, `test_report.py` +53 (qualifying, signal blocks, weekly digest, compile_report), `test_scorer.py` +24 (liquidity context, all flag paths, horizon notes), `test_scanner.py` +25 (6 new heuristic categories).

**New heuristic categories:** Fired/dismissed (0.25, word-bounded to avoid "misfired"), government shutdown (0.15), debt ceiling (0.15), continuing resolution/omnibus (0.40), antitrust/FTC/DOJ (0.40), North Korea/DPRK (0.40, placed before "nuclear deal" to prevent false match).

**False positive fixes:** `"release"` and `"show"` removed from entertainment catch-all; bare `"season"` replaced with specific patterns ("new season", "season premiere", etc.); `"fired"` replaced with padded word-boundary match.

**Commits:** `92cb49c`, `4768a1b`, `5969cf8`, `61cc019`, `cdae26f`, `b5872ce`, `14a67d4`, `04f12bf`, `5c3e1ed`

---

## Session 8 — Cross-market force-flag + polymarket test suite (2026-06-17)

**Test count:** 489 → 649

**CROSS_MARKET flag path:** Unflagged markets with significant Polymarket price divergence promoted into the scoring queue even with no heuristic/drift/edge signal.

Architecture:
- `polymarket.fetch_and_build_index(config)` — fetches once, returns shared index
- `polymarket.match_markets(markets, index, config, *, min_gap, min_match_score)` — matches any list
- `main.py` step 3: `unflagged_markets` pool collected after scoring
- `main.py` step 4: top-N unflagged by volume promoted at `abs(gap) ≥ cross_market_min_gap`

Config keys: `cross_market_promote`, `cross_market_min_gap` (0.15), `cross_market_max_candidates` (50).

**`test_polymarket.py`:** 24 new tests covering yes_price, build_index, find_match, match_markets, enrich_flagged, cross-market promotion.

Additional work this session:
- Quality-weighted `_pre_sort_score()` replacing volume-only ordering
- Signal strength composite indicator (★×N when N≥2 corroborating sources)
- Signal urgency markers (CLOSING IN Xd), repeat-count labels (REPEAT xN)
- Kelly criterion position sizing in report (full + ¼ Kelly, YES and NO formulas)
- HIGH confidence edge gate: auto-downgrade to MED when abs(edge) < 10pp
- TOP PICKS executive summary (top-3 by confidence/strength/edge at report top)
- Legal heuristics: pardon (35%), plea deal (45%), acquittal (35%)
- 3 heuristic gap fills: Fed pause/hold (0.50), federal budget (0.40), 25th Amendment (0.05)
- 5 new heuristics: arrested/testimony/approval/strikes/awards
- Various calibration rules, recalibrations (shutdown 0.85 avoid / 0.15 begins; debt ceiling 0.70 raise)
- PROGRESS.md + README housekeeping

**Commits 1–23** (see original for full list)

---

## Session 9 — Brier score calibration + digest integration (2026-06-17)

**Test count:** 649 → 791

**Brier score:** `logger.get_brier_score()` computes `mean((estimate − outcome_binary)^2)` for resolved paper signals. Labels: EXCELLENT (≤0.10), GOOD (≤0.20), FAIR (≤0.25), POOR (>0.25). Research probe rows excluded.

Surfaced in `analysis/backtest.py` COMBINED SUMMARY and `report.compile_weekly_digest()` TRACK RECORD section.

**BR_NONE gap-fill passes (commits 25-31):** 4 passes adding 48 heuristic categories, validated on a 28-market real Kalshi snapshot: BR_NONE = 0/28 = 0.0%.

Categories added include: minimum wage (0.25), national emergency (0.25), nuclear plant accident (0.05), nuclear weapons (0.05), NATO Article 5 (0.05), troop withdrawal (0.30), commodity/energy prices (0.40), inflation thresholds (0.50), wildfire (0.35), tech product releases (0.55), "be acquired" / "be taken over", bank failure (0.15), gun control (0.20), currency depreciation (0.40), company valuation (0.35), volcanic eruption (0.05), athlete retirement, trailer release, AGI, and many others.

`get_stats_by_confidence()` groups win rate/P&L by HIGH/MED/LOW confidence.

**Commits 24–31:** `02af675`, `c4875d9`, `7edf3c9`, `1568279`, `fa99b2d`, `65d81d6`, `8b6a540`, `d34aace`

---

## Session 10 — Calibration rules 23-28, heuristic direction signaling, CLAUDE OVERRIDE (2026-06-17)

**Test count:** 791 → 809

**Calibration rules 23-28 in `scorer.py` and `research_probe.py`:**
- Rule 23: AI Capability Milestones — exam passage ~40%, AGI <5%
- Rule 24: Bank Failure / Financial System Risk — named bank ~15%, systemic ~10%
- Rule 25: Emerging Technology Readiness — L4/L5 AV ~25%, quantum RSA <5%
- Rule 26: Climate / Environmental Records — hottest year ~40%
- Rule 27: Crypto / Digital Assets — Rule 13 applies strictly; deviate only if >25pp from 50%
- Rule 28: Short-Horizon Edge Decay — INTRADAY/WEEKLY: require 15pp (not 10pp) + 72h evidence

**`score_market()` — `heuristic_direction`:** "YES" / "NO" / "NEUTRAL" / None based on base rate vs mid price.

**`build_prompt()` — lean annotation:** "FLAG REASON: EDGE — heuristic base rate 30% vs market price — leans NO"

**`report._signal_block()` — CLAUDE OVERRIDE indicator:** When Claude's direction opposes scanner base rate by >5pp, shows `[!] CLAUDE OVERRIDE: Base rate 20% leans NO but Claude called YES — requires strong independent evidence.`

**Commits 32–35:** `4f8d1a7`, `2e0f738`, `55e0448`, `b7f8ef0`

---

## Session 11 — Short-horizon enforcement, alignment analytics (2026-06-17)

**Test count:** 809 → 875

**`logger.py` schema additions:** `base_rate`, `heuristic_direction`, `short_horizon` columns via additive migration. `get_stats_by_heuristic_alignment()` groups paper signals into aligned/override/no_heuristic.

**Scanner: short-horizon edge decay at filter level:** Markets with `time_horizon in (INTRADAY, WEEKLY)` require `raw_edge > 0.15` (configurable `short_horizon_edge_threshold`). `short_horizon: bool` in `score_market()` return dict.

**`build_prompt()` — `[!] SHORT HORIZON` warning:** Rule 28 context injected for markets closing within 7 days.

**`_pre_sort_score()` enhancements:**
- Signal convergence bonus: +3 for 3+ agreeing directional sources, +1 for 2
- Short-horizon penalty: markets with score <5 get −2 so long-horizon signals win Claude slots
- vol_spike (+1) and price_jump (+2) boosts

**Net-of-spread edge (`net_edge`):** `scanner.py` computes `net_edge = raw_edge − half_spread`. Surfaced in scorer prompt with [SPREAD CONSUMES EDGE] / [thin net edge] warnings. `get_stats_by_net_edge()` in calibration.

**Smart event dedup (`dedup_by_event_scored()`):** Runs post-scoring, uses priority: watchlist_signal > net_edge > raw_edge > volume. Previously pre-scoring, volume-only.

**`[SHORT HORIZON — verify within 72h]` in signal header.**

**Commits 36–49** (see original for full list)

---

## Session 12 — Net-of-spread edge, watchlist convergence, conflict detection (2026-06-17)

Merged into Session 11 above. Key additions:
- `watchlist_direction` counted as independent directional source in convergence scoring
- Cross-market conflict detection in `build_prompt()`: `[!] HEURISTIC vs POLYMARKET CONFLICT`, `[!] HEURISTIC vs CONSENSUS CONFLICT`
- Pre-sort penalizes (−2) strong Poly/heuristic conflict with no corroboration
- `analysis/net_edge_analysis.py` diagnostic for live distribution of flagged market net_edge
- `CROSS_MARKET` promoted markets get `net_edge` from Poly gap minus half-spread

---

## Session 13 — Signal quality and calibration improvements (2026-06-18)

**Test count:** 875 → 909

**Commit 50 — Net Edge column in weekly digest**
`compile_weekly_digest()`: `{'Net':>7}` column in MARKETS FLAGGED table, formatted `+X.Xpp`. 4 new tests.

**Commit 51 — Signal persistence tracking**
`logger.get_signal_history_batch(tickers, days=14)` — single query for all tickers.
`main.py` enriches signals with `prior_appearances`, `prior_yes`, `prior_no`, `direction_consistent`, `first_flagged_price`.
`_pre_sort_score()`: persistent + consistent 3+ day signals get +3 boost; 2-day +2.
`scorer.build_prompt()`: "Signal history" block shows days-seen, YES/NO split, [CONSISTENT]/[MIXED], price drift note.
`report._signal_block()`: "Nd/14d: xY/yN — consistent/mixed" persistence line. 17 new tests.

**Commit 52 — Calibration feedback loop**
`scorer.build_system_prompt(calibration)`: appends CALIBRATION FEEDBACK when resolved data exists. LOW/MED/HIGH confidence thresholds surfaced as instructions to Claude.
`main.py`: calls `logger.get_stats_by_confidence()` before each Claude run. 5 new tests.

**Commit 53 — Close time tracking + close-horizon calibration**
`logger.py`: ADD COLUMN `close_time TEXT` (additive). `log_signal()` stores `close_time || expiration_time`.
`get_stats_by_close_horizon()`: buckets resolved signals by days-to-close at signal time.
`analysis/calibration.py`: BY ACTUAL DAYS-TO-CLOSE section. 13 new tests.

---

## Session 14 — Profitability hardening (2026-06-18)

**Test count:** 909 → 1081

20 commits (54–62) adding heuristic categories, calibration rules, and analytics:

**Key additions:**
- Rule 33: Social media post markets — Trump/Musk active-user base rate 85-90%
- Rule 34: Corporate partnership — "in talks"/"exploring" ≠ signed deal
- Rule 35: FOMC / rate decision — CME FedWatch is ground truth; deviate only if ≥10pp from FedWatch
- Rule 36: Index inclusion — ~50% for eligible companies; only official S&P DJ press release is HIGH
- Rule 37: Crypto network upgrades — testnet-cleared 85-90%, contentious forks 50%
- Rule 38: Secondary offerings (shelf 75-80%, priced deal 90%+) and credit ratings (CreditWatch Neg 70%)
- Rule 39: OPEC meetings — only post-meeting communiqué is HIGH; chip export — only Federal Register rule is HIGH
- Min-hours-to-close filter: `min_hours_to_close: 6` excludes markets closing within 6h
- Actionability filter, vol_spike / price_jump pre-sort signals
- `analysis/calibration.py`: whale, watchlist, PASS-rate breakdown sections

New heuristic categories: stock buyback/dividend (0.40), treaty withdrawal (0.20), formal candidacy (0.35), martial law (0.05), social media post (0.75), corporate partnership (0.35), S&P 500 inclusion (0.50), event attendance (0.65), BRICS/SCO membership (0.30), quantitative easing/tightening (0.40), celebrity civil cases (0.45), recall election (0.15), water crisis/drought (0.30), municipal bankruptcy (0.10), OPEC production decision (0.40), semiconductor/chip export restriction (0.45), filibuster reform (0.10), housing permits/starts (0.50), blockchain/crypto protocol upgrade (0.65), secondary equity offering (0.35), credit rating change (0.40), CBDC adoption (0.15), short seller report (0.30), Iran/geopolitical: uranium enrichment (0.20), regime change (0.10).

CBDC false-positive fix: removed "digital currency" from CBDC block (was false-matching crypto market cap).

---

## Session 15 — Heuristic labels, LV specificity bonus, DB persistence (2026-06-18)

**Test count:** 1081 → 1158

**Commit 63 — Heuristic labels + 5 new categories + Rules 40-42**
`scanner.get_heuristic_label(market)` — ~200 rules mapping title patterns to human-readable category strings ("PDUFA date", "crypto ETF", etc.). First-match wins.
`score_market()` returns `heuristic_label`.
`build_prompt()` FLAG REASON now includes `[heuristic label]` suffix.
New categories: veto (0.20), tax legislation (0.35), supply chain disruption (0.30), EV adoption milestone (0.45), bond/debt issuance (0.65), unionization/NLRB vote (0.40).
Rules 40-42: unionization elections, tax legislation (reconciliation/TCJA extension), bond/debt issuance.
76 new tests.

**Commit 64 — LV score heuristic specificity bonus**
`report.compute_leviathan_score()`: +8 for HIGH_SPEC labels (PDUFA date, government shutdown avoided, FDA clinical hold, constitutional amendment, NATO Article 5, martial law, volcanic eruption, 25th Amendment), +4 for MED_SPEC labels (crypto protocol upgrade, debt ceiling resolution, cabinet departure, CEO retention, credit rating change, OPEC production decision, chip export restriction, bond/debt issuance, FDA complete response letter).
8 new tests.

**Commit 65 — heuristic_label DB persistence + `get_stats_by_heuristic_label()`**
`logger._init_db()`: ADD COLUMN `heuristic_label TEXT` (additive migration).
`log_signal()` and `log_pass()` persist `heuristic_label`.
`get_stats_by_heuristic_label()`: win rate / P&L / avg edge grouped by label (resolved paper signals only, sorted by win_rate desc).
`analysis/calibration.py`: BY HEURISTIC LABEL section.
10 new tests.

---

## Goal 2c — Resolution-first validation harness (2026-06-18)

**Branch:** `resolve-first-3a`

### PART A — Resolution timeline finding

**DB state at audit (2026-06-18):** 62 total rows. Resolved: 4 (all NO). Pending: 58.

All 58 pending rows have MISSING `close_time` — column was added in Session 13 but never backfilled. Inferred distribution: ~25 close Jul 2026, ~1 Oct 2026, ~7 Jan 2027, ~21 are 2028+ (research probes — elections, policy, geopolitics).

**Conclusion:** Existing data cannot validate the model on a useful timescale. Research-probe rows are years from resolving. This goal breaks the cycle by logging new near-dated rows with `close_time` explicitly stored.

### PART B — Near-dated paper batch (`analysis/resolve_first.py`)

Config keys: `resolve_first_max_days` (14), `resolve_first_dedup_days` (7).

**2026-06-18 run:** 2 near-dated candidates at ≤14d (both in 5-15% price band). Both already logged within dedup window — 0 new rows logged. Re-runnable without double-logging.

### PART C — Resolution status (2026-06-18)

Paper rows: 25 | Resolved: 2 | Pending: 23 | Closing ≤14d: 1
COUNTDOWN: 2 resolved / 20 needed before win-rate is meaningful.

### PART D — Fee + payoff assumption

Binary payoff fix live in `logger.resolve_outcomes`. OPEN: Kalshi `fee_cost` is per-fill-event, not per-contract. If `fill_count=1` understates actual contract count, fee_per_unit is overstated. Verify against Kalshi fee schedule before drawing real P&L conclusions.

### PART E — Tests

19 new tests in `tests/test_resolve_first.py` (1158 → 1208 total). Key assertions: select_near_dated only picks two-sided-book markets; re-running does not double-log; logged rows carry `close_time`; `print_resolution_status` refuses to show win-rate % when resolved < 10.

---

## Goal 2d — Winning-wallet selection criterion fix (2026-06-18)

**Branch:** `smart-money-fix-3b`

### PART A — Before state

Cache: 0 cached winners. Prior cache showed 3 winners — all with `resolved_count: 0, win_rate: null`, qualifying on unrealised P&L of open positions (World Cup player props, Bitcoin 5-minute Up/Down contracts). None would survive `resolved_count >= 10`.

### PART B — Fix applied (`accounts.py`)

Three targeted changes:
1. `_score_wallet`: sports-game exclusion added alongside existing coinflip patterns. Lazy import: `from analysis.smart_money_scan import _is_sports_title`.
2. `_score_wallet` + `_is_winner`: replaced `avg_pct_pnl` / `total_cash_pnl` (all-positions unrealised) with `resolved_avg_pct_pnl` / `resolved_cash_pnl`. `_is_winner` gates on resolved metrics only.
3. `discover_winners`: sort key changed to `(win_rate, resolved_cash_pnl)`. Primary: resolved win rate. Secondary: realized cash P&L on resolved positions.

Config thresholds: `min_resolved_count: 10`, `min_win_rate: 55.0%`, `min_cash_pnl: 100.0`, `min_pct_pnl: 10.0%` (applied to resolved positions only).

### PART C — After state

NO VERIFIED SMART MONEY — 0 wallets meet `resolved_count >= 10`. `winning_accounts.json` rewritten empty. Watchlist signals should be treated as informational-only until verified wallets appear.

### PART D — Tests

14 new tests in `tests/test_accounts.py` (1203 total on branch, 0 fail). Key assertions: wallet with 20 open positions at +500% does NOT qualify; sports-game resolved positions excluded; 12 real resolved positions at 75% win rate DOES qualify; ranking by resolved win rate beats all-position P&L.

---

## Goal 3c — CSV export module for Power BI (2026-06-19)

**New file:** `export_to_csv.py`
- `export_csvs(db_path, export_dir)` reads `signals` and `runs` tables via stdlib `sqlite3` + `csv`
- Writes `data/powerbi_export/signals.csv` and `data/powerbi_export/runs.csv`
- Handles missing DB gracefully; returns `{"signals": N, "runs": N}`

**`main.py` hook:** Two-line try/except after `logger.log_run()`. Pipeline continues on export failure.

**First live export:** 63 signal rows, 17 run rows.

**Power BI refresh:** After each run, open Power BI Desktop → Home > Refresh.

**Tests:** 9 new tests in `tests/test_export_to_csv.py` (1231 total, 0 fail).

---

## Goal 3d — NULL-to-empty string fix for Power BI (2026-06-20)

**Commit:** `459c440`

DAX expressions like `= ""` and `= "LOSS"` fail on `NULL` values in Power BI. Fix: `_STRING_COLS` frozenset identifies string-typed columns. `_null_to_empty()` converts `None → ""` only in those columns; numeric columns left as-is.

Confirmed on real DB: 59 empty strings and 4 LOSS values in `result` column with zero NaN.

**Tests:** 11 new tests in `tests/test_export_to_csv.py`.

---

## Goal 3e — 5 report improvements (2026-06-21)

**Commit:** `b3b79e9`

1. **`_smart_money_section(show_detail=False)`** — omits per-trader cross-refs and largest positions when no qualifying signals fire. Reduces noise on quiet days.

2. **Sports bets filtered from Largest Open Positions** via `_is_sports_title()`; shows note when <3 non-sports remain; capped at 8 rows.

3. **"Next resolution: YYYY-MM-DD (N days)"** in report header when unresolved paper signals have `close_time` stored. Backed by new `logger.get_next_resolution_date()`.

4. **UPCOMING RESOLUTIONS section** (14-day window) after WHALE ACTIVITY. Uses `logger.get_upcoming_resolutions(days=14)`. Shows placeholder when empty.

5. **Truncation and formatting:** tickers (28 chars), titles (42+...), trader names (18), Kalshi titles (45). Kalshi Targets capped at 15 rows. Duplicate footer removed. Weekly digest title 35 chars.

**Tests:** Many new tests added to `test_report.py` (large block covering all 5 changes).

---

## Goal 3f — Column whitelist + computed columns at export (2026-06-21)

**Commit:** `80bd251`

Only analysis-relevant columns written to `signals.csv`; pipeline plumbing (`run_id`, `outcome`, `fill_count`, `fill_fee`, `from_signal`, etc.) dropped at export time. DB is never touched.

**`WHITELIST`** (30 columns including computed): `call_id`, `date`, `ticker`, `title`, `source`, `direction`, `confidence`, `confidence_rank`, `flag_path`, `time_horizon`, `horizon_rank`, `market_price`, `edge`, `net_edge`, `base_rate`, `result`, `is_resolved`, `is_win`, `pnl_if_traded`, `pnl_scaled`, `leviathan_score`, `lv_band`, `close_time`, `sig_edge`, `sig_drift`, `sig_br_none`, `watchlist_signal`, `whale_detected`, `heuristic_label`, `short_horizon`.

**`_add_computed_cols(row)`** derives 7 columns at export time: `is_resolved` (0/1), `is_win` (0/1), `confidence_rank` (0/1/2), `horizon_rank` (0–4), `date` (timestamp prefix), `pnl_scaled` (×10 for per-$10 notional), `lv_band` (score bucket label).

**Tests:** Many new tests in `tests/test_export_to_csv.py`.

---

## Goal 3g — Export hardening: sentinel cleanup + validation report (2026-06-21)

**Commit:** `fad6802`

5 targeted fixes:
1. All string columns strip `None`/`"None"`/`"nan"`/`"NaT"` sentinels (not just `None`)
2. `is_win` is `None` (blank in CSV) for unresolved rows — Power BI `SUM()` ignores them; only 0/1 for resolved
3. `lv_band` emits `"Unscored"` when `leviathan_score` is NULL so charts show a readable label
4. `confidence_rank` and `horizon_rank` default to `0` (not blank) when source column is empty
5. Post-export validation report: row counts, win/loss split, win rate, net P&L, >50% blank-rate warnings per column

**Tests:** Large new block in `tests/test_export_to_csv.py`.

**Bugfix follow-up (2026-06-23, commit `06f0392`):** `real_fill` rows store order side (`BUY`/`SELL`) in the `confidence` column. Guard in `_add_computed_cols` blanks any value not in `{HIGH, MED, LOW}` so Power BI slicers aren't polluted.

---

## Goal 3h — Machine-readable backlog with CLI and tests (2026-06-23)

**Commit:** `6790118`

**`backlog.json`** (21 items at creation): structured backlog with `trigger` gates (`{"all": [{"metric": ..., "op": ">=", "value": N}]}`), `depends_on` dependency graph, and `metrics_glossary` defining the 4 live metrics.

**`backlog.py`** importable engine:
- `parse_trigger(s)` — parses `"metric>=N,metric2>=M"` to trigger dict; `"manual"` or `""` → `{"all": []}`
- `determine_status(trigger, unmet_deps)` → `"ready"` / `"locked"` / `"blocked"`
- `validate_item(item, others, glossary)` → list of error strings
- `load_backlog(path)` / `save_backlog(path, data)`
- CLI subcommands: `status` (prints ready/locked/blocked/done tables), `add` (validates and appends)

**`tests/test_backlog.py`:** 22 tests covering structure, parse_trigger, determine_status, add subcommand (valid/duplicate/bad-area/bad-metric/missing-dep all checked), and status exit code.

---

## Goal 3i — Weekly backlog checker with metrics engine (2026-06-23)

**Commit:** `fcbfadd`

**`backlog_checker.py`:**
- Computes 4 live metrics from `leviathan.db`: `resolved_count`, `resolved_count_per_category_max`, `resolved_count_per_wallet_max`, `fills_count`
- Evaluates locked item triggers against live metrics
- CLI prompt mode: C (complete) / M (mark done) / S (skip) per item
- `--email` mode: formats a BACKLOG block for weekly digest with no writes to `backlog.json`
- Regenerates `BACKLOG.md` on every run
- 22 `execute_action` stubs for future automation

**`tests/test_backlog_checker.py`:** 13 tests covering metric computation, trigger evaluation, email block formatting, and BACKLOG.md generation.

---

## Goal 3j — Complete all 8 ready backlog items (2026-06-23)

**Commit:** `eada564` | **Result:** 1331 tests, 0 failures

8 items completed in one commit:

1. **trade-reconciliation**: DB confirmed at 13 `real_fill` rows; marked done.

2. **realfill-dedup**: Guard in `export_to_csv.py` warns on duplicate pending `real_fill` tickers. 2 tests.

3. **sample-size-gates + wilson-intervals**: `_wilson_ci()` added to `report.py`. Wilson score confidence intervals shown in daily/weekly track record and probe block (`p ± X.Xpp @ 95% CI [lo, hi]`). 6 tests.

4. **title-scraping-fix**: `fetch_market_with_retry()` in `kalshi.py` — 2s retry on blank/fallback titles. Audited DB: 13 rows had `title=ticker` (blank at ingest time).

5. **smart-money-drift-alerts**: `_parse_sm_snapshot()` and SMART MONEY DRIFT block in `report.py` — compares yesterday's vs today's `.md` snapshot to detect wallet position changes.

6. **backtest-harness**: `backtest.py` with `BacktestRunner` class (`load`, `match`, `stats`, `report`); `sample_resolutions.csv`. 15 tests.

7. **empirical-base-rates-poly**: `base_rates.py` with `BASE_RATES` dict, `load_empirical_rates()`, `merge_rates()` (shrinkage-lite blending of empirical and heuristic rates), CLI. 9 tests.

8. **backlog.json**: All 8 ready items set to `"status": "done"`. `backlog.py status` extended to show Done bucket.

---

## Goal 4a — High-price filter, EV/contract display, betting queue (2026-06-23)

**Commit:** `7d22881` | **Result:** 1342 tests, 0 failures (current)

### High-price filter (`core/scanner.py`)

`apply_high_price_filter(markets)` removes markets where `mid_price >= 0.85`. Returns `(kept_list, filtered_count)`.

`score_markets()` now returns `tuple[list[dict], int]` — callers must unpack: `scored, hp_filtered = scanner.score_markets(...)`.

Markets at ≥0.85 have near-certain implied probability; risk/reward makes them unattractive regardless of edge. Printed as `[FILTERED] ticker — market price XX% above 0.85 threshold, low return potential`.

### EV per contract (`core/report.py`)

`_ev_per_contract(direction, market_price, estimate)` — per-$10 notional contract:
- YES: `(estimate − market_price) × 10`
- NO:  `(market_price − estimate) × 10`
- Returns `"$+X.XX"` or `None` if inputs missing/invalid

Shown in signal blocks and TOP PICKS summary. Example: market at 16.5¢, estimate 35% YES → EV $+1.85/contract.

### Betting queue (`core/report.py`)

`_betting_queue(db_path, top_n=5)` — reads pending paper signals via SQLite read-only URI (`file:{db_path}?mode=ro`):
- Excludes tickers that have a matching `real_fill` row (already placed)
- Excludes markets with `market_price >= 0.85`
- Urgency: `(edge × 0.6) + (1/days_to_close × 0.4)` (days from `close_time`)
- Shows top 5 by urgency: ticker, direction, edge, market price, days to close

Inserted in `compile_report` after TOP PICKS, before NEW SIGNALS. `compile_report` signature gains `db_path=None`.

### RUN STATISTICS update

Added `Filtered (high price): N` line to the RUN STATISTICS block. `run_meta["high_price_filtered"]` populated from `score_markets()` tuple.

### `main.py` wiring

- `scored_markets, hp_filtered = scanner.score_markets(filtered, config)`
- `run_meta["high_price_filtered"] = hp_filtered`
- Both `compile_report` calls pass `db_path=logger.DB_PATH`

### Tests

11 new tests in Part D of `tests/test_report.py`:
- High-price filter at 0.90 is filtered; at 0.84 passes through; None passes with warning
- EV YES and NO calculations verified to cent
- EV shown in signal block and top picks
- Betting queue excludes real_fill tickers
- Betting queue sorts by urgency
- Betting queue shows max 5
- RUN STATISTICS contains filtered high-price count

2 existing `test_scanner.py` tests updated: `results, _ = scanner.score_markets(...)` tuple unpack.

---

## Smart money daily scans

`scripts/daily_smart_money.py` runs automatically, fetching Polymarket positions for 20 watchlist traders, cross-referencing to Kalshi markets by title similarity, and committing results to `data/smart_money/YYYY-MM-DD.md`.

| Date | Positions | Kalshi signals |
|------|-----------|----------------|
| 2026-06-20 | 369 | 15 |
| 2026-06-21 | 400 | 15 |
| 2026-06-22 | 433 | 16 |
| 2026-06-23 | 416 | 16 |
| 2026-06-24 | 458 | 11 |
| 2026-06-25 | 477 | 12 |
| 2026-06-26 | 390 | 12 |
| 2026-06-27 | 334 | 13 |
| 2026-06-28 | 255 | 13 |
| 2026-06-29 | 323 | 14 |

---

## Goal 4b — EV Floor, Watchlist Gating, Fee-Aware Edge  (2026-06-29)

### Files changed
- **config.json** — added `betting.unit_size=10` and `betting.min_ev_pct_of_unit=0.50` block; added `watchlist_note` clarifying monthly_pnl values are human-reference only
- **core/fees.py** — new file; `kalshi_fee(price, contracts)` using the Kalshi variance formula: `ceil(0.07 * p * (1-p) * contracts * 100) / 100`
- **core/scanner.py** — computes `net_edge_after_fee` for every scored market
- **main.py** — computes `ev_after_fee_per_contract` after Claude direction is known
- **core/logger.py** — additive migration adds `net_edge_after_fee REAL` and `ev_after_fee_per_contract REAL` columns
- **core/report.py** — `_ev_float`/`_ev_per_contract` accept `unit_size`; `_betting_queue` applies hard EV floor filter (not sort); shows filtered count footer
- **analysis/backtest.py** — fully threaded with `unit_size` from config
- **analysis/calibration.py** — all 9 `_print_table()` calls and heuristic label section updated with `unit_size`
- **analysis/smart_money_scan.py** — `_verify_watchlist_trader()` helper gates each watchlist address through `accounts._score_wallet` + `accounts._is_winner`; cache invalidated when `verified` field absent
- **tests/test_4b.py** — new; 41 tests for PART A/B/C

### Findings: PART A — EV floor historical analysis
- DB as of 2026-06-29: 31 signals with a direction and valid prices
- **2 signals (6.5%)** clear the $5.00 EV floor after fees (50% of $10/unit)
- **29 signals (93.5%)** would be filtered
- Most historical signals had 10-30pp edge producing $1-3 EV/contract — below the $5 floor. The floor is intentionally strict: signals need ~60pp+ edge at mid-prices to clear it after the Kalshi fee haircut.

### Findings: PART B — watchlist gate
- All 20 watchlist traders require passing 5 gates: min_resolved_count, min_win_rate, min_positions, min_pct_pnl, min_cash_pnl
- Dry-run proof: trader with 3 resolved positions is EXCLUDED ("only 3 resolved positions (need >=10)"); trader with 12 resolved positions at 100% win rate is VERIFIED
- Non-verified traders cannot set `watchlist_signal=True` or influence Kalshi signal promotion

### Findings: PART C — fee haircut magnitude
- At p=0.50 (max variance): fee = $0.18 per 10 contracts (1.8% of unit)
- At p=0.30: fee = $0.15; at p=0.10: fee = $0.07
- Example signal at mp=0.69: EV before fee = $+1.95, fee = $0.15, EV after = $+1.80 (7.7% reduction)
- Fee impact is modest in isolation but decisive against a $5 floor on thin-edge signals

### Scope confirmation
- No changes to scorer.py or backlog.json
- No new heuristics, calibration rules, signals, or analytics beyond Goal 4b spec

### Next steps (v2 backlog)
1. Auto-resolve outcomes when Kalshi markets close (update `result` + `pnl_if_traded` automatically)
2. Run `_verify_watchlist_trader` against live Polymarket API to establish which of the 20 seeded traders actually pass the track-record gate
3. Consider a tiered EV floor (e.g., 30% of unit for HIGH confidence, 50% for MED/LOW) once there are enough resolved signals to calibrate empirically
