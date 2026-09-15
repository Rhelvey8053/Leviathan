# ForecastBench External Calibration Pilot

Run: 2026-09-15T14:48:21.406342+00:00
ForecastBench resolution set: 2026-08-30
Source data: forecastingresearch/forecastbench-datasets (public, no auth)

**Mode: search (matches production) -- look-ahead contamination caveat**. These questions have already resolved and the scorer runs with WebSearch enabled, matching production -- a good Brier score here is not proof of blind forecasting skill, it may just mean the model found the real outcome. See this module's own docstring for the full caveat. Isolated from the live `signals` table -- never pooled with production win/loss grading.

n scored (YES/NO): 12  |  n PASS/unscored: 4
Mean scorer Brier (YES/NO calls only): 0.0084
Mean market-price Brier (same YES/NO population): 0.1347

All-estimate comparison (n=16, includes PASS calls -- our_estimate is reported even when the model declines to act on it):
  Mean our_estimate Brier: 0.0079
  Mean market-price Brier: 0.1028
Token usage: {'input_tokens': 16, 'output_tokens': 14873, 'cache_creation_input_tokens': 42234, 'cache_read_input_tokens': 393925, 'cost_usd': 0.842063}

## Per-market detail

| ticker | direction | confidence | our_estimate | market_price | ground_truth | brier |
|---|---|---|---|---|---|---|
| KXAAAGASM-26AUG31-4.10 | NO | MED | 0.300 | 0.600 | 0 | 0.0900 |
| KXROLEINPRODUCTIONLI-27JUL31-ARI | YES | MED | 0.910 | 0.790 | 1 | 0.0081 |
| KXBRENTMON-26AUG3117-T84.99 | YES | HIGH | 0.980 | 0.780 | 1 | 0.0004 |
| KXSP500ADDQ-26SEP30-BE | YES | HIGH | 0.980 | 0.570 | 1 | 0.0004 |
| KXU3-26AUG-T4.0 | YES | HIGH | 0.980 | 0.860 | 1 | 0.0004 |
| KXEMMY-VS26SEP14-TIE | NO | HIGH | 0.020 | 0.290 | 0 | 0.0004 |
| KXNCAAFGAME-26SEP12OSUTEX-OSU | NO | HIGH | 0.020 | 0.480 | 0 | 0.0004 |
| KXNFLGAME-26SEP13DALNYG-DAL | NO | HIGH | 0.020 | 0.580 | 0 | 0.0004 |
| KXBOXING-26SEP12GARCIABENN-GARCIA | YES | HIGH | 0.990 | 0.690 | 1 | 0.0001 |
| KXNCAAFGAME-26SEP06LOUMISS-MISS | YES | HIGH | 0.990 | 0.730 | 1 | 0.0001 |
| KXATP-26USO-SIN | NO | HIGH | 0.010 | 0.350 | 0 | 0.0001 |
| KXLEAGUESCUP-26-RSL | NO | HIGH | 0.010 | 0.130 | 0 | 0.0001 |

## PASS / unscored (excluded from Brier, kept for inspection)

| ticker | direction | confidence | our_estimate | market_price | ground_truth | reasoning |
|---|---|---|---|---|---|---|
| KXCBDECISIONNZ-26SEP01-H25 | PASS | MED | 0.990 | 0.940 | 1 | RBNZ unanimously hiked the OCR by 25bps to 2.75% on September 2, 2026, confirming the YES outcome, but the market price already reflects this closely  |
| KXCPICOREYOY-26AUG-T2.1 | PASS | MED | 0.990 | 0.930 | 1 | August 2026 core CPI came in at 2.4% YoY, above the 2.1% threshold, confirming YES, but the market price is already close to resolved value so the edg |
| KXINDYCARSERIES-NTTICS26-KKIR | PASS | MED | 0.010 | 0.030 | 0 | Alex Palou won the 2026 NTT IndyCar Series championship with Kyle Kirkwood finishing runner-up; market price already correctly reflects this near-zero |
| KXSPACEXCOUNT-26AUG-14 | PASS | LOW | 0.160 | 0.140 | 0 | Cumulative Falcon 9 launch counts (93 by Aug 11, ~100 by Aug 25-31) imply roughly 12-14 launches occurred specifically in August 2026, consistent with |
