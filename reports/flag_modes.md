# Flag Mode Comparison — Leviathan v1

**Snapshot:** 2026-09-08T12:07:39.435330+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2997  
**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  
**Drift thresholds (config):** abs>0.03, pct>7% (see grid below)  

Filter stage is identical across all modes. Markets surviving filter: **72**

## Signal Presence (mode-independent)

These signal counts reflect which signals FIRED across all filtered markets, independent of which mode is active and independent of branch evaluation order. They are identical under every mode — attribution no longer depends on ordering.

| Signal | Markets firing | % of filtered |
|--------|---------------|---------------|
| `sig_edge` (raw_edge > 0.08) | 12 | 17% |
| `sig_drift` (abs+pct drift thresholds) | 16 | 22% |
| `sig_br_none` (no heuristic match) | 50 | 69% |
| `sig_edge` AND `sig_drift` (both present) | 3 | 4% |

> **Attribution bug (now fixed):** Under `passthrough`, BR_NONE was checked before DRIFT so markets with both signals were labelled BR_NONE and DRIFT appeared as 0. The `sig_*` fields above show the true fire rates regardless of mode.

## Flag Path by Mode (how each mode uses the signals)

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 72 | 62 | 86.1% | 12 | 50 | 0 | 0 |
| `strict_anomaly_only` | 72 | 16 | 22.2% | 0 | 0 | 16 | 0 |
| `strict_with_heuristic` | 72 | 25 | 34.7% | 0 | 0 | 16 | 9 |

Under `passthrough`, 50 markets are labelled BR_NONE and the DRIFT branch is never reached — but `sig_drift` shows 16 of those markets actually have a drift signal present. Passthrough was masking drift by flagging via BR_NONE first.

## Drift Signal Diagnosis (by price bucket)

Root cause of the 86% drift-fire rate: `compute_drift_signal` previously required only `pct > 5%`. A 0.5-cent absolute move at a 5-cent price is a 10% percentage drift — qualifying as a signal despite being bid/ask noise. The table below shows fire rates and average moves bucketed by price level.

| Price bucket | N | Drift% (abs>0.03, pct>7%) | Avg abs move | Avg pct move |
|-------------|---|----------------|-------------|-------------|
| Low [0.05-0.15) | 22 | 23% | 0.0221 | 0.375 |
| MidLo [0.15-0.35) | 12 | 8% | 0.0237 | 0.712 |
| Mid [0.35-0.65) | 18 | 28% | 0.0433 | 0.068 |
| High [0.65-0.95] | 20 | 25% | 0.0548 | 0.071 |

Low-price markets fire at 100% because small absolute moves (0.5-1.5 cents) are large relative percentages. The fix requires BOTH `abs_drift > drift_min_abs` AND `pct_drift > drift_min_pct` — eliminating cent-level noise at low prices.

## Drift Threshold Sweep (% of filtered markets flagging as drift)

Grid of `drift_min_abs` x `drift_min_pct` combinations. Values show what percentage of the 72 filtered markets would have `drift_flag=True` under each combination. Config baseline (abs>0.03, pct>7%) = **22%**.

| drift_min_abs | pct>5% | pct>7% | pct>10% | pct>15% | pct>20% |
|---|---|---|---|---|---|
| abs>0.01 | 34/72 (47%) | 30/72 (42%) | 27/72 (38%) | 21/72 (29%) | 17/72 (24%) |
| abs>0.02 | 27/72 (38%) | 25/72 (35%) | 23/72 (32%) | 20/72 (28%) | 16/72 (22%) |
| abs>0.03 | 18/72 (25%) | 16/72 (22%) | 15/72 (21%) | 14/72 (19%) | 10/72 (14%) |
| abs>0.04 | 14/72 (19%) | 13/72 (18%) | 12/72 (17%) | 11/72 (15%) | 7/72 (10%) |
| abs>0.05 | 13/72 (18%) | 12/72 (17%) | 12/72 (17%) | 11/72 (15%) | 7/72 (10%) |

> **Config keys:** `markets.drift_min_abs` and `markets.drift_min_pct` — currently at `0.03` / `0.07`. Adjust these to move diagonally in the grid above to reduce noise.

**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to ~11 drift flags by eliminating sub-cent moves, while keeping markets with a genuine price dislocation (3+ cent absolute move). The (0.03, 0.10) cell is the next step if 11 is still too many.

## Verdict

**Recommended mode: `strict_with_heuristic`**

Config baseline (abs>0.03, pct>7%) flags 16/72 markets as drift. Combined with strict_with_heuristic (no BR_NONE noise), expected candidates: ~16 drift + 12 heuristic-edge (with overlap possible).

At config thresholds (abs>0.03, pct>7%), drift flags 16/72 filtered markets (22%). `strict_with_heuristic` mode removes the BR_NONE catch-all and surfaces only markets with genuine heuristic edge or price drift.

> **Note:** This comparison measures candidate *volume and selectivity* only. Signal *correctness* — whether flagged markets are actually mispriced — cannot be judged until markets resolve and outcomes are logged.