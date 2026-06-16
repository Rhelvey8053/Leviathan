# Flag Mode Comparison — Leviathan v1

**Snapshot:** 2026-06-16T04:38:36.075689+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2428  
**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  

Filter stage is identical across all modes — only the flag condition differs.
Markets surviving filter: **21**

## Results by Mode

| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |
|------|----------------|---------|-----------|------|---------|-------|-----------|
| `passthrough` | 21 | 21 | 100.0% | 7 | 14 | 0 | 0 |
| `strict_anomaly_only` | 21 | 18 | 85.7% | 0 | 0 | 18 | 0 |
| `strict_with_heuristic` | 21 | 18 | 85.7% | 0 | 0 | 18 | 0 |

## Mode Descriptions

### `passthrough` (current production default)
Flags **21 of 21 markets** (100.0%). Trigger: `raw_edge > threshold` OR `base_rate is None` OR `drift`. The `BR_NONE` fallback (14 markets, 67%) causes every market without a heuristic base-rate match to flag automatically. This forwards the full filtered set to Claude — the pre-Claude funnel adds no probability-based discrimination.

### `strict_anomaly_only`
Flags **18 of 21 markets** (85.7%). Trigger: `drift_flag` only (whale_detected would also trigger, but is not available at score time — applied post-hoc by main.py). Eliminates all base_rate-derived flagging. Only markets where the order-book mid has measurably drifted from the last traded price are forwarded to Claude. 

### `strict_with_heuristic`
Flags **18 of 21 markets** (85.7%). Trigger: `drift_flag` OR `(base_rate is not None AND raw_edge > threshold)`. Excludes pure BR_NONE markets (no heuristic match) while keeping markets where a known heuristic base rate meaningfully disagrees with the current price. This is the practical middle ground: it cuts the BR_NONE noise while preserving cases where the scanner has a concrete probability estimate to compare against.

## Verdict

| Mode | Candidates forwarded to Claude | Assessment |
|------|-------------------------------|------------|
| `passthrough` | 21 | 100% flag rate — 14 are BR_NONE noise |
| `strict_anomaly_only` | 18 | 18 DRIFT markets — removes 3 BR_NONE-only markets |
| `strict_with_heuristic` | 18 | Identical to strict_anomaly_only on this snapshot (HEURISTIC=0) |

**Recommended mode: `strict_with_heuristic`** — removes the 14 BR_NONE catch-all flags in
principle, though on today's snapshot the 18 flagged markets are all DRIFT (heuristic edge
contributed 0). This reveals an important finding: **drift is a very active signal** (18 of
21 filtered markets have order-book mid > 5% away from last traded price), but was entirely
invisible in the threshold_sweep.md results because the old classifier checked BR_NONE before
DRIFT and never reached the DRIFT branch. The pre-Claude funnel is doing more real work than
the sweep suggested.

On this snapshot, `strict_anomaly_only` and `strict_with_heuristic` are equivalent because no
heuristic edge markets survived without also having drift. On a snapshot with more heuristic
matches (rain forecasts, political markets with large price gaps), `strict_with_heuristic`
would include those while `strict_anomaly_only` would not.

The 3 markets dropped by strict modes are pure BR_NONE with no drift and no heuristic — the
weakest candidates.

> **Note:** This comparison measures candidate *volume and selectivity* only. Signal
*correctness* — whether the flagged markets are actually mispriced — cannot be judged until
markets resolve and outcomes are logged.