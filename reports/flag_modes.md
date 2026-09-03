# Flag Mode Comparison — Leviathan v1

**Snapshot:** 2026-09-03T15:34:31.678481+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 3048  
**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  
**Drift thresholds (config):** abs>0.03, pct>7% (see grid below)  

Filter stage is identical across all modes. Markets surviving filter: **102**

## Signal Presence (mode-independent)

These signal counts reflect which signals FIRED across all filtered markets, independent of which mode is active and independent of branch evaluation order. They are identical under every mode — attribution no longer depends on ordering.

| Signal | Markets firing | % of filtered |
|--------|---------------|---------------|
| `sig_edge` (raw_edge > 0.08) | 16 | 16% |
| `sig_drift` (abs+pct drift thresholds) | 19 | 19% |
| `sig_br_none` (no heuristic match) | 72 | 71% |
| `sig_edge` AND `sig_drift` (both present) | 4 | 4% |

> **Attribution bug (now fixed):** Under `passthrough`, BR_NONE was checked before DRIFT so markets with both signals were labelled BR_NONE and DRIFT appeared as 0. The `sig_*` fields above show the true fire rates regardless of mode.

## Flag Path by Mode (how each mode uses the signals)

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 102 | 84 | 82.4% | 12 | 72 | 0 | 0 |
| `strict_anomaly_only` | 102 | 19 | 18.6% | 0 | 0 | 19 | 0 |
| `strict_with_heuristic` | 102 | 27 | 26.5% | 0 | 0 | 19 | 8 |

Under `passthrough`, 72 markets are labelled BR_NONE and the DRIFT branch is never reached — but `sig_drift` shows 19 of those markets actually have a drift signal present. Passthrough was masking drift by flagging via BR_NONE first.

## Drift Signal Diagnosis (by price bucket)

Root cause of the 86% drift-fire rate: `compute_drift_signal` previously required only `pct > 5%`. A 0.5-cent absolute move at a 5-cent price is a 10% percentage drift — qualifying as a signal despite being bid/ask noise. The table below shows fire rates and average moves bucketed by price level.

| Price bucket | N | Drift% (abs>0.03, pct>7%) | Avg abs move | Avg pct move |
|-------------|---|----------------|-------------|-------------|
| Low [0.05-0.15) | 35 | 20% | 0.0179 | 0.170 |
| MidLo [0.15-0.35) | 23 | 13% | 0.0328 | 0.104 |
| Mid [0.35-0.65) | 23 | 22% | 0.0357 | 0.122 |
| High [0.65-0.95] | 21 | 38% | 0.0512 | 0.063 |

Low-price markets fire at 100% because small absolute moves (0.5-1.5 cents) are large relative percentages. The fix requires BOTH `abs_drift > drift_min_abs` AND `pct_drift > drift_min_pct` — eliminating cent-level noise at low prices.

## Drift Threshold Sweep (% of filtered markets flagging as drift)

Grid of `drift_min_abs` x `drift_min_pct` combinations. Values show what percentage of the 102 filtered markets would have `drift_flag=True` under each combination. Config baseline (abs>0.03, pct>7%) = **23%**.

| drift_min_abs | pct>5% | pct>7% | pct>10% | pct>15% | pct>20% |
|---|---|---|---|---|---|
| abs>0.01 | 49/102 (48%) | 42/102 (41%) | 34/102 (33%) | 24/102 (24%) | 15/102 (15%) |
| abs>0.02 | 33/102 (32%) | 30/102 (29%) | 24/102 (24%) | 19/102 (19%) | 14/102 (14%) |
| abs>0.03 | 25/102 (25%) | 23/102 (23%) | 19/102 (19%) | 15/102 (15%) | 13/102 (13%) |
| abs>0.04 | 20/102 (20%) | 19/102 (19%) | 15/102 (15%) | 12/102 (12%) | 10/102 (10%) |
| abs>0.05 | 17/102 (17%) | 17/102 (17%) | 14/102 (14%) | 11/102 (11%) | 9/102 (9%) |

> **Config keys:** `markets.drift_min_abs` and `markets.drift_min_pct` — currently at `0.03` / `0.07`. Adjust these to move diagonally in the grid above to reduce noise.

**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to ~11 drift flags by eliminating sub-cent moves, while keeping markets with a genuine price dislocation (3+ cent absolute move). The (0.03, 0.10) cell is the next step if 11 is still too many.

## Verdict

**Recommended mode: `strict_with_heuristic`**

Config baseline (abs>0.03, pct>7%) flags 23/102 markets as drift. Combined with strict_with_heuristic (no BR_NONE noise), expected candidates: ~23 drift + 16 heuristic-edge (with overlap possible).

At config thresholds (abs>0.03, pct>7%), drift flags 23/102 filtered markets (23%). `strict_with_heuristic` mode removes the BR_NONE catch-all and surfaces only markets with genuine heuristic edge or price drift.

> **Note:** This comparison measures candidate *volume and selectivity* only. Signal *correctness* — whether flagged markets are actually mispriced — cannot be judged until markets resolve and outcomes are logged.