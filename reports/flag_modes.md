# Flag Mode Comparison — Leviathan v1

**Snapshot:** 2026-08-02T18:52:13.885486+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2922  
**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  
**Drift thresholds (config):** abs>0.03, pct>7% (see grid below)  

Filter stage is identical across all modes. Markets surviving filter: **30**

## Signal Presence (mode-independent)

These signal counts reflect which signals FIRED across all filtered markets, independent of which mode is active and independent of branch evaluation order. They are identical under every mode — attribution no longer depends on ordering.

| Signal | Markets firing | % of filtered |
|--------|---------------|---------------|
| `sig_edge` (raw_edge > 0.08) | 16 | 53% |
| `sig_drift` (abs+pct drift thresholds) | 10 | 33% |
| `sig_br_none` (no heuristic match) | 7 | 23% |
| `sig_edge` AND `sig_drift` (both present) | 6 | 20% |

> **Attribution bug (now fixed):** Under `passthrough`, BR_NONE was checked before DRIFT so markets with both signals were labelled BR_NONE and DRIFT appeared as 0. The `sig_*` fields above show the true fire rates regardless of mode.

## Flag Path by Mode (how each mode uses the signals)

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 30 | 25 | 83.3% | 16 | 7 | 2 | 0 |
| `strict_anomaly_only` | 30 | 10 | 33.3% | 0 | 0 | 10 | 0 |
| `strict_with_heuristic` | 30 | 20 | 66.7% | 0 | 0 | 10 | 10 |

Under `passthrough`, 7 markets are labelled BR_NONE and the DRIFT branch is never reached — but `sig_drift` shows 10 of those markets actually have a drift signal present. Passthrough was masking drift by flagging via BR_NONE first.

## Drift Signal Diagnosis (by price bucket)

Root cause of the 86% drift-fire rate: `compute_drift_signal` previously required only `pct > 5%`. A 0.5-cent absolute move at a 5-cent price is a 10% percentage drift — qualifying as a signal despite being bid/ask noise. The table below shows fire rates and average moves bucketed by price level.

| Price bucket | N | Drift% (abs>0.03, pct>7%) | Avg abs move | Avg pct move |
|-------------|---|----------------|-------------|-------------|
| Low [0.05-0.15) | 19 | 37% | 0.0317 | 0.913 |
| MidLo [0.15-0.35) | 5 | 40% | 0.0650 | 0.197 |
| Mid [0.35-0.65) | 5 | 20% | 0.0840 | 0.102 |
| High [0.65-0.95] | 1 | 0% | 0.0400 | 0.046 |

Low-price markets fire at 100% because small absolute moves (0.5-1.5 cents) are large relative percentages. The fix requires BOTH `abs_drift > drift_min_abs` AND `pct_drift > drift_min_pct` — eliminating cent-level noise at low prices.

## Drift Threshold Sweep (% of filtered markets flagging as drift)

Grid of `drift_min_abs` x `drift_min_pct` combinations. Values show what percentage of the 30 filtered markets would have `drift_flag=True` under each combination. Config baseline (abs>0.03, pct>7%) = **33%**.

| drift_min_abs | pct>5% | pct>7% | pct>10% | pct>15% | pct>20% |
|---|---|---|---|---|---|
| abs>0.01 | 22/30 (73%) | 21/30 (70%) | 21/30 (70%) | 18/30 (60%) | 17/30 (57%) |
| abs>0.02 | 20/30 (67%) | 19/30 (63%) | 19/30 (63%) | 18/30 (60%) | 17/30 (57%) |
| abs>0.03 | 11/30 (37%) | 10/30 (33%) | 10/30 (33%) | 10/30 (33%) | 10/30 (33%) |
| abs>0.04 | 8/30 (27%) | 8/30 (27%) | 8/30 (27%) | 8/30 (27%) | 8/30 (27%) |
| abs>0.05 | 5/30 (17%) | 5/30 (17%) | 5/30 (17%) | 5/30 (17%) | 5/30 (17%) |

> **Config keys:** `markets.drift_min_abs` and `markets.drift_min_pct` — currently at `0.03` / `0.07`. Adjust these to move diagonally in the grid above to reduce noise.

**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to ~11 drift flags by eliminating sub-cent moves, while keeping markets with a genuine price dislocation (3+ cent absolute move). The (0.03, 0.10) cell is the next step if 11 is still too many.

## Verdict

**Recommended mode: `strict_with_heuristic`**

Config baseline (abs>0.03, pct>7%) flags 10/30 markets as drift. Combined with strict_with_heuristic (no BR_NONE noise), expected candidates: ~10 drift + 16 heuristic-edge (with overlap possible).

At config thresholds (abs>0.03, pct>7%), drift flags 10/30 filtered markets (33%). `strict_with_heuristic` mode removes the BR_NONE catch-all and surfaces only markets with genuine heuristic edge or price drift.

> **Note:** This comparison measures candidate *volume and selectivity* only. Signal *correctness* — whether flagged markets are actually mispriced — cannot be judged until markets resolve and outcomes are logged.