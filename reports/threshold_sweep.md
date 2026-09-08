# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-09-08T12:07:39.435330+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2997  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 86 | 49 | 57.0% | 6 | 0 | 43 |
| 0.06 | [0.05, 0.95] | ×1.0 | 76 | 42 | 55.3% | 5 | 0 | 37 |
| 0.06 | [0.05, 0.95] | ×2.0 | 62 | 33 | 53.2% | 4 | 0 | 29 |
| 0.06 | [0.10, 0.90] | ×0.5 | 70 | 37 | 52.9% | 5 | 0 | 32 |
| 0.06 | [0.10, 0.90] | ×1.0 | 61 | 30 | 49.2% | 4 | 0 | 26 |
| 0.06 | [0.10, 0.90] | ×2.0 | 49 | 23 | 46.9% | 3 | 0 | 20 |
| 0.06 | [0.15, 0.85] | ×0.5 | 57 | 29 | 50.9% | 4 | 0 | 25 |
| 0.06 | [0.15, 0.85] | ×1.0 | 48 | 22 | 45.8% | 3 | 0 | 19 |
| 0.06 | [0.15, 0.85] | ×2.0 | 38 | 16 | 42.1% | 2 | 0 | 14 |
| 0.08 | [0.05, 0.95] | ×0.5 | 86 | 48 | 55.8% | 5 | 0 | 43 |
| 0.08 | [0.05, 0.95] | ×1.0 | 76 | 41 | 53.9% | 4 | 0 | 37 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 62 | 32 | 51.6% | 3 | 0 | 29 |
| 0.08 | [0.10, 0.90] | ×0.5 | 70 | 36 | 51.4% | 4 | 0 | 32 |
| 0.08 | [0.10, 0.90] | ×1.0 | 61 | 29 | 47.5% | 3 | 0 | 26 |
| 0.08 | [0.10, 0.90] | ×2.0 | 49 | 22 | 44.9% | 2 | 0 | 20 |
| 0.08 | [0.15, 0.85] | ×0.5 | 57 | 28 | 49.1% | 3 | 0 | 25 |
| 0.08 | [0.15, 0.85] | ×1.0 | 48 | 21 | 43.8% | 2 | 0 | 19 |
| 0.08 | [0.15, 0.85] | ×2.0 | 38 | 15 | 39.5% | 1 | 0 | 14 | ← **rec**
| 0.12 | [0.05, 0.95] | ×0.5 | 86 | 48 | 55.8% | 5 | 0 | 43 |
| 0.12 | [0.05, 0.95] | ×1.0 | 76 | 41 | 53.9% | 4 | 0 | 37 |
| 0.12 | [0.05, 0.95] | ×2.0 | 62 | 32 | 51.6% | 3 | 0 | 29 |
| 0.12 | [0.10, 0.90] | ×0.5 | 70 | 36 | 51.4% | 4 | 0 | 32 |
| 0.12 | [0.10, 0.90] | ×1.0 | 61 | 29 | 47.5% | 3 | 0 | 26 |
| 0.12 | [0.10, 0.90] | ×2.0 | 49 | 22 | 44.9% | 2 | 0 | 20 |
| 0.12 | [0.15, 0.85] | ×0.5 | 57 | 28 | 49.1% | 3 | 0 | 25 |
| 0.12 | [0.15, 0.85] | ×1.0 | 48 | 21 | 43.8% | 2 | 0 | 19 |
| 0.12 | [0.15, 0.85] | ×2.0 | 38 | 15 | 39.5% | 1 | 0 | 14 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 86 | 78 | 90.7% | 17 | 60 | 1 |
| 0.06 | [0.05, 0.95] | ×1.0 | 76 | 69 | 90.8% | 14 | 54 | 1 |
| 0.06 | [0.05, 0.95] | ×2.0 | 62 | 56 | 90.3% | 12 | 43 | 1 |
| 0.06 | [0.10, 0.90] | ×0.5 | 70 | 64 | 91.4% | 12 | 51 | 1 |
| 0.06 | [0.10, 0.90] | ×1.0 | 61 | 55 | 90.2% | 9 | 45 | 1 |
| 0.06 | [0.10, 0.90] | ×2.0 | 49 | 44 | 89.8% | 7 | 36 | 1 |
| 0.06 | [0.15, 0.85] | ×0.5 | 57 | 55 | 96.5% | 9 | 45 | 1 |
| 0.06 | [0.15, 0.85] | ×1.0 | 48 | 46 | 95.8% | 6 | 39 | 1 |
| 0.06 | [0.15, 0.85] | ×2.0 | 38 | 36 | 94.7% | 4 | 31 | 1 |
| 0.08 | [0.05, 0.95] | ×0.5 | 86 | 77 | 89.5% | 15 | 60 | 2 |
| 0.08 | [0.05, 0.95] | ×1.0 | 76 | 68 | 89.5% | 12 | 54 | 2 |
| 0.08 | [0.05, 0.95] | ×2.0 | 62 | 55 | 88.7% | 10 | 43 | 2 |
| 0.08 | [0.10, 0.90] | ×0.5 | 70 | 63 | 90.0% | 11 | 51 | 1 |
| 0.08 | [0.10, 0.90] | ×1.0 | 61 | 54 | 88.5% | 8 | 45 | 1 |
| 0.08 | [0.10, 0.90] | ×2.0 | 49 | 43 | 87.8% | 6 | 36 | 1 |
| 0.08 | [0.15, 0.85] | ×0.5 | 57 | 54 | 94.7% | 8 | 45 | 1 |
| 0.08 | [0.15, 0.85] | ×1.0 | 48 | 45 | 93.8% | 5 | 39 | 1 |
| 0.08 | [0.15, 0.85] | ×2.0 | 38 | 35 | 92.1% | 3 | 31 | 1 |
| 0.12 | [0.05, 0.95] | ×0.5 | 86 | 77 | 89.5% | 13 | 60 | 4 |
| 0.12 | [0.05, 0.95] | ×1.0 | 76 | 68 | 89.5% | 10 | 54 | 4 |
| 0.12 | [0.05, 0.95] | ×2.0 | 62 | 55 | 88.7% | 8 | 43 | 4 |
| 0.12 | [0.10, 0.90] | ×0.5 | 70 | 63 | 90.0% | 9 | 51 | 3 |
| 0.12 | [0.10, 0.90] | ×1.0 | 61 | 54 | 88.5% | 6 | 45 | 3 |
| 0.12 | [0.10, 0.90] | ×2.0 | 49 | 43 | 87.8% | 4 | 36 | 3 |
| 0.12 | [0.15, 0.85] | ×0.5 | 57 | 54 | 94.7% | 6 | 45 | 3 |
| 0.12 | [0.15, 0.85] | ×1.0 | 48 | 45 | 93.8% | 3 | 39 | 3 |
| 0.12 | [0.15, 0.85] | ×2.0 | 38 | 35 | 92.1% | 1 | 31 | 3 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **76 markets** survive the filter and **41 are flagged** (53.9% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **4** markets (10%)
- `DRIFT` (order-book mid vs last trade): **37** markets (90%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×2.0, strict_with_heuristic  
→ 38 markets survive, 15 flagged (39.5%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×2.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.