# Flag Mode Comparison — Leviathan v1

**Snapshot:** 2026-06-16T04:38:36.075689+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2428  
**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  
**Drift thresholds:** abs>0.0, pct>0.05 (provisional — see grid below)  

Filter stage is identical across all modes. Markets surviving filter: **21**

## Signal Presence (mode-independent)

These signal counts reflect which signals FIRED across all filtered markets, independent of which mode is active and independent of branch evaluation order. They are identical under every mode — attribution no longer depends on ordering.

| Signal | Markets firing | % of filtered |
|--------|---------------|---------------|
| `sig_edge` (raw_edge > 0.08) | 7 | 33% |
| `sig_drift` (abs+pct drift thresholds) | 18 | 86% |
| `sig_br_none` (no heuristic match) | 14 | 67% |
| `sig_edge` AND `sig_drift` (both present) | 7 | 33% |

> **Attribution bug (now fixed):** Under `passthrough`, BR_NONE was checked before DRIFT so markets with both signals were labelled BR_NONE and DRIFT appeared as 0. The `sig_*` fields above show the true fire rates regardless of mode.

## Flag Path by Mode (how each mode uses the signals)

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 21 | 21 | 100.0% | 7 | 14 | 0 | 0 |
| `strict_anomaly_only` | 21 | 18 | 85.7% | 0 | 0 | 18 | 0 |
| `strict_with_heuristic` | 21 | 18 | 85.7% | 0 | 0 | 18 | 0 |

Under `passthrough`, 14 markets are labelled BR_NONE and the DRIFT branch is never reached — but `sig_drift` shows 18 of those markets actually have a drift signal present. Passthrough was masking drift by flagging via BR_NONE first.

## Drift Signal Diagnosis (by price bucket)

Root cause of the 86% drift-fire rate: `compute_drift_signal` previously required only `pct > 5%`. A 0.5-cent absolute move at a 5-cent price is a 10% percentage drift — qualifying as a signal despite being bid/ask noise. The table below shows fire rates and average moves bucketed by price level.

| Price bucket | N | Drift% (pct>5%) | Avg abs move | Avg pct move |
|-------------|---|----------------|-------------|-------------|
| Low [0.05-0.15) | 11 | 100% | 0.0273 | 0.426 |
| MidLo [0.15-0.35) | 3 | 67% | 0.0283 | 0.095 |
| Mid [0.35-0.65) | 5 | 80% | 0.0340 | 0.077 |
| High [0.65-0.95] | 2 | 50% | 0.0550 | 0.071 |

Low-price markets fire at 100% because small absolute moves (0.5-1.5 cents) are large relative percentages. The fix requires BOTH `abs_drift > drift_min_abs` AND `pct_drift > drift_min_pct` — eliminating cent-level noise at low prices.

## Drift Threshold Sweep (% of filtered markets flagging as drift)

Grid of `drift_min_abs` x `drift_min_pct` combinations. Values show what percentage of the 21 filtered markets would have `drift_flag=True` under each combination. Current behavior (abs>0.0, pct>5%) = **86%** (top-left reference).

| drift_min_abs | pct>5% | pct>7% | pct>10% | pct>15% | pct>20% |
|---|---|---|---|---|---|
| abs>0.01 | 15/21 (71%) | 15/21 (71%) | 12/21 (57%) | 8/21 (38%) | 8/21 (38%) |
| abs>0.02 | 14/21 (67%) | 14/21 (67%) | 11/21 (52%) | 7/21 (33%) | 7/21 (33%) |
| abs>0.03 | 11/21 (52%) | 11/21 (52%) | 8/21 (38%) | 5/21 (24%) | 5/21 (24%) |
| abs>0.04 | 8/21 (38%) | 8/21 (38%) | 5/21 (24%) | 3/21 (14%) | 3/21 (14%) |
| abs>0.05 | 2/21 (10%) | 2/21 (10%) | 1/21 (5%) | 0/21 (0%) | 0/21 (0%) |

> **Config keys:** `markets.drift_min_abs` and `markets.drift_min_pct` — currently at `0.0` / `0.05` (current-behavior defaults, not yet calibrated). Reed picks the target cell from the grid above.

**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to ~11 drift flags by eliminating sub-cent moves, while keeping markets with a genuine price dislocation (3+ cent absolute move). The (0.03, 0.10) cell is the next step if 11 is still too many.

## Verdict

**Recommended mode: `strict_with_heuristic`**

At recommended drift calibration (abs>0.03, pct>5%), drift would flag 11/21 markets instead of 18/21. Combined with strict_with_heuristic (no BR_NONE noise), expected candidates: ~11 drift + 7 heuristic-edge (with overlap possible).

No flag_mode is truly selective yet because drift is still at current-behavior defaults (abs>0.0, pct>5%), which fires on 86% of filtered markets. Drift becomes selective only after Reed sets `drift_min_abs` from the grid above. Until then, `strict_with_heuristic` at least removes the BR_NONE catch-all noise.

> **Note:** This comparison measures candidate *volume and selectivity* only. Signal *correctness* — whether flagged markets are actually mispriced — cannot be judged until markets resolve and outcomes are logged.