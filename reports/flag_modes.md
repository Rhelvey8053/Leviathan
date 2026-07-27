# Flag Mode Comparison — Leviathan v1

**Snapshot:** 2026-07-26T16:15:52.700285+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2476  
**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  
**Drift thresholds (config):** abs>0.03, pct>7% (see grid below)  

Filter stage is identical across all modes. Markets surviving filter: **29**

## Signal Presence (mode-independent)

These signal counts reflect which signals FIRED across all filtered markets, independent of which mode is active and independent of branch evaluation order. They are identical under every mode — attribution no longer depends on ordering.

| Signal | Markets firing | % of filtered |
|--------|---------------|---------------|
| `sig_edge` (raw_edge > 0.08) | 19 | 66% |
| `sig_drift` (abs+pct drift thresholds) | 14 | 48% |
| `sig_br_none` (no heuristic match) | 6 | 21% |
| `sig_edge` AND `sig_drift` (both present) | 9 | 31% |

> **Attribution bug (now fixed):** Under `passthrough`, BR_NONE was checked before DRIFT so markets with both signals were labelled BR_NONE and DRIFT appeared as 0. The `sig_*` fields above show the true fire rates regardless of mode.

## Flag Path by Mode (how each mode uses the signals)

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 29 | 28 | 96.6% | 19 | 6 | 3 | 0 |
| `strict_anomaly_only` | 29 | 14 | 48.3% | 0 | 0 | 14 | 0 |
| `strict_with_heuristic` | 29 | 24 | 82.8% | 0 | 0 | 14 | 10 |

Under `passthrough`, 6 markets are labelled BR_NONE and the DRIFT branch is never reached — but `sig_drift` shows 14 of those markets actually have a drift signal present. Passthrough was masking drift by flagging via BR_NONE first.

## Drift Signal Diagnosis (by price bucket)

Root cause of the 86% drift-fire rate: `compute_drift_signal` previously required only `pct > 5%`. A 0.5-cent absolute move at a 5-cent price is a 10% percentage drift — qualifying as a signal despite being bid/ask noise. The table below shows fire rates and average moves bucketed by price level.

| Price bucket | N | Drift% (abs>0.03, pct>7%) | Avg abs move | Avg pct move |
|-------------|---|----------------|-------------|-------------|
| Low [0.05-0.15) | 13 | 31% | 0.0883 | 0.344 |
| MidLo [0.15-0.35) | 9 | 67% | 0.0528 | 0.605 |
| Mid [0.35-0.65) | 7 | 57% | 0.0650 | 0.112 |
| High [0.65-0.95] | 0 | 0% | 0.0000 | 0.000 |

Low-price markets fire at 100% because small absolute moves (0.5-1.5 cents) are large relative percentages. The fix requires BOTH `abs_drift > drift_min_abs` AND `pct_drift > drift_min_pct` — eliminating cent-level noise at low prices.

## Drift Threshold Sweep (% of filtered markets flagging as drift)

Grid of `drift_min_abs` x `drift_min_pct` combinations. Values show what percentage of the 29 filtered markets would have `drift_flag=True` under each combination. Config baseline (abs>0.03, pct>7%) = **48%**.

| drift_min_abs | pct>5% | pct>7% | pct>10% | pct>15% | pct>20% |
|---|---|---|---|---|---|
| abs>0.01 | 25/29 (86%) | 24/29 (83%) | 21/29 (72%) | 19/29 (66%) | 15/29 (52%) |
| abs>0.02 | 22/29 (76%) | 21/29 (72%) | 19/29 (66%) | 17/29 (59%) | 14/29 (48%) |
| abs>0.03 | 15/29 (52%) | 14/29 (48%) | 12/29 (41%) | 11/29 (38%) | 10/29 (34%) |
| abs>0.04 | 11/29 (38%) | 11/29 (38%) | 9/29 (31%) | 8/29 (28%) | 8/29 (28%) |
| abs>0.05 | 9/29 (31%) | 9/29 (31%) | 8/29 (28%) | 7/29 (24%) | 7/29 (24%) |

> **Config keys:** `markets.drift_min_abs` and `markets.drift_min_pct` — currently at `0.03` / `0.07`. Adjust these to move diagonally in the grid above to reduce noise.

**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to ~11 drift flags by eliminating sub-cent moves, while keeping markets with a genuine price dislocation (3+ cent absolute move). The (0.03, 0.10) cell is the next step if 11 is still too many.

## Verdict

**Recommended mode: `strict_with_heuristic`**

Config baseline (abs>0.03, pct>7%) flags 14/29 markets as drift. Combined with strict_with_heuristic (no BR_NONE noise), expected candidates: ~14 drift + 19 heuristic-edge (with overlap possible).

At config thresholds (abs>0.03, pct>7%), drift flags 14/29 filtered markets (48%). `strict_with_heuristic` mode removes the BR_NONE catch-all and surfaces only markets with genuine heuristic edge or price drift.

> **Note:** This comparison measures candidate *volume and selectivity* only. Signal *correctness* — whether flagged markets are actually mispriced — cannot be judged until markets resolve and outcomes are logged.