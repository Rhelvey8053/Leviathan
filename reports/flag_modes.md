# Flag Mode Comparison — Leviathan v1

**Snapshot:** 2026-08-25T01:38:06.453461+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2941  
**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  
**Drift thresholds (config):** abs>0.03, pct>7% (see grid below)  

Filter stage is identical across all modes. Markets surviving filter: **43**

## Signal Presence (mode-independent)

These signal counts reflect which signals FIRED across all filtered markets, independent of which mode is active and independent of branch evaluation order. They are identical under every mode — attribution no longer depends on ordering.

| Signal | Markets firing | % of filtered |
|--------|---------------|---------------|
| `sig_edge` (raw_edge > 0.08) | 12 | 28% |
| `sig_drift` (abs+pct drift thresholds) | 11 | 26% |
| `sig_br_none` (no heuristic match) | 21 | 49% |
| `sig_edge` AND `sig_drift` (both present) | 5 | 12% |

> **Attribution bug (now fixed):** Under `passthrough`, BR_NONE was checked before DRIFT so markets with both signals were labelled BR_NONE and DRIFT appeared as 0. The `sig_*` fields above show the true fire rates regardless of mode.

## Flag Path by Mode (how each mode uses the signals)

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 43 | 34 | 79.1% | 12 | 21 | 1 | 0 |
| `strict_anomaly_only` | 43 | 11 | 25.6% | 0 | 0 | 11 | 0 |
| `strict_with_heuristic` | 43 | 18 | 41.9% | 0 | 0 | 11 | 7 |

Under `passthrough`, 21 markets are labelled BR_NONE and the DRIFT branch is never reached — but `sig_drift` shows 11 of those markets actually have a drift signal present. Passthrough was masking drift by flagging via BR_NONE first.

## Drift Signal Diagnosis (by price bucket)

Root cause of the 86% drift-fire rate: `compute_drift_signal` previously required only `pct > 5%`. A 0.5-cent absolute move at a 5-cent price is a 10% percentage drift — qualifying as a signal despite being bid/ask noise. The table below shows fire rates and average moves bucketed by price level.

| Price bucket | N | Drift% (abs>0.03, pct>7%) | Avg abs move | Avg pct move |
|-------------|---|----------------|-------------|-------------|
| Low [0.05-0.15) | 17 | 29% | 0.0221 | 0.282 |
| MidLo [0.15-0.35) | 8 | 38% | 0.0225 | 0.085 |
| Mid [0.35-0.65) | 8 | 12% | 0.0231 | 0.046 |
| High [0.65-0.95] | 10 | 50% | 0.0590 | 0.081 |

Low-price markets fire at 100% because small absolute moves (0.5-1.5 cents) are large relative percentages. The fix requires BOTH `abs_drift > drift_min_abs` AND `pct_drift > drift_min_pct` — eliminating cent-level noise at low prices.

## Drift Threshold Sweep (% of filtered markets flagging as drift)

Grid of `drift_min_abs` x `drift_min_pct` combinations. Values show what percentage of the 43 filtered markets would have `drift_flag=True` under each combination. Config baseline (abs>0.03, pct>7%) = **33%**.

| drift_min_abs | pct>5% | pct>7% | pct>10% | pct>15% | pct>20% |
|---|---|---|---|---|---|
| abs>0.01 | 30/43 (70%) | 26/43 (60%) | 24/43 (56%) | 16/43 (37%) | 11/43 (26%) |
| abs>0.02 | 23/43 (53%) | 19/43 (44%) | 17/43 (40%) | 13/43 (30%) | 9/43 (21%) |
| abs>0.03 | 16/43 (37%) | 14/43 (33%) | 12/43 (28%) | 9/43 (21%) | 5/43 (12%) |
| abs>0.04 | 8/43 (19%) | 8/43 (19%) | 7/43 (16%) | 6/43 (14%) | 2/43 (5%) |
| abs>0.05 | 7/43 (16%) | 7/43 (16%) | 6/43 (14%) | 5/43 (12%) | 1/43 (2%) |

> **Config keys:** `markets.drift_min_abs` and `markets.drift_min_pct` — currently at `0.03` / `0.07`. Adjust these to move diagonally in the grid above to reduce noise.

**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to ~11 drift flags by eliminating sub-cent moves, while keeping markets with a genuine price dislocation (3+ cent absolute move). The (0.03, 0.10) cell is the next step if 11 is still too many.

## Verdict

**Recommended mode: `strict_with_heuristic`**

Config baseline (abs>0.03, pct>7%) flags 14/43 markets as drift. Combined with strict_with_heuristic (no BR_NONE noise), expected candidates: ~14 drift + 12 heuristic-edge (with overlap possible).

At config thresholds (abs>0.03, pct>7%), drift flags 14/43 filtered markets (33%). `strict_with_heuristic` mode removes the BR_NONE catch-all and surfaces only markets with genuine heuristic edge or price drift.

> **Note:** This comparison measures candidate *volume and selectivity* only. Signal *correctness* — whether flagged markets are actually mispriced — cannot be judged until markets resolve and outcomes are logged.