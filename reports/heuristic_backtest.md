# Heuristic Backtest — Leviathan v1

**Generated:** 2026-08-02T00:34:38.527412+00:00  
**Source:** settled_markets (real Kalshi resolutions, no LLM cost)  
**Total settled markets (binary result):** 12600  
**Heuristic coverage:** 2154 / 12600 (17.1%)  

## Overall calibration

- Naive baseline (always predict the population YES-rate, 23.3%): Brier = 0.1786
- Heuristic table (only where a pattern matched): Brier = 0.0837
- Delta: -0.0949 -> heuristics beat the naive baseline
- Directional accuracy (excludes exact-0.5 coin-flip predictions, n=2096): 89.0%

## By heuristic label

| Label | n | Avg predicted | Actual YES rate | Calibration gap | Brier |
|---|---|---|---|---|---|
| competition win | 734 | 8.0% | 8.2% | -0.002 | 0.0751 |
| competition/award ranking | 637 | 2.0% | 1.7% | +0.003 | 0.0170 |
| entertainment award | 236 | 20.0% | 10.6% | +0.094 | 0.1036 |
| sports award | 107 | 20.0% | 3.7% | +0.163 | 0.0624 |
| political coup | 89 | 10.0% | 3.4% | +0.066 | 0.0370 |
| down-ballot election | 43 | 52.0% | 23.3% | +0.287 | 0.2611 |
| AI model release | 34 | 25.0% | 11.8% | +0.132 | 0.1213 |
| legislative passage | 33 | 35.0% | 42.4% | -0.074 | 0.2498 |
| supreme court ruling | 24 | 50.0% | 37.5% | +0.125 | 0.2500 |
| first named storm | 24 | 4.0% | 4.2% | -0.002 | 0.0399 |
| SpaceX launch | 15 | 40.0% | 40.0% | +0.000 | 0.2400 |
| executive order | 14 | 45.0% | 21.4% | +0.236 | 0.2239 |
| IPO announcement | 13 | 25.0% | 0.0% | +0.250 | 0.0625 |
| FDA advisory committee | 13 | 50.0% | 46.2% | +0.038 | 0.2500 |
| sports debut | 12 | 35.0% | 58.3% | -0.233 | 0.2975 |
| sports transaction | 12 | 30.0% | 100.0% | -0.700 | 0.4900 |
| sports qualification | 11 | 35.0% | 54.5% | -0.195 | 0.2861 |
| merger or acquisition | 10 | 35.0% | 50.0% | -0.150 | 0.2725 |
| employment data | 9 | 50.0% | 66.7% | -0.167 | 0.2500 |
| media/entertainment release | 6 | 25.0% | 50.0% | -0.250 | 0.3125 |
| presidential clemency | 6 | 35.0% | 16.7% | +0.183 | 0.1725 |
| crypto price level | 6 | 50.0% | 50.0% | +0.000 | 0.2500 |
| NASA mission | 6 | 30.0% | 0.0% | +0.300 | 0.0900 |
| CPI/inflation data | 5 | 50.0% | 100.0% | -0.500 | 0.2500 |
| company valuation | 5 | 35.0% | 100.0% | -0.650 | 0.4225 |
| labor strike | 5 | 30.0% | 0.0% | +0.300 | 0.0900 |
| hurricane category ladder | 5 | 5.0% | 0.0% | +0.050 | 0.0025 |
| senate confirmation | 4 | 55.0% | 25.0% | +0.300 | 0.2775 |
| candidacy announcement | 4 | 35.0% | 100.0% | -0.650 | 0.4225 |
| athlete retirement | 3 | 30.0% | 100.0% | -0.700 | 0.4900 |
| social media post | 3 | 75.0% | 66.7% | +0.083 | 0.2292 |
| budget/spending legislation | 3 | 40.0% | 100.0% | -0.600 | 0.3600 |
| cabinet departure | 2 | 65.0% | 0.0% | +0.650 | 0.4225 |
| exchange rate | 2 | 40.0% | 100.0% | -0.600 | 0.3600 |
| nuclear deal | 2 | 20.0% | 0.0% | +0.200 | 0.0400 |
| criminal conviction | 2 | 40.0% | 100.0% | -0.600 | 0.3600 |
| trade tariffs | 2 | 40.0% | 100.0% | -0.600 | 0.3600 |
| impeachment | 1 | 15.0% | 0.0% | +0.150 | 0.0225 |
| GDP data | 1 | 50.0% | 100.0% | -0.500 | 0.2500 |
| autonomous vehicle deployment | 1 | 25.0% | 100.0% | -0.750 | 0.5625 |
| resignation | 1 | 20.0% | 0.0% | +0.200 | 0.0400 |
| athletic record | 1 | 30.0% | 100.0% | -0.700 | 0.4900 |
| lawsuit settlement | 1 | 40.0% | 0.0% | +0.400 | 0.1600 |
| tax legislation | 1 | 35.0% | 0.0% | +0.350 | 0.1225 |
| IPO timing | 1 | 30.0% | 100.0% | -0.700 | 0.4900 |
| tech product announcement | 1 | 55.0% | 0.0% | +0.550 | 0.3025 |
| FDA approval | 1 | 40.0% | 0.0% | +0.400 | 0.1600 |
| national emergency | 1 | 25.0% | 0.0% | +0.250 | 0.0625 |
| ceasefire or peace deal | 1 | 25.0% | 0.0% | +0.250 | 0.0625 |
| candidate withdrawal | 1 | 30.0% | 100.0% | -0.700 | 0.4900 |

Calibration gap = avg predicted YES probability minus actual YES rate. Positive = heuristic is overconfident on YES; negative = underconfident on YES (overconfident on NO). Labels with few resolved rows (n<10) are noisy -- read the gap as a lead, not a verdict, until more settled markets accumulate for that label.
