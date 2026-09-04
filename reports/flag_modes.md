# Flag Mode Comparison — Leviathan v1

**Snapshot:** 2026-09-03T15:34:31.678481+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 3048  
**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  
**Drift thresholds (config):** abs>0.03, pct>7% (see grid below)  

Filter stage is identical across all modes. Markets surviving filter: **66**

## Signal Presence (mode-independent)

These signal counts reflect which signals FIRED across all filtered markets, independent of which mode is active and independent of branch evaluation order. They are identical under every mode — attribution no longer depends on ordering.

| Signal | Markets firing | % of filtered |
|--------|---------------|---------------|
| `sig_edge` (raw_edge > 0.08) | 14 | 21% |
| `sig_drift` (abs+pct drift thresholds) | 11 | 17% |
| `sig_br_none` (no heuristic match) | 43 | 65% |
| `sig_edge` AND `sig_drift` (both present) | 4 | 6% |

> **Attribution bug (now fixed):** Under `passthrough`, BR_NONE was checked before DRIFT so markets with both signals were labelled BR_NONE and DRIFT appeared as 0. The `sig_*` fields above show the true fire rates regardless of mode.

## Flag Path by Mode (how each mode uses the signals)

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 66 | 55 | 83.3% | 12 | 43 | 0 | 0 |
| `strict_anomaly_only` | 66 | 11 | 16.7% | 0 | 0 | 11 | 0 |
| `strict_with_heuristic` | 66 | 19 | 28.8% | 0 | 0 | 11 | 8 |

Under `passthrough`, 43 markets are labelled BR_NONE and the DRIFT branch is never reached — but `sig_drift` shows 11 of those markets actually have a drift signal present. Passthrough was masking drift by flagging via BR_NONE first.

## Drift Signal Diagnosis (by price bucket)

Root cause of the 86% drift-fire rate: `compute_drift_signal` previously required only `pct > 5%`. A 0.5-cent absolute move at a 5-cent price is a 10% percentage drift — qualifying as a signal despite being bid/ask noise. The table below shows fire rates and average moves bucketed by price level.

| Price bucket | N | Drift% (abs>0.03, pct>7%) | Avg abs move | Avg pct move |
|-------------|---|----------------|-------------|-------------|
| Low [0.05-0.15) | 26 | 19% | 0.0174 | 0.173 |
| MidLo [0.15-0.35) | 17 | 12% | 0.0265 | 0.091 |
| Mid [0.35-0.65) | 10 | 20% | 0.0310 | 0.108 |
| High [0.65-0.95] | 13 | 15% | 0.0277 | 0.039 |

Low-price markets fire at 100% because small absolute moves (0.5-1.5 cents) are large relative percentages. The fix requires BOTH `abs_drift > drift_min_abs` AND `pct_drift > drift_min_pct` — eliminating cent-level noise at low prices.

## Drift Threshold Sweep (% of filtered markets flagging as drift)

Grid of `drift_min_abs` x `drift_min_pct` combinations. Values show what percentage of the 66 filtered markets would have `drift_flag=True` under each combination. Config baseline (abs>0.03, pct>7%) = **17%**.

| drift_min_abs | pct>5% | pct>7% | pct>10% | pct>15% | pct>20% |
|---|---|---|---|---|---|
| abs>0.01 | 29/66 (44%) | 25/66 (38%) | 22/66 (33%) | 16/66 (24%) | 8/66 (12%) |
| abs>0.02 | 18/66 (27%) | 17/66 (26%) | 15/66 (23%) | 12/66 (18%) | 8/66 (12%) |
| abs>0.03 | 12/66 (18%) | 11/66 (17%) | 10/66 (15%) | 8/66 (12%) | 7/66 (11%) |
| abs>0.04 | 9/66 (14%) | 8/66 (12%) | 7/66 (11%) | 5/66 (8%) | 4/66 (6%) |
| abs>0.05 | 6/66 (9%) | 6/66 (9%) | 6/66 (9%) | 4/66 (6%) | 3/66 (5%) |

> **Config keys:** `markets.drift_min_abs` and `markets.drift_min_pct` — currently at `0.03` / `0.07`. Adjust these to move diagonally in the grid above to reduce noise.

**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to ~11 drift flags by eliminating sub-cent moves, while keeping markets with a genuine price dislocation (3+ cent absolute move). The (0.03, 0.10) cell is the next step if 11 is still too many.

## Verdict

**Recommended mode: `strict_with_heuristic`**

Config baseline (abs>0.03, pct>7%) flags 11/66 markets as drift. Combined with strict_with_heuristic (no BR_NONE noise), expected candidates: ~11 drift + 14 heuristic-edge (with overlap possible).

At config thresholds (abs>0.03, pct>7%), drift flags 11/66 filtered markets (17%). `strict_with_heuristic` mode removes the BR_NONE catch-all and surfaces only markets with genuine heuristic edge or price drift.

> **Note:** This comparison measures candidate *volume and selectivity* only. Signal *correctness* — whether flagged markets are actually mispriced — cannot be judged until markets resolve and outcomes are logged.