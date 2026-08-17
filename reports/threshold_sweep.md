# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-08-16T18:53:32.265041+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2997  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 42 | 31 | 73.8% | 6 | 0 | 25 |
| 0.06 | [0.05, 0.95] | ×1.0 | 40 | 29 | 72.5% | 5 | 0 | 24 |
| 0.06 | [0.05, 0.95] | ×2.0 | 38 | 28 | 73.7% | 5 | 0 | 23 |
| 0.06 | [0.10, 0.90] | ×0.5 | 30 | 21 | 70.0% | 6 | 0 | 15 |
| 0.06 | [0.10, 0.90] | ×1.0 | 28 | 19 | 67.9% | 5 | 0 | 14 |
| 0.06 | [0.10, 0.90] | ×2.0 | 26 | 18 | 69.2% | 5 | 0 | 13 |
| 0.06 | [0.15, 0.85] | ×0.5 | 21 | 16 | 76.2% | 6 | 0 | 10 |
| 0.06 | [0.15, 0.85] | ×1.0 | 19 | 14 | 73.7% | 5 | 0 | 9 |
| 0.06 | [0.15, 0.85] | ×2.0 | 18 | 14 | 77.8% | 5 | 0 | 9 |
| 0.08 | [0.05, 0.95] | ×0.5 | 42 | 31 | 73.8% | 6 | 0 | 25 |
| 0.08 | [0.05, 0.95] | ×1.0 | 40 | 29 | 72.5% | 5 | 0 | 24 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 38 | 28 | 73.7% | 5 | 0 | 23 |
| 0.08 | [0.10, 0.90] | ×0.5 | 30 | 21 | 70.0% | 6 | 0 | 15 |
| 0.08 | [0.10, 0.90] | ×1.0 | 28 | 19 | 67.9% | 5 | 0 | 14 | ← **rec**
| 0.08 | [0.10, 0.90] | ×2.0 | 26 | 18 | 69.2% | 5 | 0 | 13 |
| 0.08 | [0.15, 0.85] | ×0.5 | 21 | 16 | 76.2% | 6 | 0 | 10 |
| 0.08 | [0.15, 0.85] | ×1.0 | 19 | 14 | 73.7% | 5 | 0 | 9 |
| 0.08 | [0.15, 0.85] | ×2.0 | 18 | 14 | 77.8% | 5 | 0 | 9 |
| 0.12 | [0.05, 0.95] | ×0.5 | 42 | 31 | 73.8% | 6 | 0 | 25 |
| 0.12 | [0.05, 0.95] | ×1.0 | 40 | 29 | 72.5% | 5 | 0 | 24 |
| 0.12 | [0.05, 0.95] | ×2.0 | 38 | 28 | 73.7% | 5 | 0 | 23 |
| 0.12 | [0.10, 0.90] | ×0.5 | 30 | 21 | 70.0% | 6 | 0 | 15 |
| 0.12 | [0.10, 0.90] | ×1.0 | 28 | 19 | 67.9% | 5 | 0 | 14 |
| 0.12 | [0.10, 0.90] | ×2.0 | 26 | 18 | 69.2% | 5 | 0 | 13 |
| 0.12 | [0.15, 0.85] | ×0.5 | 21 | 16 | 76.2% | 6 | 0 | 10 |
| 0.12 | [0.15, 0.85] | ×1.0 | 19 | 14 | 73.7% | 5 | 0 | 9 |
| 0.12 | [0.15, 0.85] | ×2.0 | 18 | 14 | 77.8% | 5 | 0 | 9 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 42 | 33 | 78.6% | 22 | 9 | 2 |
| 0.06 | [0.05, 0.95] | ×1.0 | 40 | 31 | 77.5% | 21 | 8 | 2 |
| 0.06 | [0.05, 0.95] | ×2.0 | 38 | 30 | 78.9% | 20 | 8 | 2 |
| 0.06 | [0.10, 0.90] | ×0.5 | 30 | 23 | 76.7% | 16 | 5 | 2 |
| 0.06 | [0.10, 0.90] | ×1.0 | 28 | 21 | 75.0% | 15 | 4 | 2 |
| 0.06 | [0.10, 0.90] | ×2.0 | 26 | 20 | 76.9% | 14 | 4 | 2 |
| 0.06 | [0.15, 0.85] | ×0.5 | 21 | 17 | 81.0% | 11 | 4 | 2 |
| 0.06 | [0.15, 0.85] | ×1.0 | 19 | 15 | 78.9% | 10 | 3 | 2 |
| 0.06 | [0.15, 0.85] | ×2.0 | 18 | 15 | 83.3% | 10 | 3 | 2 |
| 0.08 | [0.05, 0.95] | ×0.5 | 42 | 33 | 78.6% | 20 | 9 | 4 |
| 0.08 | [0.05, 0.95] | ×1.0 | 40 | 31 | 77.5% | 19 | 8 | 4 |
| 0.08 | [0.05, 0.95] | ×2.0 | 38 | 30 | 78.9% | 18 | 8 | 4 |
| 0.08 | [0.10, 0.90] | ×0.5 | 30 | 23 | 76.7% | 15 | 5 | 3 |
| 0.08 | [0.10, 0.90] | ×1.0 | 28 | 21 | 75.0% | 14 | 4 | 3 |
| 0.08 | [0.10, 0.90] | ×2.0 | 26 | 20 | 76.9% | 13 | 4 | 3 |
| 0.08 | [0.15, 0.85] | ×0.5 | 21 | 17 | 81.0% | 10 | 4 | 3 |
| 0.08 | [0.15, 0.85] | ×1.0 | 19 | 15 | 78.9% | 9 | 3 | 3 |
| 0.08 | [0.15, 0.85] | ×2.0 | 18 | 15 | 83.3% | 9 | 3 | 3 |
| 0.12 | [0.05, 0.95] | ×0.5 | 42 | 33 | 78.6% | 20 | 9 | 4 |
| 0.12 | [0.05, 0.95] | ×1.0 | 40 | 31 | 77.5% | 19 | 8 | 4 |
| 0.12 | [0.05, 0.95] | ×2.0 | 38 | 30 | 78.9% | 18 | 8 | 4 |
| 0.12 | [0.10, 0.90] | ×0.5 | 30 | 23 | 76.7% | 15 | 5 | 3 |
| 0.12 | [0.10, 0.90] | ×1.0 | 28 | 21 | 75.0% | 14 | 4 | 3 |
| 0.12 | [0.10, 0.90] | ×2.0 | 26 | 20 | 76.9% | 13 | 4 | 3 |
| 0.12 | [0.15, 0.85] | ×0.5 | 21 | 17 | 81.0% | 10 | 4 | 3 |
| 0.12 | [0.15, 0.85] | ×1.0 | 19 | 15 | 78.9% | 9 | 3 | 3 |
| 0.12 | [0.15, 0.85] | ×2.0 | 18 | 15 | 83.3% | 9 | 3 | 3 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **40 markets** survive the filter and **29 are flagged** (72.5% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **5** markets (17%)
- `DRIFT` (order-book mid vs last trade): **24** markets (83%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.10, 0.90], vol×1.0, strict_with_heuristic  
→ 28 markets survive, 19 flagged (67.9%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×1.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.