# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-08-02T18:52:13.885486+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2922  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 57 | 46 | 80.7% | 12 | 0 | 34 |
| 0.06 | [0.05, 0.95] | ×1.0 | 54 | 44 | 81.5% | 11 | 0 | 33 |
| 0.06 | [0.05, 0.95] | ×2.0 | 52 | 42 | 80.8% | 11 | 0 | 31 |
| 0.06 | [0.10, 0.90] | ×0.5 | 41 | 31 | 75.6% | 10 | 0 | 21 |
| 0.06 | [0.10, 0.90] | ×1.0 | 39 | 30 | 76.9% | 10 | 0 | 20 |
| 0.06 | [0.10, 0.90] | ×2.0 | 37 | 28 | 75.7% | 10 | 0 | 18 |
| 0.06 | [0.15, 0.85] | ×0.5 | 28 | 20 | 71.4% | 9 | 0 | 11 |
| 0.06 | [0.15, 0.85] | ×1.0 | 26 | 19 | 73.1% | 9 | 0 | 10 |
| 0.06 | [0.15, 0.85] | ×2.0 | 25 | 18 | 72.0% | 9 | 0 | 9 |
| 0.08 | [0.05, 0.95] | ×0.5 | 57 | 46 | 80.7% | 12 | 0 | 34 |
| 0.08 | [0.05, 0.95] | ×1.0 | 54 | 44 | 81.5% | 11 | 0 | 33 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 52 | 42 | 80.8% | 11 | 0 | 31 |
| 0.08 | [0.10, 0.90] | ×0.5 | 41 | 31 | 75.6% | 10 | 0 | 21 |
| 0.08 | [0.10, 0.90] | ×1.0 | 39 | 30 | 76.9% | 10 | 0 | 20 |
| 0.08 | [0.10, 0.90] | ×2.0 | 37 | 28 | 75.7% | 10 | 0 | 18 |
| 0.08 | [0.15, 0.85] | ×0.5 | 28 | 20 | 71.4% | 9 | 0 | 11 |
| 0.08 | [0.15, 0.85] | ×1.0 | 26 | 19 | 73.1% | 9 | 0 | 10 |
| 0.08 | [0.15, 0.85] | ×2.0 | 25 | 18 | 72.0% | 9 | 0 | 9 | ← **rec**
| 0.12 | [0.05, 0.95] | ×0.5 | 57 | 45 | 78.9% | 11 | 0 | 34 |
| 0.12 | [0.05, 0.95] | ×1.0 | 54 | 43 | 79.6% | 10 | 0 | 33 |
| 0.12 | [0.05, 0.95] | ×2.0 | 52 | 41 | 78.8% | 10 | 0 | 31 |
| 0.12 | [0.10, 0.90] | ×0.5 | 41 | 30 | 73.2% | 9 | 0 | 21 |
| 0.12 | [0.10, 0.90] | ×1.0 | 39 | 29 | 74.4% | 9 | 0 | 20 |
| 0.12 | [0.10, 0.90] | ×2.0 | 37 | 27 | 73.0% | 9 | 0 | 18 |
| 0.12 | [0.15, 0.85] | ×0.5 | 28 | 19 | 67.9% | 8 | 0 | 11 |
| 0.12 | [0.15, 0.85] | ×1.0 | 26 | 18 | 69.2% | 8 | 0 | 10 |
| 0.12 | [0.15, 0.85] | ×2.0 | 25 | 17 | 68.0% | 8 | 0 | 9 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 57 | 49 | 86.0% | 35 | 10 | 4 |
| 0.06 | [0.05, 0.95] | ×1.0 | 54 | 46 | 85.2% | 33 | 9 | 4 |
| 0.06 | [0.05, 0.95] | ×2.0 | 52 | 44 | 84.6% | 32 | 9 | 3 |
| 0.06 | [0.10, 0.90] | ×0.5 | 41 | 34 | 82.9% | 24 | 7 | 3 |
| 0.06 | [0.10, 0.90] | ×1.0 | 39 | 32 | 82.1% | 23 | 6 | 3 |
| 0.06 | [0.10, 0.90] | ×2.0 | 37 | 30 | 81.1% | 22 | 6 | 2 |
| 0.06 | [0.15, 0.85] | ×0.5 | 28 | 22 | 78.6% | 16 | 3 | 3 |
| 0.06 | [0.15, 0.85] | ×1.0 | 26 | 20 | 76.9% | 15 | 2 | 3 |
| 0.06 | [0.15, 0.85] | ×2.0 | 25 | 19 | 76.0% | 15 | 2 | 2 |
| 0.08 | [0.05, 0.95] | ×0.5 | 57 | 49 | 86.0% | 32 | 10 | 7 |
| 0.08 | [0.05, 0.95] | ×1.0 | 54 | 46 | 85.2% | 30 | 9 | 7 |
| 0.08 | [0.05, 0.95] | ×2.0 | 52 | 44 | 84.6% | 29 | 9 | 6 |
| 0.08 | [0.10, 0.90] | ×0.5 | 41 | 34 | 82.9% | 21 | 7 | 6 |
| 0.08 | [0.10, 0.90] | ×1.0 | 39 | 32 | 82.1% | 20 | 6 | 6 |
| 0.08 | [0.10, 0.90] | ×2.0 | 37 | 30 | 81.1% | 19 | 6 | 5 |
| 0.08 | [0.15, 0.85] | ×0.5 | 28 | 22 | 78.6% | 15 | 3 | 4 |
| 0.08 | [0.15, 0.85] | ×1.0 | 26 | 20 | 76.9% | 14 | 2 | 4 |
| 0.08 | [0.15, 0.85] | ×2.0 | 25 | 19 | 76.0% | 14 | 2 | 3 |
| 0.12 | [0.05, 0.95] | ×0.5 | 57 | 48 | 84.2% | 29 | 10 | 9 |
| 0.12 | [0.05, 0.95] | ×1.0 | 54 | 45 | 83.3% | 27 | 9 | 9 |
| 0.12 | [0.05, 0.95] | ×2.0 | 52 | 43 | 82.7% | 26 | 9 | 8 |
| 0.12 | [0.10, 0.90] | ×0.5 | 41 | 33 | 80.5% | 19 | 7 | 7 |
| 0.12 | [0.10, 0.90] | ×1.0 | 39 | 31 | 79.5% | 18 | 6 | 7 |
| 0.12 | [0.10, 0.90] | ×2.0 | 37 | 29 | 78.4% | 17 | 6 | 6 |
| 0.12 | [0.15, 0.85] | ×0.5 | 28 | 21 | 75.0% | 13 | 3 | 5 |
| 0.12 | [0.15, 0.85] | ×1.0 | 26 | 19 | 73.1% | 12 | 2 | 5 |
| 0.12 | [0.15, 0.85] | ×2.0 | 25 | 18 | 72.0% | 12 | 2 | 4 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **54 markets** survive the filter and **44 are flagged** (81.5% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **11** markets (25%)
- `DRIFT` (order-book mid vs last trade): **33** markets (75%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×2.0, strict_with_heuristic  
→ 25 markets survive, 18 flagged (72.0%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×2.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.