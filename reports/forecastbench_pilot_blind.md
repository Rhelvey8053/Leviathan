# ForecastBench External Calibration Pilot

Run: 2026-09-15T14:45:08.644040+00:00
ForecastBench resolution set: 2026-08-30
Source data: forecastingresearch/forecastbench-datasets (public, no auth)

**Mode: blind (WebSearch disabled)**. These questions have already resolved, but this run omits --allowedTools WebSearch so the model cannot look up the real outcome -- it answers from parametric knowledge alone. Not a fully clean read (a post-cutoff event can still correlate with pre-cutoff context the model has), but removes the dominant contamination source the search-enabled run (mode=search) demonstrated. See this module's docstring for the full caveat. Isolated from the live `signals` table -- never pooled with production win/loss grading.

n scored (YES/NO): 0  |  n PASS/unscored: 16
Mean scorer Brier (YES/NO calls only): n/a
Mean market-price Brier (same YES/NO population): n/a

All-estimate comparison (n=16, includes PASS calls -- our_estimate is reported even when the model declines to act on it):
  Mean our_estimate Brier: 0.1038
  Mean market-price Brier: 0.1028
Token usage: {'input_tokens': 26, 'output_tokens': 14611, 'cache_creation_input_tokens': 18184, 'cache_read_input_tokens': 652845, 'cost_usd': 0.35397599999999996}

## Per-market detail

| ticker | direction | confidence | our_estimate | market_price | ground_truth | brier |
|---|---|---|---|---|---|---|

## PASS / unscored (excluded from Brier, kept for inspection)

| ticker | direction | confidence | our_estimate | market_price | ground_truth | reasoning |
|---|---|---|---|---|---|---|
| KXAAAGASM-26AUG31-4.10 | PASS | LOW | 0.600 | 0.600 | 0 | Web search tools were unavailable this session (permission errors on all search/fetch attempts), so no incremental evidence beyond the market price co |
| KXATP-26USO-SIN | PASS | LOW | 0.350 | 0.350 | 0 | Unable to verify current US Open bracket/results due to web tool access failure. 35% is a plausible outright price for a top favorite; no basis to dev |
| KXBOXING-26SEP12GARCIABENN-GARCIA | PASS | LOW | 0.690 | 0.690 | 1 | Could not confirm fight outcome or any pre-fight news due to web tool access failure; per sports-market rule, defer to market pricing absent verified  |
| KXBRENTMON-26AUG3117-T84.99 | PASS | LOW | 0.780 | 0.780 | 1 | No access to actual Brent settlement data due to tool failures; price-level market default is to match market absent a verified catalyst. |
| KXCBDECISIONNZ-26SEP01-H25 | PASS | LOW | 0.940 | 0.940 | 1 | Central bank decision markets are typically well-calibrated to public information (similar to CME FedWatch dynamics); with no ability to verify RBNZ s |
| KXCPICOREYOY-26AUG-T2.1 | PASS | LOW | 0.930 | 0.930 | 1 | Core CPI has been persistently above 2.1% YoY for an extended period historically, consistent with the market's high-confidence pricing; no new data a |
| KXEMMY-VS26SEP14-TIE | PASS | LOW | 0.200 | 0.290 | 0 | Exact ties in major Emmy categories are historically rare; without ability to verify the specific market structure or nominee field this session, edge |
| KXINDYCARSERIES-NTTICS26-KKIR | PASS | LOW | 0.030 | 0.030 | 0 | Sub-15% tail-probability market; per calibration rules, trust the crowd absent independently verified evidence, which was unobtainable this session du |
| KXLEAGUESCUP-26-RSL | PASS | LOW | 0.130 | 0.130 | 0 | Sub-15% tail-probability soccer market; defer to market pricing given no accessible verification this session. |
| KXNCAAFGAME-26SEP06LOUMISS-MISS | PASS | LOW | 0.730 | 0.730 | 1 | College football match outcome market; sports markets are efficiently priced and no injury/lineup edge could be verified this session, so defer to mar |
| KXNCAAFGAME-26SEP12OSUTEX-OSU | PASS | LOW | 0.480 | 0.480 | 0 | Web search/fetch tools were unavailable this session (permission denied), so no fresh evidence could be gathered. Sports matchup markets are efficient |
| KXNFLGAME-26SEP13DALNYG-DAL | PASS | LOW | 0.580 | 0.580 | 0 | Web search/fetch tools were unavailable this session, so no fresh evidence on injuries/form could be gathered. NFL matchup markets are efficiently pri |
| KXROLEINPRODUCTIONLI-27JUL31-ARI | PASS | LOW | 0.770 | 0.790 | 1 | Web search/fetch tools were unavailable this session to confirm current host status. Ariana Madix has been the established Love Island USA host in rec |
| KXSP500ADDQ-26SEP30-BE | PASS | LOW | 0.520 | 0.570 | 1 | Web search/fetch tools were unavailable this session to verify Bloom Energy's eligibility criteria (market cap, trailing profitability) or any S&P com |
| KXSPACEXCOUNT-26AUG-14 | PASS | LOW | 0.150 | 0.140 | 0 | Web search/fetch tools were unavailable this session to check actual August 2026 SpaceX launch cadence. Market price is already low (Rule 1, tail prob |
| KXU3-26AUG-T4.0 | PASS | LOW | 0.850 | 0.860 | 1 | Web search/fetch tools were unavailable this session to confirm the actual August 2026 BLS unemployment print. Unemployment has trended near/above 4%  |
