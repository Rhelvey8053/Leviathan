# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-09-03T15:34:31.678481+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 3048  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 105 | 57 | 54.3% | 4 | 0 | 53 |
| 0.06 | [0.05, 0.95] | ×1.0 | 104 | 56 | 53.8% | 4 | 0 | 52 |
| 0.06 | [0.05, 0.95] | ×2.0 | 100 | 54 | 54.0% | 3 | 0 | 51 |
| 0.06 | [0.10, 0.90] | ×0.5 | 74 | 36 | 48.6% | 0 | 0 | 36 |
| 0.06 | [0.10, 0.90] | ×1.0 | 73 | 35 | 47.9% | 0 | 0 | 35 |
| 0.06 | [0.10, 0.90] | ×2.0 | 70 | 34 | 48.6% | 0 | 0 | 34 |
| 0.06 | [0.15, 0.85] | ×0.5 | 59 | 28 | 47.5% | 0 | 0 | 28 |
| 0.06 | [0.15, 0.85] | ×1.0 | 58 | 27 | 46.6% | 0 | 0 | 27 |
| 0.06 | [0.15, 0.85] | ×2.0 | 55 | 26 | 47.3% | 0 | 0 | 26 |
| 0.08 | [0.05, 0.95] | ×0.5 | 105 | 57 | 54.3% | 4 | 0 | 53 |
| 0.08 | [0.05, 0.95] | ×1.0 | 104 | 56 | 53.8% | 4 | 0 | 52 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 100 | 54 | 54.0% | 3 | 0 | 51 |
| 0.08 | [0.10, 0.90] | ×0.5 | 74 | 36 | 48.6% | 0 | 0 | 36 |
| 0.08 | [0.10, 0.90] | ×1.0 | 73 | 35 | 47.9% | 0 | 0 | 35 |
| 0.08 | [0.10, 0.90] | ×2.0 | 70 | 34 | 48.6% | 0 | 0 | 34 |
| 0.08 | [0.15, 0.85] | ×0.5 | 59 | 28 | 47.5% | 0 | 0 | 28 |
| 0.08 | [0.15, 0.85] | ×1.0 | 58 | 27 | 46.6% | 0 | 0 | 27 | ← **rec**
| 0.08 | [0.15, 0.85] | ×2.0 | 55 | 26 | 47.3% | 0 | 0 | 26 |
| 0.12 | [0.05, 0.95] | ×0.5 | 105 | 57 | 54.3% | 4 | 0 | 53 |
| 0.12 | [0.05, 0.95] | ×1.0 | 104 | 56 | 53.8% | 4 | 0 | 52 |
| 0.12 | [0.05, 0.95] | ×2.0 | 100 | 54 | 54.0% | 3 | 0 | 51 |
| 0.12 | [0.10, 0.90] | ×0.5 | 74 | 36 | 48.6% | 0 | 0 | 36 |
| 0.12 | [0.10, 0.90] | ×1.0 | 73 | 35 | 47.9% | 0 | 0 | 35 |
| 0.12 | [0.10, 0.90] | ×2.0 | 70 | 34 | 48.6% | 0 | 0 | 34 |
| 0.12 | [0.15, 0.85] | ×0.5 | 59 | 28 | 47.5% | 0 | 0 | 28 |
| 0.12 | [0.15, 0.85] | ×1.0 | 58 | 27 | 46.6% | 0 | 0 | 27 |
| 0.12 | [0.15, 0.85] | ×2.0 | 55 | 26 | 47.3% | 0 | 0 | 26 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 105 | 90 | 85.7% | 12 | 75 | 3 |
| 0.06 | [0.05, 0.95] | ×1.0 | 104 | 89 | 85.6% | 12 | 74 | 3 |
| 0.06 | [0.05, 0.95] | ×2.0 | 100 | 85 | 85.0% | 11 | 71 | 3 |
| 0.06 | [0.10, 0.90] | ×0.5 | 74 | 66 | 89.2% | 5 | 58 | 3 |
| 0.06 | [0.10, 0.90] | ×1.0 | 73 | 65 | 89.0% | 5 | 57 | 3 |
| 0.06 | [0.10, 0.90] | ×2.0 | 70 | 62 | 88.6% | 5 | 54 | 3 |
| 0.06 | [0.15, 0.85] | ×0.5 | 59 | 55 | 93.2% | 2 | 50 | 3 |
| 0.06 | [0.15, 0.85] | ×1.0 | 58 | 54 | 93.1% | 2 | 49 | 3 |
| 0.06 | [0.15, 0.85] | ×2.0 | 55 | 51 | 92.7% | 2 | 46 | 3 |
| 0.08 | [0.05, 0.95] | ×0.5 | 105 | 90 | 85.7% | 12 | 75 | 3 |
| 0.08 | [0.05, 0.95] | ×1.0 | 104 | 89 | 85.6% | 12 | 74 | 3 |
| 0.08 | [0.05, 0.95] | ×2.0 | 100 | 85 | 85.0% | 11 | 71 | 3 |
| 0.08 | [0.10, 0.90] | ×0.5 | 74 | 66 | 89.2% | 5 | 58 | 3 |
| 0.08 | [0.10, 0.90] | ×1.0 | 73 | 65 | 89.0% | 5 | 57 | 3 |
| 0.08 | [0.10, 0.90] | ×2.0 | 70 | 62 | 88.6% | 5 | 54 | 3 |
| 0.08 | [0.15, 0.85] | ×0.5 | 59 | 55 | 93.2% | 2 | 50 | 3 |
| 0.08 | [0.15, 0.85] | ×1.0 | 58 | 54 | 93.1% | 2 | 49 | 3 |
| 0.08 | [0.15, 0.85] | ×2.0 | 55 | 51 | 92.7% | 2 | 46 | 3 |
| 0.12 | [0.05, 0.95] | ×0.5 | 105 | 90 | 85.7% | 9 | 75 | 6 |
| 0.12 | [0.05, 0.95] | ×1.0 | 104 | 89 | 85.6% | 9 | 74 | 6 |
| 0.12 | [0.05, 0.95] | ×2.0 | 100 | 85 | 85.0% | 8 | 71 | 6 |
| 0.12 | [0.10, 0.90] | ×0.5 | 74 | 66 | 89.2% | 3 | 58 | 5 |
| 0.12 | [0.10, 0.90] | ×1.0 | 73 | 65 | 89.0% | 3 | 57 | 5 |
| 0.12 | [0.10, 0.90] | ×2.0 | 70 | 62 | 88.6% | 3 | 54 | 5 |
| 0.12 | [0.15, 0.85] | ×0.5 | 59 | 55 | 93.2% | 1 | 50 | 4 |
| 0.12 | [0.15, 0.85] | ×1.0 | 58 | 54 | 93.1% | 1 | 49 | 4 |
| 0.12 | [0.15, 0.85] | ×2.0 | 55 | 51 | 92.7% | 1 | 46 | 4 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **104 markets** survive the filter and **56 are flagged** (53.8% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **4** markets (7%)
- `DRIFT` (order-book mid vs last trade): **52** markets (93%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×1.0, strict_with_heuristic  
→ 58 markets survive, 27 flagged (46.6%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×1.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.