# Flag Mode Comparison — Leviathan v1

**Snapshot:** 2026-09-07T12:07:38.483623+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 3007  
**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  
**Drift thresholds (config):** abs>0.03, pct>7% (see grid below)  

Filter stage is identical across all modes. Markets surviving filter: **80**

## Signal Presence (mode-independent)

These signal counts reflect which signals FIRED across all filtered markets, independent of which mode is active and independent of branch evaluation order. They are identical under every mode — attribution no longer depends on ordering.

| Signal | Markets firing | % of filtered |
|--------|---------------|---------------|
| `sig_edge` (raw_edge > 0.08) | 21 | 26% |
| `sig_drift` (abs+pct drift thresholds) | 26 | 32% |
| `sig_br_none` (no heuristic match) | 46 | 57% |
| `sig_edge` AND `sig_drift` (both present) | 8 | 10% |

> **Attribution bug (now fixed):** Under `passthrough`, BR_NONE was checked before DRIFT so markets with both signals were labelled BR_NONE and DRIFT appeared as 0. The `sig_*` fields above show the true fire rates regardless of mode.

## Flag Path by Mode (how each mode uses the signals)

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 80 | 69 | 86.2% | 21 | 46 | 2 | 0 |
| `strict_anomaly_only` | 80 | 26 | 32.5% | 0 | 0 | 26 | 0 |
| `strict_with_heuristic` | 80 | 39 | 48.8% | 0 | 0 | 26 | 13 |

Under `passthrough`, 46 markets are labelled BR_NONE and the DRIFT branch is never reached — but `sig_drift` shows 26 of those markets actually have a drift signal present. Passthrough was masking drift by flagging via BR_NONE first.

## Drift Signal Diagnosis (by price bucket)

Root cause of the 86% drift-fire rate: `compute_drift_signal` previously required only `pct > 5%`. A 0.5-cent absolute move at a 5-cent price is a 10% percentage drift — qualifying as a signal despite being bid/ask noise. The table below shows fire rates and average moves bucketed by price level.

| Price bucket | N | Drift% (abs>0.03, pct>7%) | Avg abs move | Avg pct move |
|-------------|---|----------------|-------------|-------------|
| Low [0.05-0.15) | 26 | 31% | 0.0276 | 0.528 |
| MidLo [0.15-0.35) | 17 | 35% | 0.0412 | 0.911 |
| Mid [0.35-0.65) | 23 | 35% | 0.0613 | 0.114 |
| High [0.65-0.95] | 14 | 29% | 0.0529 | 0.073 |

Low-price markets fire at 100% because small absolute moves (0.5-1.5 cents) are large relative percentages. The fix requires BOTH `abs_drift > drift_min_abs` AND `pct_drift > drift_min_pct` — eliminating cent-level noise at low prices.

## Drift Threshold Sweep (% of filtered markets flagging as drift)

Grid of `drift_min_abs` x `drift_min_pct` combinations. Values show what percentage of the 80 filtered markets would have `drift_flag=True` under each combination. Config baseline (abs>0.03, pct>7%) = **32%**.

| drift_min_abs | pct>5% | pct>7% | pct>10% | pct>15% | pct>20% |
|---|---|---|---|---|---|
| abs>0.01 | 46/80 (57%) | 38/80 (48%) | 32/80 (40%) | 27/80 (34%) | 24/80 (30%) |
| abs>0.02 | 40/80 (50%) | 33/80 (41%) | 29/80 (36%) | 25/80 (31%) | 22/80 (28%) |
| abs>0.03 | 30/80 (38%) | 26/80 (32%) | 22/80 (28%) | 20/80 (25%) | 18/80 (22%) |
| abs>0.04 | 21/80 (26%) | 19/80 (24%) | 16/80 (20%) | 15/80 (19%) | 13/80 (16%) |
| abs>0.05 | 20/80 (25%) | 18/80 (22%) | 16/80 (20%) | 15/80 (19%) | 13/80 (16%) |

> **Config keys:** `markets.drift_min_abs` and `markets.drift_min_pct` — currently at `0.03` / `0.07`. Adjust these to move diagonally in the grid above to reduce noise.

**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to ~11 drift flags by eliminating sub-cent moves, while keeping markets with a genuine price dislocation (3+ cent absolute move). The (0.03, 0.10) cell is the next step if 11 is still too many.

## Verdict

**Recommended mode: `strict_with_heuristic`**

Config baseline (abs>0.03, pct>7%) flags 26/80 markets as drift. Combined with strict_with_heuristic (no BR_NONE noise), expected candidates: ~26 drift + 21 heuristic-edge (with overlap possible).

At config thresholds (abs>0.03, pct>7%), drift flags 26/80 filtered markets (32%). `strict_with_heuristic` mode removes the BR_NONE catch-all and surfaces only markets with genuine heuristic edge or price drift.

> **Note:** This comparison measures candidate *volume and selectivity* only. Signal *correctness* — whether flagged markets are actually mispriced — cannot be judged until markets resolve and outcomes are logged.