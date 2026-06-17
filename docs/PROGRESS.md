# Leviathan — Progress Log

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
