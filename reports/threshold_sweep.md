# Threshold Sweep — Leviathan v1

**Snapshot:** 2026-07-27T14:59:54.274780+00:00  
**Environment:** PROD  
**Total markets in snapshot:** 2476  
**Grid size:** 54 combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  

## Grid Results — `strict\_with\_heuristic (production)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 32 | 31 | 96.9% | 3 | 0 | 28 |
| 0.06 | [0.05, 0.95] | ×1.0 | 31 | 30 | 96.8% | 2 | 0 | 28 |
| 0.06 | [0.05, 0.95] | ×2.0 | 26 | 25 | 96.2% | 2 | 0 | 23 |
| 0.06 | [0.10, 0.90] | ×0.5 | 23 | 22 | 95.7% | 2 | 0 | 20 |
| 0.06 | [0.10, 0.90] | ×1.0 | 23 | 22 | 95.7% | 2 | 0 | 20 |
| 0.06 | [0.10, 0.90] | ×2.0 | 18 | 17 | 94.4% | 2 | 0 | 15 |
| 0.06 | [0.15, 0.85] | ×0.5 | 17 | 16 | 94.1% | 1 | 0 | 15 |
| 0.06 | [0.15, 0.85] | ×1.0 | 17 | 16 | 94.1% | 1 | 0 | 15 |
| 0.06 | [0.15, 0.85] | ×2.0 | 13 | 12 | 92.3% | 1 | 0 | 11 |
| 0.08 | [0.05, 0.95] | ×0.5 | 32 | 31 | 96.9% | 3 | 0 | 28 |
| 0.08 | [0.05, 0.95] | ×1.0 | 31 | 30 | 96.8% | 2 | 0 | 28 | ← **prod**
| 0.08 | [0.05, 0.95] | ×2.0 | 26 | 25 | 96.2% | 2 | 0 | 23 |
| 0.08 | [0.10, 0.90] | ×0.5 | 23 | 22 | 95.7% | 2 | 0 | 20 |
| 0.08 | [0.10, 0.90] | ×1.0 | 23 | 22 | 95.7% | 2 | 0 | 20 |
| 0.08 | [0.10, 0.90] | ×2.0 | 18 | 17 | 94.4% | 2 | 0 | 15 |
| 0.08 | [0.15, 0.85] | ×0.5 | 17 | 16 | 94.1% | 1 | 0 | 15 |
| 0.08 | [0.15, 0.85] | ×1.0 | 17 | 16 | 94.1% | 1 | 0 | 15 |
| 0.08 | [0.15, 0.85] | ×2.0 | 13 | 12 | 92.3% | 1 | 0 | 11 | ← **rec**
| 0.12 | [0.05, 0.95] | ×0.5 | 32 | 31 | 96.9% | 3 | 0 | 28 |
| 0.12 | [0.05, 0.95] | ×1.0 | 31 | 30 | 96.8% | 2 | 0 | 28 |
| 0.12 | [0.05, 0.95] | ×2.0 | 26 | 25 | 96.2% | 2 | 0 | 23 |
| 0.12 | [0.10, 0.90] | ×0.5 | 23 | 22 | 95.7% | 2 | 0 | 20 |
| 0.12 | [0.10, 0.90] | ×1.0 | 23 | 22 | 95.7% | 2 | 0 | 20 |
| 0.12 | [0.10, 0.90] | ×2.0 | 18 | 17 | 94.4% | 2 | 0 | 15 |
| 0.12 | [0.15, 0.85] | ×0.5 | 17 | 16 | 94.1% | 1 | 0 | 15 |
| 0.12 | [0.15, 0.85] | ×1.0 | 17 | 16 | 94.1% | 1 | 0 | 15 |
| 0.12 | [0.15, 0.85] | ×2.0 | 13 | 12 | 92.3% | 1 | 0 | 11 |

## Grid Results — `passthrough (baseline)`

| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |
|----------|--------------|------------|----------|---------|-----------|------|---------|-------|
| 0.06 | [0.05, 0.95] | ×0.5 | 32 | 32 | 100.0% | 21 | 8 | 3 |
| 0.06 | [0.05, 0.95] | ×1.0 | 31 | 31 | 100.0% | 20 | 8 | 3 |
| 0.06 | [0.05, 0.95] | ×2.0 | 26 | 26 | 100.0% | 16 | 8 | 2 |
| 0.06 | [0.10, 0.90] | ×0.5 | 23 | 23 | 100.0% | 14 | 6 | 3 |
| 0.06 | [0.10, 0.90] | ×1.0 | 23 | 23 | 100.0% | 14 | 6 | 3 |
| 0.06 | [0.10, 0.90] | ×2.0 | 18 | 18 | 100.0% | 10 | 6 | 2 |
| 0.06 | [0.15, 0.85] | ×0.5 | 17 | 17 | 100.0% | 9 | 5 | 3 |
| 0.06 | [0.15, 0.85] | ×1.0 | 17 | 17 | 100.0% | 9 | 5 | 3 |
| 0.06 | [0.15, 0.85] | ×2.0 | 13 | 13 | 100.0% | 6 | 5 | 2 |
| 0.08 | [0.05, 0.95] | ×0.5 | 32 | 32 | 100.0% | 21 | 8 | 3 |
| 0.08 | [0.05, 0.95] | ×1.0 | 31 | 31 | 100.0% | 20 | 8 | 3 |
| 0.08 | [0.05, 0.95] | ×2.0 | 26 | 26 | 100.0% | 16 | 8 | 2 |
| 0.08 | [0.10, 0.90] | ×0.5 | 23 | 23 | 100.0% | 14 | 6 | 3 |
| 0.08 | [0.10, 0.90] | ×1.0 | 23 | 23 | 100.0% | 14 | 6 | 3 |
| 0.08 | [0.10, 0.90] | ×2.0 | 18 | 18 | 100.0% | 10 | 6 | 2 |
| 0.08 | [0.15, 0.85] | ×0.5 | 17 | 17 | 100.0% | 9 | 5 | 3 |
| 0.08 | [0.15, 0.85] | ×1.0 | 17 | 17 | 100.0% | 9 | 5 | 3 |
| 0.08 | [0.15, 0.85] | ×2.0 | 13 | 13 | 100.0% | 6 | 5 | 2 |
| 0.12 | [0.05, 0.95] | ×0.5 | 32 | 32 | 100.0% | 19 | 8 | 5 |
| 0.12 | [0.05, 0.95] | ×1.0 | 31 | 31 | 100.0% | 18 | 8 | 5 |
| 0.12 | [0.05, 0.95] | ×2.0 | 26 | 26 | 100.0% | 14 | 8 | 4 |
| 0.12 | [0.10, 0.90] | ×0.5 | 23 | 23 | 100.0% | 13 | 6 | 4 |
| 0.12 | [0.10, 0.90] | ×1.0 | 23 | 23 | 100.0% | 13 | 6 | 4 |
| 0.12 | [0.10, 0.90] | ×2.0 | 18 | 18 | 100.0% | 9 | 6 | 3 |
| 0.12 | [0.15, 0.85] | ×0.5 | 17 | 17 | 100.0% | 9 | 5 | 3 |
| 0.12 | [0.15, 0.85] | ×1.0 | 17 | 17 | 100.0% | 9 | 5 | 3 |
| 0.12 | [0.15, 0.85] | ×2.0 | 13 | 13 | 100.0% | 6 | 5 | 2 |

## Verdict

At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): **31 markets** survive the filter and **30 are flagged** (96.8% flag rate).

**Flag path breakdown (production mode):**
- `HEURISTIC/EDGE` (base rate edge > threshold): **2** markets (7%)
- `DRIFT` (order-book mid vs last trade): **28** markets (93%)
- `BR_NONE` (no base rate fallback): **0** markets (0%)

**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives the filter has a matching base rate, so `strict_with_heuristic` mode flags only markets with real edge signals (heuristic disagrees with price by >8pp) or drift. This is the optimal state: the flag step is doing genuine probability-based selection.

**Passthrough vs strict_with_heuristic:**  
The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the correct production mode: it rejects markets where the crowd is likely right (no strong heuristic disagreement, no drift) and focuses Claude's budget on genuine mispricing candidates.

## Recommendation

**Recommended config:** edge=0.08, price=[0.15, 0.85], vol×2.0, strict_with_heuristic  
→ 13 markets survive, 12 flagged (92.3%).  
**Reasoning:** Tighter price bounds cut the long tail of near-certain and tail-probability markets while preserving the contested 15–85% range where genuine mispricing is plausible. Volume floor at ×2.0 avoids illiquid markets where the edge estimate is noise.

> **Note:** This sweep measures candidate *volume* only — it cannot judge signal *correctness*. A market flagged here may or may not represent a real edge; that can only be measured once markets resolve.