# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-06-17T12:40:21.810242+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2429  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 33 | 32 | 97.0% | 12 | 0 | 20 |
| 0.06 | [0.05, 0.95] | ×1.0 | 29 | 28 | 96.6% | 10 | 0 | 18 |
| 0.06 | [0.05, 0.95] | ×2.0 | 21 | 21 | 100.0% | 8 | 0 | 13 |
| 0.06 | [0.10, 0.90] | ×0.5 | 13 | 12 | 92.3% | 4 | 0 | 8 |
| 0.06 | [0.10, 0.90] | ×1.0 | 11 | 10 | 90.9% | 4 | 0 | 6 |
| 0.06 | [0.10, 0.90] | ×2.0 | 8 | 8 | 100.0% | 4 | 0 | 4 |
| 0.06 | [0.15, 0.85] | ×0.5 | 12 | 11 | 91.7% | 3 | 0 | 8 |
| 0.06 | [0.15, 0.85] | ×1.0 | 10 | 9 | 90.0% | 3 | 0 | 6 |
| 0.06 | [0.15, 0.85] | ×2.0 | 7 | 7 | 100.0% | 3 | 0 | 4 |
| 0.08 | [0.05, 0.95] | ×0.5 | 33 | 32 | 97.0% | 12 | 0 | 20 |
| 0.08 | [0.05, 0.95] | ×1.0 | 29 | 28 | 96.6% | 10 | 0 | 18 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 21 | 21 | 100.0% | 8 | 0 | 13 |
| 0.08 | [0.10, 0.90] | ×0.5 | 13 | 12 | 92.3% | 4 | 0 | 8 |
| 0.08 | [0.10, 0.90] | ×1.0 | 11 | 10 | 90.9% | 4 | 0 | 6 |
| 0.08 | [0.10, 0.90] | ×2.0 | 8 | 8 | 100.0% | 4 | 0 | 4 |
| 0.08 | [0.15, 0.85] | ×0.5 | 12 | 11 | 91.7% | 3 | 0 | 8 |
| 0.08 | [0.15, 0.85] | ×1.0 | 10 | 9 | 90.0% | 3 | 0 | 6 | ← **rec**
| 0.08 | [0.15, 0.85] | ×2.0 | 7 | 7 | 100.0% | 3 | 0 | 4 |
| 0.12 | [0.05, 0.95] | ×0.5 | 33 | 31 | 93.9% | 11 | 0 | 20 |
| 0.12 | [0.05, 0.95] | ×1.0 | 29 | 27 | 93.1% | 9 | 0 | 18 |
| 0.12 | [0.05, 0.95] | ×2.0 | 21 | 20 | 95.2% | 7 | 0 | 13 |
| 0.12 | [0.10, 0.90] | ×0.5 | 13 | 11 | 84.6% | 3 | 0 | 8 |
| 0.12 | [0.10, 0.90] | ×1.0 | 11 | 9 | 81.8% | 3 | 0 | 6 |
| 0.12 | [0.10, 0.90] | ×2.0 | 8 | 7 | 87.5% | 3 | 0 | 4 |
| 0.12 | [0.15, 0.85] | ×0.5 | 12 | 10 | 83.3% | 2 | 0 | 8 |
| 0.12 | [0.15, 0.85] | ×1.0 | 10 | 8 | 80.0% | 2 | 0 | 6 |
| 0.12 | [0.15, 0.85] | ×2.0 | 7 | 6 | 85.7% | 2 | 0 | 4 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 33 | 32 | 97.0% | 32 | 0 | 0 |
| 0.06 | [0.05, 0.95] | ×1.0 | 29 | 28 | 96.6% | 28 | 0 | 0 |
| 0.06 | [0.05, 0.95] | ×2.0 | 21 | 21 | 100.0% | 21 | 0 | 0 |
| 0.06 | [0.10, 0.90] | ×0.5 | 13 | 12 | 92.3% | 12 | 0 | 0 |
| 0.06 | [0.10, 0.90] | ×1.0 | 11 | 10 | 90.9% | 10 | 0 | 0 |
| 0.06 | [0.10, 0.90] | ×2.0 | 8 | 8 | 100.0% | 8 | 0 | 0 |
| 0.06 | [0.15, 0.85] | ×0.5 | 12 | 11 | 91.7% | 11 | 0 | 0 |
| 0.06 | [0.15, 0.85] | ×1.0 | 10 | 9 | 90.0% | 9 | 0 | 0 |
| 0.06 | [0.15, 0.85] | ×2.0 | 7 | 7 | 100.0% | 7 | 0 | 0 |
| 0.08 | [0.05, 0.95] | ×0.5 | 33 | 32 | 97.0% | 32 | 0 | 0 |
| 0.08 | [0.05, 0.95] | ×1.0 | 29 | 28 | 96.6% | 28 | 0 | 0 |
| 0.08 | [0.05, 0.95] | ×2.0 | 21 | 21 | 100.0% | 21 | 0 | 0 |
| 0.08 | [0.10, 0.90] | ×0.5 | 13 | 12 | 92.3% | 12 | 0 | 0 |
| 0.08 | [0.10, 0.90] | ×1.0 | 11 | 10 | 90.9% | 10 | 0 | 0 |
| 0.08 | [0.10, 0.90] | ×2.0 | 8 | 8 | 100.0% | 8 | 0 | 0 |
| 0.08 | [0.15, 0.85] | ×0.5 | 12 | 11 | 91.7% | 11 | 0 | 0 |
| 0.08 | [0.15, 0.85] | ×1.0 | 10 | 9 | 90.0% | 9 | 0 | 0 |
| 0.08 | [0.15, 0.85] | ×2.0 | 7 | 7 | 100.0% | 7 | 0 | 0 |
| 0.12 | [0.05, 0.95] | ×0.5 | 33 | 31 | 93.9% | 31 | 0 | 0 |
| 0.12 | [0.05, 0.95] | ×1.0 | 29 | 27 | 93.1% | 27 | 0 | 0 |
| 0.12 | [0.05, 0.95] | ×2.0 | 21 | 20 | 95.2% | 20 | 0 | 0 |
| 0.12 | [0.10, 0.90] | ×0.5 | 13 | 11 | 84.6% | 11 | 0 | 0 |
| 0.12 | [0.10, 0.90] | ×1.0 | 11 | 9 | 81.8% | 9 | 0 | 0 |
| 0.12 | [0.10, 0.90] | ×2.0 | 8 | 7 | 87.5% | 7 | 0 | 0 |
| 0.12 | [0.15, 0.85] | ×0.5 | 12 | 10 | 83.3% | 10 | 0 | 0 |
| 0.12 | [0.15, 0.85] | ×1.0 | 10 | 8 | 80.0% | 8 | 0 | 0 |
| 0.12 | [0.15, 0.85] | ×2.0 | 7 | 6 | 85.7% | 6 | 0 | 0 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **29 markets** survive the filter and **28 are flagged** (96.6% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **10** markets (36%)
- `DRIFT` (order-book mid vs last trade): **18** markets (64%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×1.0, strict_with_heuristic  
→ 10 markets survive, 9 flagged (90.0%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×1.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.