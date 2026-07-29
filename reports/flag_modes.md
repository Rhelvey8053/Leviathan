# Flag Mode Comparison — Leviathan v1

**Snapshot:** 2026-07-29T17:01:17.110868+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2493  
**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  
**Drift thresholds (config):** abs>0.03, pct>7% (see grid below)  

Filter stage is identical across all modes. Markets surviving filter: **28**

## Signal Presence (mode-independent)

These signal counts reflect which signals FIRED across all filtered markets, independent of which mode is active and independent of branch evaluation order. They are identical under every mode — attribution no longer depends on ordering.

| Signal | Markets firing | % of filtered |
|--------|---------------|---------------|
| `sig_edge` (raw_edge > 0.08) | 19 | 68% |
| `sig_drift` (abs+pct drift thresholds) | 12 | 43% |
| `sig_br_none` (no heuristic match) | 6 | 21% |
| `sig_edge` AND `sig_drift` (both present) | 8 | 29% |

> **Attribution bug (now fixed):** Under `passthrough`, BR_NONE was checked before DRIFT so markets with both signals were labelled BR_NONE and DRIFT appeared as 0. The `sig_*` fields above show the true fire rates regardless of mode.

## Flag Path by Mode (how each mode uses the signals)

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 28 | 27 | 96.4% | 19 | 6 | 2 | 0 |
| `strict_anomaly_only` | 28 | 12 | 42.9% | 0 | 0 | 12 | 0 |
| `strict_with_heuristic` | 28 | 23 | 82.1% | 0 | 0 | 12 | 11 |

Under `passthrough`, 6 markets are labelled BR_NONE and the DRIFT branch is never reached — but `sig_drift` shows 12 of those markets actually have a drift signal present. Passthrough was masking drift by flagging via BR_NONE first.

## Drift Signal Diagnosis (by price bucket)

Root cause of the 86% drift-fire rate: `compute_drift_signal` previously required only `pct > 5%`. A 0.5-cent absolute move at a 5-cent price is a 10% percentage drift — qualifying as a signal despite being bid/ask noise. The table below shows fire rates and average moves bucketed by price level.

| Price bucket | N | Drift% (abs>0.03, pct>7%) | Avg abs move | Avg pct move |
|-------------|---|----------------|-------------|-------------|
| Low [0.05-0.15) | 12 | 25% | 0.0212 | 0.220 |
| MidLo [0.15-0.35) | 10 | 60% | 0.0590 | 0.418 |
| Mid [0.35-0.65) | 5 | 60% | 0.0490 | 0.085 |
| High [0.65-0.95] | 1 | 0% | 0.0150 | 0.020 |

Low-price markets fire at 100% because small absolute moves (0.5-1.5 cents) are large relative percentages. The fix requires BOTH `abs_drift > drift_min_abs` AND `pct_drift > drift_min_pct` — eliminating cent-level noise at low prices.

## Drift Threshold Sweep (% of filtered markets flagging as drift)

Grid of `drift_min_abs` x `drift_min_pct` combinations. Values show what percentage of the 28 filtered markets would have `drift_flag=True` under each combination. Config baseline (abs>0.03, pct>7%) = **43%**.

| drift_min_abs | pct>5% | pct>7% | pct>10% | pct>15% | pct>20% |
|---|---|---|---|---|---|
| abs>0.01 | 22/28 (79%) | 20/28 (71%) | 18/28 (64%) | 14/28 (50%) | 10/28 (36%) |
| abs>0.02 | 16/28 (57%) | 14/28 (50%) | 13/28 (46%) | 11/28 (39%) | 8/28 (29%) |
| abs>0.03 | 13/28 (46%) | 12/28 (43%) | 11/28 (39%) | 9/28 (32%) | 7/28 (25%) |
| abs>0.04 | 10/28 (36%) | 10/28 (36%) | 9/28 (32%) | 7/28 (25%) | 6/28 (21%) |
| abs>0.05 | 8/28 (29%) | 8/28 (29%) | 7/28 (25%) | 5/28 (18%) | 5/28 (18%) |

> **Config keys:** `markets.drift_min_abs` and `markets.drift_min_pct` — currently at `0.03` / `0.07`. Adjust these to move diagonally in the grid above to reduce noise.

**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to ~11 drift flags by eliminating sub-cent moves, while keeping markets with a genuine price dislocation (3+ cent absolute move). The (0.03, 0.10) cell is the next step if 11 is still too many.

## Verdict

**Recommended mode: `strict_with_heuristic`**

Config baseline (abs>0.03, pct>7%) flags 12/28 markets as drift. Combined with strict_with_heuristic (no BR_NONE noise), expected candidates: ~12 drift + 19 heuristic-edge (with overlap possible).

At config thresholds (abs>0.03, pct>7%), drift flags 12/28 filtered markets (43%). `strict_with_heuristic` mode removes the BR_NONE catch-all and surfaces only markets with genuine heuristic edge or price drift.

> **Note:** This comparison measures candidate *volume and selectivity* only. Signal *correctness* — whether flagged markets are actually mispriced — cannot be judged until markets resolve and outcomes are logged.