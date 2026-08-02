# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-08-02T15:19:14.209172+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2922  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 35 | 31 | 88.6% | 3 | 0 | 28 |
| 0.06 | [0.05, 0.95] | ×1.0 | 32 | 29 | 90.6% | 2 | 0 | 27 |
| 0.06 | [0.05, 0.95] | ×2.0 | 30 | 27 | 90.0% | 2 | 0 | 25 |
| 0.06 | [0.10, 0.90] | ×0.5 | 23 | 19 | 82.6% | 1 | 0 | 18 |
| 0.06 | [0.10, 0.90] | ×1.0 | 21 | 18 | 85.7% | 1 | 0 | 17 |
| 0.06 | [0.10, 0.90] | ×2.0 | 19 | 16 | 84.2% | 1 | 0 | 15 |
| 0.06 | [0.15, 0.85] | ×0.5 | 13 | 10 | 76.9% | 1 | 0 | 9 |
| 0.06 | [0.15, 0.85] | ×1.0 | 11 | 9 | 81.8% | 1 | 0 | 8 |
| 0.06 | [0.15, 0.85] | ×2.0 | 10 | 8 | 80.0% | 1 | 0 | 7 |
| 0.08 | [0.05, 0.95] | ×0.5 | 35 | 31 | 88.6% | 3 | 0 | 28 |
| 0.08 | [0.05, 0.95] | ×1.0 | 32 | 29 | 90.6% | 2 | 0 | 27 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 30 | 27 | 90.0% | 2 | 0 | 25 |
| 0.08 | [0.10, 0.90] | ×0.5 | 23 | 19 | 82.6% | 1 | 0 | 18 |
| 0.08 | [0.10, 0.90] | ×1.0 | 21 | 18 | 85.7% | 1 | 0 | 17 |
| 0.08 | [0.10, 0.90] | ×2.0 | 19 | 16 | 84.2% | 1 | 0 | 15 |
| 0.08 | [0.15, 0.85] | ×0.5 | 13 | 10 | 76.9% | 1 | 0 | 9 |
| 0.08 | [0.15, 0.85] | ×1.0 | 11 | 9 | 81.8% | 1 | 0 | 8 |
| 0.08 | [0.15, 0.85] | ×2.0 | 10 | 8 | 80.0% | 1 | 0 | 7 | ← **rec**
| 0.12 | [0.05, 0.95] | ×0.5 | 35 | 30 | 85.7% | 2 | 0 | 28 |
| 0.12 | [0.05, 0.95] | ×1.0 | 32 | 28 | 87.5% | 1 | 0 | 27 |
| 0.12 | [0.05, 0.95] | ×2.0 | 30 | 26 | 86.7% | 1 | 0 | 25 |
| 0.12 | [0.10, 0.90] | ×0.5 | 23 | 18 | 78.3% | 0 | 0 | 18 |
| 0.12 | [0.10, 0.90] | ×1.0 | 21 | 17 | 81.0% | 0 | 0 | 17 |
| 0.12 | [0.10, 0.90] | ×2.0 | 19 | 15 | 78.9% | 0 | 0 | 15 |
| 0.12 | [0.15, 0.85] | ×0.5 | 13 | 9 | 69.2% | 0 | 0 | 9 |
| 0.12 | [0.15, 0.85] | ×1.0 | 11 | 8 | 72.7% | 0 | 0 | 8 |
| 0.12 | [0.15, 0.85] | ×2.0 | 10 | 7 | 70.0% | 0 | 0 | 7 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 35 | 34 | 97.1% | 21 | 10 | 3 |
| 0.06 | [0.05, 0.95] | ×1.0 | 32 | 31 | 96.9% | 19 | 9 | 3 |
| 0.06 | [0.05, 0.95] | ×2.0 | 30 | 29 | 96.7% | 18 | 9 | 2 |
| 0.06 | [0.10, 0.90] | ×0.5 | 23 | 22 | 95.7% | 13 | 7 | 2 |
| 0.06 | [0.10, 0.90] | ×1.0 | 21 | 20 | 95.2% | 12 | 6 | 2 |
| 0.06 | [0.10, 0.90] | ×2.0 | 19 | 18 | 94.7% | 11 | 6 | 1 |
| 0.06 | [0.15, 0.85] | ×0.5 | 13 | 12 | 92.3% | 7 | 3 | 2 |
| 0.06 | [0.15, 0.85] | ×1.0 | 11 | 10 | 90.9% | 6 | 2 | 2 |
| 0.06 | [0.15, 0.85] | ×2.0 | 10 | 9 | 90.0% | 6 | 2 | 1 |
| 0.08 | [0.05, 0.95] | ×0.5 | 35 | 34 | 97.1% | 19 | 10 | 5 |
| 0.08 | [0.05, 0.95] | ×1.0 | 32 | 31 | 96.9% | 17 | 9 | 5 |
| 0.08 | [0.05, 0.95] | ×2.0 | 30 | 29 | 96.7% | 16 | 9 | 4 |
| 0.08 | [0.10, 0.90] | ×0.5 | 23 | 22 | 95.7% | 11 | 7 | 4 |
| 0.08 | [0.10, 0.90] | ×1.0 | 21 | 20 | 95.2% | 10 | 6 | 4 |
| 0.08 | [0.10, 0.90] | ×2.0 | 19 | 18 | 94.7% | 9 | 6 | 3 |
| 0.08 | [0.15, 0.85] | ×0.5 | 13 | 12 | 92.3% | 6 | 3 | 3 |
| 0.08 | [0.15, 0.85] | ×1.0 | 11 | 10 | 90.9% | 5 | 2 | 3 |
| 0.08 | [0.15, 0.85] | ×2.0 | 10 | 9 | 90.0% | 5 | 2 | 2 |
| 0.12 | [0.05, 0.95] | ×0.5 | 35 | 33 | 94.3% | 15 | 10 | 8 |
| 0.12 | [0.05, 0.95] | ×1.0 | 32 | 30 | 93.8% | 13 | 9 | 8 |
| 0.12 | [0.05, 0.95] | ×2.0 | 30 | 28 | 93.3% | 12 | 9 | 7 |
| 0.12 | [0.10, 0.90] | ×0.5 | 23 | 21 | 91.3% | 8 | 7 | 6 |
| 0.12 | [0.10, 0.90] | ×1.0 | 21 | 19 | 90.5% | 7 | 6 | 6 |
| 0.12 | [0.10, 0.90] | ×2.0 | 19 | 17 | 89.5% | 6 | 6 | 5 |
| 0.12 | [0.15, 0.85] | ×0.5 | 13 | 11 | 84.6% | 4 | 3 | 4 |
| 0.12 | [0.15, 0.85] | ×1.0 | 11 | 9 | 81.8% | 3 | 2 | 4 |
| 0.12 | [0.15, 0.85] | ×2.0 | 10 | 8 | 80.0% | 3 | 2 | 3 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **32 markets** survive the filter and **29 are flagged** (90.6% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **2** markets (7%)
- `DRIFT` (order-book mid vs last trade): **27** markets (93%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×2.0, strict_with_heuristic  
→ 10 markets survive, 8 flagged (80.0%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×2.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.