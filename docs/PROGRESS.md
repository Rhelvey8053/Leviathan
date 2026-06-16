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
