# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-06-16T04:38:36.075689+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2428  
**Grid size:** 27 combinations  

## Grid Results

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 22 | 22 | 100.0% | 7 | 15 | 0 |
| 0.06 | [0.05, 0.95] | ×1.0 | 21 | 21 | 100.0% | 7 | 14 | 0 |
| 0.06 | [0.05, 0.95] | ×2.0 | 15 | 15 | 100.0% | 5 | 10 | 0 |
| 0.06 | [0.10, 0.90] | ×0.5 | 12 | 12 | 100.0% | 1 | 11 | 0 |
| 0.06 | [0.10, 0.90] | ×1.0 | 11 | 11 | 100.0% | 1 | 10 | 0 |
| 0.06 | [0.10, 0.90] | ×2.0 | 7 | 7 | 100.0% | 1 | 6 | 0 |
| 0.06 | [0.15, 0.85] | ×0.5 | 11 | 11 | 100.0% | 1 | 10 | 0 |
| 0.06 | [0.15, 0.85] | ×1.0 | 10 | 10 | 100.0% | 1 | 9 | 0 |
| 0.06 | [0.15, 0.85] | ×2.0 | 7 | 7 | 100.0% | 1 | 6 | 0 |
| 0.08 | [0.05, 0.95] | ×0.5 | 22 | 22 | 100.0% | 7 | 15 | 0 |
| 0.08 | [0.05, 0.95] | ×1.0 | 21 | 21 | 100.0% | 7 | 14 | 0 | <- **rec**
| 0.08 | [0.05, 0.95] | ×2.0 | 15 | 15 | 100.0% | 5 | 10 | 0 |
| 0.08 | [0.10, 0.90] | ×0.5 | 12 | 12 | 100.0% | 1 | 11 | 0 |
| 0.08 | [0.10, 0.90] | ×1.0 | 11 | 11 | 100.0% | 1 | 10 | 0 |
| 0.08 | [0.10, 0.90] | ×2.0 | 7 | 7 | 100.0% | 1 | 6 | 0 |
| 0.08 | [0.15, 0.85] | ×0.5 | 11 | 11 | 100.0% | 1 | 10 | 0 |
| 0.08 | [0.15, 0.85] | ×1.0 | 10 | 10 | 100.0% | 1 | 9 | 0 |
| 0.08 | [0.15, 0.85] | ×2.0 | 7 | 7 | 100.0% | 1 | 6 | 0 |
| 0.12 | [0.05, 0.95] | ×0.5 | 22 | 22 | 100.0% | 6 | 15 | 1 |
| 0.12 | [0.05, 0.95] | ×1.0 | 21 | 21 | 100.0% | 6 | 14 | 1 |
| 0.12 | [0.05, 0.95] | ×2.0 | 15 | 15 | 100.0% | 4 | 10 | 1 |
| 0.12 | [0.10, 0.90] | ×0.5 | 12 | 12 | 100.0% | 0 | 11 | 1 |
| 0.12 | [0.10, 0.90] | ×1.0 | 11 | 11 | 100.0% | 0 | 10 | 1 |
| 0.12 | [0.10, 0.90] | ×2.0 | 7 | 7 | 100.0% | 0 | 6 | 1 |
| 0.12 | [0.15, 0.85] | ×0.5 | 11 | 11 | 100.0% | 0 | 10 | 1 |
| 0.12 | [0.15, 0.85] | ×1.0 | 10 | 10 | 100.0% | 0 | 9 | 1 |
| 0.12 | [0.15, 0.85] | ×2.0 | 7 | 7 | 100.0% | 0 | 6 | 1 |

## Verdict

At **current production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0): **21 markets** survive the filter and **21 are flagged** (100.0% flag rate).

**Flag path breakdown:**
- `BR_NONE` (base_rate=None fallback): **14** markets (67%)
- `EDGE` (heuristic base rate + edge > threshold): **7** markets (33%)
- `DRIFT` (order-book mid vs last trade): **0** markets (0%)

**`BR_NONE` is the majority flag path.** The scanner's heuristic base rates cover only a small fraction of market types (rain, elections, IPOs, etc.). For everything else, any market with a mid-price gets flagged automatically. Tighter filters reduce noise without fixing the root cause.

**Is the flag logic itself the problem?**  
Yes, partially. The `score_market` flag fires on `base_rate is None and mid_price is not None` as a catch-all, meaning any market with a price and no heuristic match is automatically a candidate. This is intentional (send everything to Claude and let it filter), but it means the pre-Claude funnel is not doing meaningful probability-based selection. The fix is not a threshold change — it is adding more heuristic base rates, or replacing the `BR_NONE` trigger with a require-signal rule so only markets with a real anomaly (edge, drift, or whale) are flagged.

## Recommendation

**Stick with current production config** — it already produces a workable candidate count. The real improvement is in flag logic, not thresholds.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.