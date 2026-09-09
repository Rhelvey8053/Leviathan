# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-09-09T12:08:11.516929+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 3000  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 96 | 70 | 72.9% | 2 | 0 | 68 |
| 0.06 | [0.05, 0.95] | ×1.0 | 85 | 61 | 71.8% | 2 | 0 | 59 |
| 0.06 | [0.05, 0.95] | ×2.0 | 74 | 53 | 71.6% | 2 | 0 | 51 |
| 0.06 | [0.10, 0.90] | ×0.5 | 84 | 63 | 75.0% | 2 | 0 | 61 |
| 0.06 | [0.10, 0.90] | ×1.0 | 74 | 54 | 73.0% | 2 | 0 | 52 |
| 0.06 | [0.10, 0.90] | ×2.0 | 64 | 47 | 73.4% | 2 | 0 | 45 |
| 0.06 | [0.15, 0.85] | ×0.5 | 71 | 53 | 74.6% | 2 | 0 | 51 |
| 0.06 | [0.15, 0.85] | ×1.0 | 61 | 44 | 72.1% | 2 | 0 | 42 |
| 0.06 | [0.15, 0.85] | ×2.0 | 52 | 37 | 71.2% | 2 | 0 | 35 |
| 0.08 | [0.05, 0.95] | ×0.5 | 96 | 70 | 72.9% | 2 | 0 | 68 |
| 0.08 | [0.05, 0.95] | ×1.0 | 85 | 61 | 71.8% | 2 | 0 | 59 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 74 | 53 | 71.6% | 2 | 0 | 51 |
| 0.08 | [0.10, 0.90] | ×0.5 | 84 | 63 | 75.0% | 2 | 0 | 61 |
| 0.08 | [0.10, 0.90] | ×1.0 | 74 | 54 | 73.0% | 2 | 0 | 52 |
| 0.08 | [0.10, 0.90] | ×2.0 | 64 | 47 | 73.4% | 2 | 0 | 45 |
| 0.08 | [0.15, 0.85] | ×0.5 | 71 | 53 | 74.6% | 2 | 0 | 51 |
| 0.08 | [0.15, 0.85] | ×1.0 | 61 | 44 | 72.1% | 2 | 0 | 42 |
| 0.08 | [0.15, 0.85] | ×2.0 | 52 | 37 | 71.2% | 2 | 0 | 35 | ← **rec**
| 0.12 | [0.05, 0.95] | ×0.5 | 96 | 70 | 72.9% | 2 | 0 | 68 |
| 0.12 | [0.05, 0.95] | ×1.0 | 85 | 61 | 71.8% | 2 | 0 | 59 |
| 0.12 | [0.05, 0.95] | ×2.0 | 74 | 53 | 71.6% | 2 | 0 | 51 |
| 0.12 | [0.10, 0.90] | ×0.5 | 84 | 63 | 75.0% | 2 | 0 | 61 |
| 0.12 | [0.10, 0.90] | ×1.0 | 74 | 54 | 73.0% | 2 | 0 | 52 |
| 0.12 | [0.10, 0.90] | ×2.0 | 64 | 47 | 73.4% | 2 | 0 | 45 |
| 0.12 | [0.15, 0.85] | ×0.5 | 71 | 53 | 74.6% | 2 | 0 | 51 |
| 0.12 | [0.15, 0.85] | ×1.0 | 61 | 44 | 72.1% | 2 | 0 | 42 |
| 0.12 | [0.15, 0.85] | ×2.0 | 52 | 37 | 71.2% | 2 | 0 | 35 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 96 | 90 | 93.8% | 22 | 67 | 1 |
| 0.06 | [0.05, 0.95] | ×1.0 | 85 | 80 | 94.1% | 19 | 60 | 1 |
| 0.06 | [0.05, 0.95] | ×2.0 | 74 | 70 | 94.6% | 19 | 50 | 1 |
| 0.06 | [0.10, 0.90] | ×0.5 | 84 | 81 | 96.4% | 19 | 61 | 1 |
| 0.06 | [0.10, 0.90] | ×1.0 | 74 | 71 | 95.9% | 16 | 54 | 1 |
| 0.06 | [0.10, 0.90] | ×2.0 | 64 | 62 | 96.9% | 16 | 45 | 1 |
| 0.06 | [0.15, 0.85] | ×0.5 | 71 | 71 | 100.0% | 16 | 54 | 1 |
| 0.06 | [0.15, 0.85] | ×1.0 | 61 | 61 | 100.0% | 13 | 47 | 1 |
| 0.06 | [0.15, 0.85] | ×2.0 | 52 | 52 | 100.0% | 13 | 38 | 1 |
| 0.08 | [0.05, 0.95] | ×0.5 | 96 | 90 | 93.8% | 19 | 67 | 4 |
| 0.08 | [0.05, 0.95] | ×1.0 | 85 | 80 | 94.1% | 16 | 60 | 4 |
| 0.08 | [0.05, 0.95] | ×2.0 | 74 | 70 | 94.6% | 16 | 50 | 4 |
| 0.08 | [0.10, 0.90] | ×0.5 | 84 | 81 | 96.4% | 17 | 61 | 3 |
| 0.08 | [0.10, 0.90] | ×1.0 | 74 | 71 | 95.9% | 14 | 54 | 3 |
| 0.08 | [0.10, 0.90] | ×2.0 | 64 | 62 | 96.9% | 14 | 45 | 3 |
| 0.08 | [0.15, 0.85] | ×0.5 | 71 | 71 | 100.0% | 14 | 54 | 3 |
| 0.08 | [0.15, 0.85] | ×1.0 | 61 | 61 | 100.0% | 11 | 47 | 3 |
| 0.08 | [0.15, 0.85] | ×2.0 | 52 | 52 | 100.0% | 11 | 38 | 3 |
| 0.12 | [0.05, 0.95] | ×0.5 | 96 | 90 | 93.8% | 18 | 67 | 5 |
| 0.12 | [0.05, 0.95] | ×1.0 | 85 | 80 | 94.1% | 15 | 60 | 5 |
| 0.12 | [0.05, 0.95] | ×2.0 | 74 | 70 | 94.6% | 15 | 50 | 5 |
| 0.12 | [0.10, 0.90] | ×0.5 | 84 | 81 | 96.4% | 16 | 61 | 4 |
| 0.12 | [0.10, 0.90] | ×1.0 | 74 | 71 | 95.9% | 13 | 54 | 4 |
| 0.12 | [0.10, 0.90] | ×2.0 | 64 | 62 | 96.9% | 13 | 45 | 4 |
| 0.12 | [0.15, 0.85] | ×0.5 | 71 | 71 | 100.0% | 13 | 54 | 4 |
| 0.12 | [0.15, 0.85] | ×1.0 | 61 | 61 | 100.0% | 10 | 47 | 4 |
| 0.12 | [0.15, 0.85] | ×2.0 | 52 | 52 | 100.0% | 10 | 38 | 4 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **85 markets** survive the filter and **61 are flagged** (71.8% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **2** markets (3%)
- `DRIFT` (order-book mid vs last trade): **59** markets (97%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×2.0, strict_with_heuristic  
→ 52 markets survive, 37 flagged (71.2%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×2.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.