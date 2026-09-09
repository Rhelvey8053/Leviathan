# Flag Mode Comparison — Leviathan v1

**Snapshot:** 2026-09-09T12:08:11.516929+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 3000  
**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  
**Drift thresholds (config):** abs>0.03, pct>7% (see grid below)  

Filter stage is identical across all modes. Markets surviving filter: **82**

## Signal Presence (mode-independent)

These signal counts reflect which signals FIRED across all filtered markets, independent of which mode is active and independent of branch evaluation order. They are identical under every mode — attribution no longer depends on ordering.

| Signal | Markets firing | % of filtered |
|--------|---------------|---------------|
| `sig_edge` (raw_edge > 0.08) | 16 | 20% |
| `sig_drift` (abs+pct drift thresholds) | 32 | 39% |
| `sig_br_none` (no heuristic match) | 57 | 70% |
| `sig_edge` AND `sig_drift` (both present) | 9 | 11% |

> **Attribution bug (now fixed):** Under `passthrough`, BR_NONE was checked before DRIFT so markets with both signals were labelled BR_NONE and DRIFT appeared as 0. The `sig_*` fields above show the true fire rates regardless of mode.

## Flag Path by Mode (how each mode uses the signals)

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 82 | 75 | 91.5% | 16 | 57 | 2 | 0 |
| `strict_anomaly_only` | 82 | 32 | 39.0% | 0 | 0 | 32 | 0 |
| `strict_with_heuristic` | 82 | 39 | 47.6% | 0 | 0 | 32 | 7 |

Under `passthrough`, 57 markets are labelled BR_NONE and the DRIFT branch is never reached — but `sig_drift` shows 32 of those markets actually have a drift signal present. Passthrough was masking drift by flagging via BR_NONE first.

## Drift Signal Diagnosis (by price bucket)

Root cause of the 86% drift-fire rate: `compute_drift_signal` previously required only `pct > 5%`. A 0.5-cent absolute move at a 5-cent price is a 10% percentage drift — qualifying as a signal despite being bid/ask noise. The table below shows fire rates and average moves bucketed by price level.

| Price bucket | N | Drift% (abs>0.03, pct>7%) | Avg abs move | Avg pct move |
|-------------|---|----------------|-------------|-------------|
| Low [0.05-0.15) | 19 | 32% | 0.0301 | 0.806 |
| MidLo [0.15-0.35) | 18 | 67% | 0.0947 | 1.908 |
| Mid [0.35-0.65) | 32 | 41% | 0.0733 | 0.110 |
| High [0.65-0.95] | 13 | 8% | 0.0277 | 0.035 |

Low-price markets fire at 100% because small absolute moves (0.5-1.5 cents) are large relative percentages. The fix requires BOTH `abs_drift > drift_min_abs` AND `pct_drift > drift_min_pct` — eliminating cent-level noise at low prices.

## Drift Threshold Sweep (% of filtered markets flagging as drift)

Grid of `drift_min_abs` x `drift_min_pct` combinations. Values show what percentage of the 82 filtered markets would have `drift_flag=True` under each combination. Config baseline (abs>0.03, pct>7%) = **39%**.

| drift_min_abs | pct>5% | pct>7% | pct>10% | pct>15% | pct>20% |
|---|---|---|---|---|---|
| abs>0.01 | 56/82 (68%) | 43/82 (52%) | 40/82 (49%) | 30/82 (37%) | 23/82 (28%) |
| abs>0.02 | 50/82 (61%) | 39/82 (48%) | 36/82 (44%) | 29/82 (35%) | 22/82 (27%) |
| abs>0.03 | 41/82 (50%) | 32/82 (39%) | 29/82 (35%) | 25/82 (30%) | 19/82 (23%) |
| abs>0.04 | 30/82 (37%) | 26/82 (32%) | 23/82 (28%) | 20/82 (24%) | 15/82 (18%) |
| abs>0.05 | 24/82 (29%) | 22/82 (27%) | 20/82 (24%) | 18/82 (22%) | 15/82 (18%) |

> **Config keys:** `markets.drift_min_abs` and `markets.drift_min_pct` — currently at `0.03` / `0.07`. Adjust these to move diagonally in the grid above to reduce noise.

**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to ~11 drift flags by eliminating sub-cent moves, while keeping markets with a genuine price dislocation (3+ cent absolute move). The (0.03, 0.10) cell is the next step if 11 is still too many.

## Verdict

**Recommended mode: `strict_with_heuristic`**

Config baseline (abs>0.03, pct>7%) flags 32/82 markets as drift. Combined with strict_with_heuristic (no BR_NONE noise), expected candidates: ~32 drift + 16 heuristic-edge (with overlap possible).

At config thresholds (abs>0.03, pct>7%), drift flags 32/82 filtered markets (39%). `strict_with_heuristic` mode removes the BR_NONE catch-all and surfaces only markets with genuine heuristic edge or price drift.

> **Note:** This comparison measures candidate *volume and selectivity* only. Signal *correctness* — whether flagged markets are actually mispriced — cannot be judged until markets resolve and outcomes are logged.