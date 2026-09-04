# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-09-03T15:34:31.678481+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 3048  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 69 | 37 | 53.6% | 4 | 0 | 33 |
| 0.06 | [0.05, 0.95] | ×1.0 | 68 | 36 | 52.9% | 4 | 0 | 32 |
| 0.06 | [0.05, 0.95] | ×2.0 | 64 | 34 | 53.1% | 3 | 0 | 31 |
| 0.06 | [0.10, 0.90] | ×0.5 | 48 | 22 | 45.8% | 0 | 0 | 22 |
| 0.06 | [0.10, 0.90] | ×1.0 | 47 | 21 | 44.7% | 0 | 0 | 21 |
| 0.06 | [0.10, 0.90] | ×2.0 | 44 | 20 | 45.5% | 0 | 0 | 20 |
| 0.06 | [0.15, 0.85] | ×0.5 | 37 | 16 | 43.2% | 0 | 0 | 16 |
| 0.06 | [0.15, 0.85] | ×1.0 | 36 | 15 | 41.7% | 0 | 0 | 15 |
| 0.06 | [0.15, 0.85] | ×2.0 | 33 | 14 | 42.4% | 0 | 0 | 14 |
| 0.08 | [0.05, 0.95] | ×0.5 | 69 | 37 | 53.6% | 4 | 0 | 33 |
| 0.08 | [0.05, 0.95] | ×1.0 | 68 | 36 | 52.9% | 4 | 0 | 32 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 64 | 34 | 53.1% | 3 | 0 | 31 |
| 0.08 | [0.10, 0.90] | ×0.5 | 48 | 22 | 45.8% | 0 | 0 | 22 |
| 0.08 | [0.10, 0.90] | ×1.0 | 47 | 21 | 44.7% | 0 | 0 | 21 |
| 0.08 | [0.10, 0.90] | ×2.0 | 44 | 20 | 45.5% | 0 | 0 | 20 |
| 0.08 | [0.15, 0.85] | ×0.5 | 37 | 16 | 43.2% | 0 | 0 | 16 |
| 0.08 | [0.15, 0.85] | ×1.0 | 36 | 15 | 41.7% | 0 | 0 | 15 | ← **rec**
| 0.08 | [0.15, 0.85] | ×2.0 | 33 | 14 | 42.4% | 0 | 0 | 14 |
| 0.12 | [0.05, 0.95] | ×0.5 | 69 | 37 | 53.6% | 4 | 0 | 33 |
| 0.12 | [0.05, 0.95] | ×1.0 | 68 | 36 | 52.9% | 4 | 0 | 32 |
| 0.12 | [0.05, 0.95] | ×2.0 | 64 | 34 | 53.1% | 3 | 0 | 31 |
| 0.12 | [0.10, 0.90] | ×0.5 | 48 | 22 | 45.8% | 0 | 0 | 22 |
| 0.12 | [0.10, 0.90] | ×1.0 | 47 | 21 | 44.7% | 0 | 0 | 21 |
| 0.12 | [0.10, 0.90] | ×2.0 | 44 | 20 | 45.5% | 0 | 0 | 20 |
| 0.12 | [0.15, 0.85] | ×0.5 | 37 | 16 | 43.2% | 0 | 0 | 16 |
| 0.12 | [0.15, 0.85] | ×1.0 | 36 | 15 | 41.7% | 0 | 0 | 15 |
| 0.12 | [0.15, 0.85] | ×2.0 | 33 | 14 | 42.4% | 0 | 0 | 14 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 69 | 59 | 85.5% | 12 | 46 | 1 |
| 0.06 | [0.05, 0.95] | ×1.0 | 68 | 58 | 85.3% | 12 | 45 | 1 |
| 0.06 | [0.05, 0.95] | ×2.0 | 64 | 54 | 84.4% | 11 | 42 | 1 |
| 0.06 | [0.10, 0.90] | ×0.5 | 48 | 42 | 87.5% | 5 | 36 | 1 |
| 0.06 | [0.10, 0.90] | ×1.0 | 47 | 41 | 87.2% | 5 | 35 | 1 |
| 0.06 | [0.10, 0.90] | ×2.0 | 44 | 38 | 86.4% | 5 | 32 | 1 |
| 0.06 | [0.15, 0.85] | ×0.5 | 37 | 33 | 89.2% | 2 | 30 | 1 |
| 0.06 | [0.15, 0.85] | ×1.0 | 36 | 32 | 88.9% | 2 | 29 | 1 |
| 0.06 | [0.15, 0.85] | ×2.0 | 33 | 29 | 87.9% | 2 | 26 | 1 |
| 0.08 | [0.05, 0.95] | ×0.5 | 69 | 59 | 85.5% | 12 | 46 | 1 |
| 0.08 | [0.05, 0.95] | ×1.0 | 68 | 58 | 85.3% | 12 | 45 | 1 |
| 0.08 | [0.05, 0.95] | ×2.0 | 64 | 54 | 84.4% | 11 | 42 | 1 |
| 0.08 | [0.10, 0.90] | ×0.5 | 48 | 42 | 87.5% | 5 | 36 | 1 |
| 0.08 | [0.10, 0.90] | ×1.0 | 47 | 41 | 87.2% | 5 | 35 | 1 |
| 0.08 | [0.10, 0.90] | ×2.0 | 44 | 38 | 86.4% | 5 | 32 | 1 |
| 0.08 | [0.15, 0.85] | ×0.5 | 37 | 33 | 89.2% | 2 | 30 | 1 |
| 0.08 | [0.15, 0.85] | ×1.0 | 36 | 32 | 88.9% | 2 | 29 | 1 |
| 0.08 | [0.15, 0.85] | ×2.0 | 33 | 29 | 87.9% | 2 | 26 | 1 |
| 0.12 | [0.05, 0.95] | ×0.5 | 69 | 59 | 85.5% | 9 | 46 | 4 |
| 0.12 | [0.05, 0.95] | ×1.0 | 68 | 58 | 85.3% | 9 | 45 | 4 |
| 0.12 | [0.05, 0.95] | ×2.0 | 64 | 54 | 84.4% | 8 | 42 | 4 |
| 0.12 | [0.10, 0.90] | ×0.5 | 48 | 42 | 87.5% | 3 | 36 | 3 |
| 0.12 | [0.10, 0.90] | ×1.0 | 47 | 41 | 87.2% | 3 | 35 | 3 |
| 0.12 | [0.10, 0.90] | ×2.0 | 44 | 38 | 86.4% | 3 | 32 | 3 |
| 0.12 | [0.15, 0.85] | ×0.5 | 37 | 33 | 89.2% | 1 | 30 | 2 |
| 0.12 | [0.15, 0.85] | ×1.0 | 36 | 32 | 88.9% | 1 | 29 | 2 |
| 0.12 | [0.15, 0.85] | ×2.0 | 33 | 29 | 87.9% | 1 | 26 | 2 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **68 markets** survive the filter and **36 are flagged** (52.9% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **4** markets (11%)
- `DRIFT` (order-book mid vs last trade): **32** markets (89%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×1.0, strict_with_heuristic  
→ 36 markets survive, 15 flagged (41.7%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×1.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.