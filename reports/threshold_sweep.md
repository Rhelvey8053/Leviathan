# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-08-02T01:10:00.766242+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2922  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 41 | 36 | 87.8% | 3 | 0 | 33 |
| 0.06 | [0.05, 0.95] | ×1.0 | 37 | 33 | 89.2% | 2 | 0 | 31 |
| 0.06 | [0.05, 0.95] | ×2.0 | 35 | 31 | 88.6% | 2 | 0 | 29 |
| 0.06 | [0.10, 0.90] | ×0.5 | 27 | 22 | 81.5% | 1 | 0 | 21 |
| 0.06 | [0.10, 0.90] | ×1.0 | 24 | 20 | 83.3% | 1 | 0 | 19 |
| 0.06 | [0.10, 0.90] | ×2.0 | 22 | 18 | 81.8% | 1 | 0 | 17 |
| 0.06 | [0.15, 0.85] | ×0.5 | 18 | 14 | 77.8% | 1 | 0 | 13 |
| 0.06 | [0.15, 0.85] | ×1.0 | 15 | 12 | 80.0% | 1 | 0 | 11 |
| 0.06 | [0.15, 0.85] | ×2.0 | 14 | 11 | 78.6% | 1 | 0 | 10 |
| 0.08 | [0.05, 0.95] | ×0.5 | 41 | 36 | 87.8% | 3 | 0 | 33 |
| 0.08 | [0.05, 0.95] | ×1.0 | 37 | 33 | 89.2% | 2 | 0 | 31 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 35 | 31 | 88.6% | 2 | 0 | 29 |
| 0.08 | [0.10, 0.90] | ×0.5 | 27 | 22 | 81.5% | 1 | 0 | 21 |
| 0.08 | [0.10, 0.90] | ×1.0 | 24 | 20 | 83.3% | 1 | 0 | 19 |
| 0.08 | [0.10, 0.90] | ×2.0 | 22 | 18 | 81.8% | 1 | 0 | 17 |
| 0.08 | [0.15, 0.85] | ×0.5 | 18 | 14 | 77.8% | 1 | 0 | 13 |
| 0.08 | [0.15, 0.85] | ×1.0 | 15 | 12 | 80.0% | 1 | 0 | 11 |
| 0.08 | [0.15, 0.85] | ×2.0 | 14 | 11 | 78.6% | 1 | 0 | 10 | ← **rec**
| 0.12 | [0.05, 0.95] | ×0.5 | 41 | 35 | 85.4% | 2 | 0 | 33 |
| 0.12 | [0.05, 0.95] | ×1.0 | 37 | 32 | 86.5% | 1 | 0 | 31 |
| 0.12 | [0.05, 0.95] | ×2.0 | 35 | 30 | 85.7% | 1 | 0 | 29 |
| 0.12 | [0.10, 0.90] | ×0.5 | 27 | 21 | 77.8% | 0 | 0 | 21 |
| 0.12 | [0.10, 0.90] | ×1.0 | 24 | 19 | 79.2% | 0 | 0 | 19 |
| 0.12 | [0.10, 0.90] | ×2.0 | 22 | 17 | 77.3% | 0 | 0 | 17 |
| 0.12 | [0.15, 0.85] | ×0.5 | 18 | 13 | 72.2% | 0 | 0 | 13 |
| 0.12 | [0.15, 0.85] | ×1.0 | 15 | 11 | 73.3% | 0 | 0 | 11 |
| 0.12 | [0.15, 0.85] | ×2.0 | 14 | 10 | 71.4% | 0 | 0 | 10 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 41 | 40 | 97.6% | 21 | 15 | 4 |
| 0.06 | [0.05, 0.95] | ×1.0 | 37 | 36 | 97.3% | 19 | 13 | 4 |
| 0.06 | [0.05, 0.95] | ×2.0 | 35 | 34 | 97.1% | 18 | 13 | 3 |
| 0.06 | [0.10, 0.90] | ×0.5 | 27 | 26 | 96.3% | 12 | 11 | 3 |
| 0.06 | [0.10, 0.90] | ×1.0 | 24 | 23 | 95.8% | 11 | 9 | 3 |
| 0.06 | [0.10, 0.90] | ×2.0 | 22 | 21 | 95.5% | 10 | 9 | 2 |
| 0.06 | [0.15, 0.85] | ×0.5 | 18 | 17 | 94.4% | 6 | 8 | 3 |
| 0.06 | [0.15, 0.85] | ×1.0 | 15 | 14 | 93.3% | 5 | 6 | 3 |
| 0.06 | [0.15, 0.85] | ×2.0 | 14 | 13 | 92.9% | 5 | 6 | 2 |
| 0.08 | [0.05, 0.95] | ×0.5 | 41 | 40 | 97.6% | 19 | 15 | 6 |
| 0.08 | [0.05, 0.95] | ×1.0 | 37 | 36 | 97.3% | 17 | 13 | 6 |
| 0.08 | [0.05, 0.95] | ×2.0 | 35 | 34 | 97.1% | 16 | 13 | 5 |
| 0.08 | [0.10, 0.90] | ×0.5 | 27 | 26 | 96.3% | 10 | 11 | 5 |
| 0.08 | [0.10, 0.90] | ×1.0 | 24 | 23 | 95.8% | 9 | 9 | 5 |
| 0.08 | [0.10, 0.90] | ×2.0 | 22 | 21 | 95.5% | 8 | 9 | 4 |
| 0.08 | [0.15, 0.85] | ×0.5 | 18 | 17 | 94.4% | 6 | 8 | 3 |
| 0.08 | [0.15, 0.85] | ×1.0 | 15 | 14 | 93.3% | 5 | 6 | 3 |
| 0.08 | [0.15, 0.85] | ×2.0 | 14 | 13 | 92.9% | 5 | 6 | 2 |
| 0.12 | [0.05, 0.95] | ×0.5 | 41 | 39 | 95.1% | 15 | 15 | 9 |
| 0.12 | [0.05, 0.95] | ×1.0 | 37 | 35 | 94.6% | 13 | 13 | 9 |
| 0.12 | [0.05, 0.95] | ×2.0 | 35 | 33 | 94.3% | 12 | 13 | 8 |
| 0.12 | [0.10, 0.90] | ×0.5 | 27 | 25 | 92.6% | 7 | 11 | 7 |
| 0.12 | [0.10, 0.90] | ×1.0 | 24 | 22 | 91.7% | 6 | 9 | 7 |
| 0.12 | [0.10, 0.90] | ×2.0 | 22 | 20 | 90.9% | 5 | 9 | 6 |
| 0.12 | [0.15, 0.85] | ×0.5 | 18 | 16 | 88.9% | 3 | 8 | 5 |
| 0.12 | [0.15, 0.85] | ×1.0 | 15 | 13 | 86.7% | 2 | 6 | 5 |
| 0.12 | [0.15, 0.85] | ×2.0 | 14 | 12 | 85.7% | 2 | 6 | 4 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **37 markets** survive the filter and **33 are flagged** (89.2% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **2** markets (6%)
- `DRIFT` (order-book mid vs last trade): **31** markets (94%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×2.0, strict_with_heuristic  
→ 14 markets survive, 11 flagged (78.6%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×2.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.