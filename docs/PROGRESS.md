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
