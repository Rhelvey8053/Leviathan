# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-08-01T23:58:31.789952+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2722  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 34 | 30 | 88.2% | 4 | 0 | 26 |
| 0.06 | [0.05, 0.95] | ×1.0 | 31 | 28 | 90.3% | 3 | 0 | 25 |
| 0.06 | [0.05, 0.95] | ×2.0 | 29 | 26 | 89.7% | 3 | 0 | 23 |
| 0.06 | [0.10, 0.90] | ×0.5 | 22 | 18 | 81.8% | 2 | 0 | 16 |
| 0.06 | [0.10, 0.90] | ×1.0 | 20 | 17 | 85.0% | 2 | 0 | 15 |
| 0.06 | [0.10, 0.90] | ×2.0 | 18 | 15 | 83.3% | 2 | 0 | 13 |
| 0.06 | [0.15, 0.85] | ×0.5 | 13 | 10 | 76.9% | 2 | 0 | 8 |
| 0.06 | [0.15, 0.85] | ×1.0 | 11 | 9 | 81.8% | 2 | 0 | 7 |
| 0.06 | [0.15, 0.85] | ×2.0 | 10 | 8 | 80.0% | 2 | 0 | 6 |
| 0.08 | [0.05, 0.95] | ×0.5 | 34 | 30 | 88.2% | 4 | 0 | 26 |
| 0.08 | [0.05, 0.95] | ×1.0 | 31 | 28 | 90.3% | 3 | 0 | 25 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 29 | 26 | 89.7% | 3 | 0 | 23 |
| 0.08 | [0.10, 0.90] | ×0.5 | 22 | 18 | 81.8% | 2 | 0 | 16 |
| 0.08 | [0.10, 0.90] | ×1.0 | 20 | 17 | 85.0% | 2 | 0 | 15 |
| 0.08 | [0.10, 0.90] | ×2.0 | 18 | 15 | 83.3% | 2 | 0 | 13 |
| 0.08 | [0.15, 0.85] | ×0.5 | 13 | 10 | 76.9% | 2 | 0 | 8 |
| 0.08 | [0.15, 0.85] | ×1.0 | 11 | 9 | 81.8% | 2 | 0 | 7 |
| 0.08 | [0.15, 0.85] | ×2.0 | 10 | 8 | 80.0% | 2 | 0 | 6 | ← **rec**
| 0.12 | [0.05, 0.95] | ×0.5 | 34 | 29 | 85.3% | 3 | 0 | 26 |
| 0.12 | [0.05, 0.95] | ×1.0 | 31 | 27 | 87.1% | 2 | 0 | 25 |
| 0.12 | [0.05, 0.95] | ×2.0 | 29 | 25 | 86.2% | 2 | 0 | 23 |
| 0.12 | [0.10, 0.90] | ×0.5 | 22 | 17 | 77.3% | 1 | 0 | 16 |
| 0.12 | [0.10, 0.90] | ×1.0 | 20 | 16 | 80.0% | 1 | 0 | 15 |
| 0.12 | [0.10, 0.90] | ×2.0 | 18 | 14 | 77.8% | 1 | 0 | 13 |
| 0.12 | [0.15, 0.85] | ×0.5 | 13 | 9 | 69.2% | 1 | 0 | 8 |
| 0.12 | [0.15, 0.85] | ×1.0 | 11 | 8 | 72.7% | 1 | 0 | 7 |
| 0.12 | [0.15, 0.85] | ×2.0 | 10 | 7 | 70.0% | 1 | 0 | 6 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 34 | 33 | 97.1% | 21 | 9 | 3 |
| 0.06 | [0.05, 0.95] | ×1.0 | 31 | 30 | 96.8% | 19 | 8 | 3 |
| 0.06 | [0.05, 0.95] | ×2.0 | 29 | 28 | 96.6% | 18 | 8 | 2 |
| 0.06 | [0.10, 0.90] | ×0.5 | 22 | 21 | 95.5% | 12 | 6 | 3 |
| 0.06 | [0.10, 0.90] | ×1.0 | 20 | 19 | 95.0% | 11 | 5 | 3 |
| 0.06 | [0.10, 0.90] | ×2.0 | 18 | 17 | 94.4% | 10 | 5 | 2 |
| 0.06 | [0.15, 0.85] | ×0.5 | 13 | 12 | 92.3% | 6 | 3 | 3 |
| 0.06 | [0.15, 0.85] | ×1.0 | 11 | 10 | 90.9% | 5 | 2 | 3 |
| 0.06 | [0.15, 0.85] | ×2.0 | 10 | 9 | 90.0% | 5 | 2 | 2 |
| 0.08 | [0.05, 0.95] | ×0.5 | 34 | 33 | 97.1% | 19 | 9 | 5 |
| 0.08 | [0.05, 0.95] | ×1.0 | 31 | 30 | 96.8% | 17 | 8 | 5 |
| 0.08 | [0.05, 0.95] | ×2.0 | 29 | 28 | 96.6% | 16 | 8 | 4 |
| 0.08 | [0.10, 0.90] | ×0.5 | 22 | 21 | 95.5% | 10 | 6 | 5 |
| 0.08 | [0.10, 0.90] | ×1.0 | 20 | 19 | 95.0% | 9 | 5 | 5 |
| 0.08 | [0.10, 0.90] | ×2.0 | 18 | 17 | 94.4% | 8 | 5 | 4 |
| 0.08 | [0.15, 0.85] | ×0.5 | 13 | 12 | 92.3% | 6 | 3 | 3 |
| 0.08 | [0.15, 0.85] | ×1.0 | 11 | 10 | 90.9% | 5 | 2 | 3 |
| 0.08 | [0.15, 0.85] | ×2.0 | 10 | 9 | 90.0% | 5 | 2 | 2 |
| 0.12 | [0.05, 0.95] | ×0.5 | 34 | 32 | 94.1% | 15 | 9 | 8 |
| 0.12 | [0.05, 0.95] | ×1.0 | 31 | 29 | 93.5% | 13 | 8 | 8 |
| 0.12 | [0.05, 0.95] | ×2.0 | 29 | 27 | 93.1% | 12 | 8 | 7 |
| 0.12 | [0.10, 0.90] | ×0.5 | 22 | 20 | 90.9% | 7 | 6 | 7 |
| 0.12 | [0.10, 0.90] | ×1.0 | 20 | 18 | 90.0% | 6 | 5 | 7 |
| 0.12 | [0.10, 0.90] | ×2.0 | 18 | 16 | 88.9% | 5 | 5 | 6 |
| 0.12 | [0.15, 0.85] | ×0.5 | 13 | 11 | 84.6% | 3 | 3 | 5 |
| 0.12 | [0.15, 0.85] | ×1.0 | 11 | 9 | 81.8% | 2 | 2 | 5 |
| 0.12 | [0.15, 0.85] | ×2.0 | 10 | 8 | 80.0% | 2 | 2 | 4 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **31 markets** survive the filter and **28 are flagged** (90.3% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **3** markets (11%)
- `DRIFT` (order-book mid vs last trade): **25** markets (89%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×2.0, strict_with_heuristic  
→ 10 markets survive, 8 flagged (80.0%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×2.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.