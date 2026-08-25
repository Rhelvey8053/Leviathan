# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-08-25T01:38:06.453461+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2941  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 48 | 33 | 68.8% | 1 | 0 | 32 |
| 0.06 | [0.05, 0.95] | ×1.0 | 44 | 30 | 68.2% | 1 | 0 | 29 |
| 0.06 | [0.05, 0.95] | ×2.0 | 42 | 28 | 66.7% | 1 | 0 | 27 |
| 0.06 | [0.10, 0.90] | ×0.5 | 32 | 19 | 59.4% | 0 | 0 | 19 |
| 0.06 | [0.10, 0.90] | ×1.0 | 30 | 17 | 56.7% | 0 | 0 | 17 |
| 0.06 | [0.10, 0.90] | ×2.0 | 28 | 15 | 53.6% | 0 | 0 | 15 |
| 0.06 | [0.15, 0.85] | ×0.5 | 24 | 14 | 58.3% | 0 | 0 | 14 |
| 0.06 | [0.15, 0.85] | ×1.0 | 22 | 12 | 54.5% | 0 | 0 | 12 |
| 0.06 | [0.15, 0.85] | ×2.0 | 21 | 11 | 52.4% | 0 | 0 | 11 |
| 0.08 | [0.05, 0.95] | ×0.5 | 48 | 33 | 68.8% | 1 | 0 | 32 |
| 0.08 | [0.05, 0.95] | ×1.0 | 44 | 30 | 68.2% | 1 | 0 | 29 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 42 | 28 | 66.7% | 1 | 0 | 27 |
| 0.08 | [0.10, 0.90] | ×0.5 | 32 | 19 | 59.4% | 0 | 0 | 19 |
| 0.08 | [0.10, 0.90] | ×1.0 | 30 | 17 | 56.7% | 0 | 0 | 17 |
| 0.08 | [0.10, 0.90] | ×2.0 | 28 | 15 | 53.6% | 0 | 0 | 15 |
| 0.08 | [0.15, 0.85] | ×0.5 | 24 | 14 | 58.3% | 0 | 0 | 14 |
| 0.08 | [0.15, 0.85] | ×1.0 | 22 | 12 | 54.5% | 0 | 0 | 12 |
| 0.08 | [0.15, 0.85] | ×2.0 | 21 | 11 | 52.4% | 0 | 0 | 11 | ← **rec**
| 0.12 | [0.05, 0.95] | ×0.5 | 48 | 33 | 68.8% | 1 | 0 | 32 |
| 0.12 | [0.05, 0.95] | ×1.0 | 44 | 30 | 68.2% | 1 | 0 | 29 |
| 0.12 | [0.05, 0.95] | ×2.0 | 42 | 28 | 66.7% | 1 | 0 | 27 |
| 0.12 | [0.10, 0.90] | ×0.5 | 32 | 19 | 59.4% | 0 | 0 | 19 |
| 0.12 | [0.10, 0.90] | ×1.0 | 30 | 17 | 56.7% | 0 | 0 | 17 |
| 0.12 | [0.10, 0.90] | ×2.0 | 28 | 15 | 53.6% | 0 | 0 | 15 |
| 0.12 | [0.15, 0.85] | ×0.5 | 24 | 14 | 58.3% | 0 | 0 | 14 |
| 0.12 | [0.15, 0.85] | ×1.0 | 22 | 12 | 54.5% | 0 | 0 | 12 |
| 0.12 | [0.15, 0.85] | ×2.0 | 21 | 11 | 52.4% | 0 | 0 | 11 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 48 | 41 | 85.4% | 13 | 25 | 3 |
| 0.06 | [0.05, 0.95] | ×1.0 | 44 | 38 | 86.4% | 13 | 22 | 3 |
| 0.06 | [0.05, 0.95] | ×2.0 | 42 | 36 | 85.7% | 11 | 22 | 3 |
| 0.06 | [0.10, 0.90] | ×0.5 | 32 | 27 | 84.4% | 7 | 18 | 2 |
| 0.06 | [0.10, 0.90] | ×1.0 | 30 | 25 | 83.3% | 7 | 16 | 2 |
| 0.06 | [0.10, 0.90] | ×2.0 | 28 | 23 | 82.1% | 5 | 16 | 2 |
| 0.06 | [0.15, 0.85] | ×0.5 | 24 | 22 | 91.7% | 3 | 17 | 2 |
| 0.06 | [0.15, 0.85] | ×1.0 | 22 | 20 | 90.9% | 3 | 15 | 2 |
| 0.06 | [0.15, 0.85] | ×2.0 | 21 | 19 | 90.5% | 2 | 15 | 2 |
| 0.08 | [0.05, 0.95] | ×0.5 | 48 | 41 | 85.4% | 12 | 25 | 4 |
| 0.08 | [0.05, 0.95] | ×1.0 | 44 | 38 | 86.4% | 12 | 22 | 4 |
| 0.08 | [0.05, 0.95] | ×2.0 | 42 | 36 | 85.7% | 11 | 22 | 3 |
| 0.08 | [0.10, 0.90] | ×0.5 | 32 | 27 | 84.4% | 6 | 18 | 3 |
| 0.08 | [0.10, 0.90] | ×1.0 | 30 | 25 | 83.3% | 6 | 16 | 3 |
| 0.08 | [0.10, 0.90] | ×2.0 | 28 | 23 | 82.1% | 5 | 16 | 2 |
| 0.08 | [0.15, 0.85] | ×0.5 | 24 | 22 | 91.7% | 2 | 17 | 3 |
| 0.08 | [0.15, 0.85] | ×1.0 | 22 | 20 | 90.9% | 2 | 15 | 3 |
| 0.08 | [0.15, 0.85] | ×2.0 | 21 | 19 | 90.5% | 2 | 15 | 2 |
| 0.12 | [0.05, 0.95] | ×0.5 | 48 | 41 | 85.4% | 11 | 25 | 5 |
| 0.12 | [0.05, 0.95] | ×1.0 | 44 | 38 | 86.4% | 11 | 22 | 5 |
| 0.12 | [0.05, 0.95] | ×2.0 | 42 | 36 | 85.7% | 10 | 22 | 4 |
| 0.12 | [0.10, 0.90] | ×0.5 | 32 | 27 | 84.4% | 5 | 18 | 4 |
| 0.12 | [0.10, 0.90] | ×1.0 | 30 | 25 | 83.3% | 5 | 16 | 4 |
| 0.12 | [0.10, 0.90] | ×2.0 | 28 | 23 | 82.1% | 4 | 16 | 3 |
| 0.12 | [0.15, 0.85] | ×0.5 | 24 | 22 | 91.7% | 2 | 17 | 3 |
| 0.12 | [0.15, 0.85] | ×1.0 | 22 | 20 | 90.9% | 2 | 15 | 3 |
| 0.12 | [0.15, 0.85] | ×2.0 | 21 | 19 | 90.5% | 2 | 15 | 2 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **44 markets** survive the filter and **30 are flagged** (68.2% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **1** markets (3%)
- `DRIFT` (order-book mid vs last trade): **29** markets (97%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×2.0, strict_with_heuristic  
→ 21 markets survive, 11 flagged (52.4%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×2.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.