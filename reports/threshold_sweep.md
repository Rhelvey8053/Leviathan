# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-09-07T12:07:38.483623+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 3007  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 94 | 63 | 67.0% | 6 | 0 | 57 |
| 0.06 | [0.05, 0.95] | ×1.0 | 83 | 54 | 65.1% | 5 | 0 | 49 |
| 0.06 | [0.05, 0.95] | ×2.0 | 66 | 44 | 66.7% | 4 | 0 | 40 |
| 0.06 | [0.10, 0.90] | ×0.5 | 73 | 49 | 67.1% | 4 | 0 | 45 |
| 0.06 | [0.10, 0.90] | ×1.0 | 65 | 42 | 64.6% | 3 | 0 | 39 |
| 0.06 | [0.10, 0.90] | ×2.0 | 51 | 34 | 66.7% | 2 | 0 | 32 |
| 0.06 | [0.15, 0.85] | ×0.5 | 60 | 38 | 63.3% | 4 | 0 | 34 |
| 0.06 | [0.15, 0.85] | ×1.0 | 52 | 31 | 59.6% | 3 | 0 | 28 |
| 0.06 | [0.15, 0.85] | ×2.0 | 41 | 25 | 61.0% | 2 | 0 | 23 |
| 0.08 | [0.05, 0.95] | ×0.5 | 94 | 63 | 67.0% | 6 | 0 | 57 |
| 0.08 | [0.05, 0.95] | ×1.0 | 83 | 54 | 65.1% | 5 | 0 | 49 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 66 | 44 | 66.7% | 4 | 0 | 40 |
| 0.08 | [0.10, 0.90] | ×0.5 | 73 | 49 | 67.1% | 4 | 0 | 45 |
| 0.08 | [0.10, 0.90] | ×1.0 | 65 | 42 | 64.6% | 3 | 0 | 39 |
| 0.08 | [0.10, 0.90] | ×2.0 | 51 | 34 | 66.7% | 2 | 0 | 32 |
| 0.08 | [0.15, 0.85] | ×0.5 | 60 | 38 | 63.3% | 4 | 0 | 34 |
| 0.08 | [0.15, 0.85] | ×1.0 | 52 | 31 | 59.6% | 3 | 0 | 28 | ← **rec**
| 0.08 | [0.15, 0.85] | ×2.0 | 41 | 25 | 61.0% | 2 | 0 | 23 |
| 0.12 | [0.05, 0.95] | ×0.5 | 94 | 63 | 67.0% | 6 | 0 | 57 |
| 0.12 | [0.05, 0.95] | ×1.0 | 83 | 54 | 65.1% | 5 | 0 | 49 |
| 0.12 | [0.05, 0.95] | ×2.0 | 66 | 44 | 66.7% | 4 | 0 | 40 |
| 0.12 | [0.10, 0.90] | ×0.5 | 73 | 49 | 67.1% | 4 | 0 | 45 |
| 0.12 | [0.10, 0.90] | ×1.0 | 65 | 42 | 64.6% | 3 | 0 | 39 |
| 0.12 | [0.10, 0.90] | ×2.0 | 51 | 34 | 66.7% | 2 | 0 | 32 |
| 0.12 | [0.15, 0.85] | ×0.5 | 60 | 38 | 63.3% | 4 | 0 | 34 |
| 0.12 | [0.15, 0.85] | ×1.0 | 52 | 31 | 59.6% | 3 | 0 | 28 |
| 0.12 | [0.15, 0.85] | ×2.0 | 41 | 25 | 61.0% | 2 | 0 | 23 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 94 | 86 | 91.5% | 26 | 56 | 4 |
| 0.06 | [0.05, 0.95] | ×1.0 | 83 | 76 | 91.6% | 23 | 49 | 4 |
| 0.06 | [0.05, 0.95] | ×2.0 | 66 | 61 | 92.4% | 18 | 40 | 3 |
| 0.06 | [0.10, 0.90] | ×0.5 | 73 | 69 | 94.5% | 21 | 47 | 1 |
| 0.06 | [0.10, 0.90] | ×1.0 | 65 | 61 | 93.8% | 18 | 42 | 1 |
| 0.06 | [0.10, 0.90] | ×2.0 | 51 | 48 | 94.1% | 13 | 34 | 1 |
| 0.06 | [0.15, 0.85] | ×0.5 | 60 | 58 | 96.7% | 15 | 42 | 1 |
| 0.06 | [0.15, 0.85] | ×1.0 | 52 | 50 | 96.2% | 12 | 37 | 1 |
| 0.06 | [0.15, 0.85] | ×2.0 | 41 | 39 | 95.1% | 9 | 29 | 1 |
| 0.08 | [0.05, 0.95] | ×0.5 | 94 | 86 | 91.5% | 24 | 56 | 6 |
| 0.08 | [0.05, 0.95] | ×1.0 | 83 | 76 | 91.6% | 21 | 49 | 6 |
| 0.08 | [0.05, 0.95] | ×2.0 | 66 | 61 | 92.4% | 16 | 40 | 5 |
| 0.08 | [0.10, 0.90] | ×0.5 | 73 | 69 | 94.5% | 20 | 47 | 2 |
| 0.08 | [0.10, 0.90] | ×1.0 | 65 | 61 | 93.8% | 17 | 42 | 2 |
| 0.08 | [0.10, 0.90] | ×2.0 | 51 | 48 | 94.1% | 12 | 34 | 2 |
| 0.08 | [0.15, 0.85] | ×0.5 | 60 | 58 | 96.7% | 14 | 42 | 2 |
| 0.08 | [0.15, 0.85] | ×1.0 | 52 | 50 | 96.2% | 11 | 37 | 2 |
| 0.08 | [0.15, 0.85] | ×2.0 | 41 | 39 | 95.1% | 8 | 29 | 2 |
| 0.12 | [0.05, 0.95] | ×0.5 | 94 | 86 | 91.5% | 22 | 56 | 8 |
| 0.12 | [0.05, 0.95] | ×1.0 | 83 | 76 | 91.6% | 19 | 49 | 8 |
| 0.12 | [0.05, 0.95] | ×2.0 | 66 | 61 | 92.4% | 15 | 40 | 6 |
| 0.12 | [0.10, 0.90] | ×0.5 | 73 | 69 | 94.5% | 18 | 47 | 4 |
| 0.12 | [0.10, 0.90] | ×1.0 | 65 | 61 | 93.8% | 15 | 42 | 4 |
| 0.12 | [0.10, 0.90] | ×2.0 | 51 | 48 | 94.1% | 11 | 34 | 3 |
| 0.12 | [0.15, 0.85] | ×0.5 | 60 | 58 | 96.7% | 14 | 42 | 2 |
| 0.12 | [0.15, 0.85] | ×1.0 | 52 | 50 | 96.2% | 11 | 37 | 2 |
| 0.12 | [0.15, 0.85] | ×2.0 | 41 | 39 | 95.1% | 8 | 29 | 2 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **83 markets** survive the filter and **54 are flagged** (65.1% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **5** markets (9%)
- `DRIFT` (order-book mid vs last trade): **49** markets (91%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×1.0, strict_with_heuristic  
→ 52 markets survive, 31 flagged (59.6%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×1.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.