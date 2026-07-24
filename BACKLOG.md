# Leviathan Backlog
Last updated: 2026-07-23 | Metrics: resolved=11, fills=7

## Ready (5)
| Priority | ID | Action | Area |
|----------|-----|--------|------|
| 1 | preregistration | Write docs/PREREGISTRATION.md stating, in advance and dated, the result at n=50 that would falsify the edge hypothesis and halt signal development. Specify the metric (scorer Brier vs market-baseline Brier), the margin required, and what happens if it is not met. | validation |
| 2 | llm-cost-ceiling | Add a configurable daily spend cap in core/llm.py that accumulates cost_usd across calls and raises once breached. Surface the running total in the daily report footer. | infra |
| 2 | powerbi-schema-hardening | Add run_id to data/powerbi_export/signals.csv as a foreign key to runs.csv. Confirm the source column reliably distinguishes live paper signals from any future replay corpus rows. Audit every column where blank currently appears and document, per column, whether blank means not-computed or zero. | reporting |
| 2 | replay-settled-fetcher | Pull Kalshi settled markets with their final outcomes, reaching further back than the local snapshot archive begins. Persist to a dedicated table separate from live signals. | backtesting |
| 4 | unattended-ops | Alert on absence rather than presence: notify if no successful run has completed within N hours. Add graceful degradation when an upstream API changes shape, and a runbook in docs/ for diagnosing a failed run without reloading full project context. | infra |

## Locked (9)
| Priority | ID | Gate | Area |
|----------|-----|------|------|
| 4 | brier-tracking | resolved_count >= 25 | calibration |
| 4 | confluence-detection | resolved_count >= 25 | validation |
| 4 | per-heuristic-scorecard | resolved_count_per_category_max >= 15 | reporting |
| 4 | per-wallet-track-record | resolved_count_per_wallet_max >= 10 | smart-money |
| 5 | calibration-curve | resolved_count >= 50 | calibration |
| 5 | edge-decay-analysis | resolved_count >= 30 | validation |
| 5 | heuristic-sunsetting | resolved_count_per_category_max >= 15 | calibration |
| 5 | skill-vs-luck-weighting | resolved_count_per_wallet_max >= 10 | smart-money |
| 5 | slippage-tracking | fills_count >= 20 | execution |

## Blocked (9)
| Priority | ID | Waiting On | Area |
|----------|-----|-----------|------|
| 2 | replay-asof-reconstruction | replay-settled-fetcher | backtesting |
| 3 | replay-instrument-validation | replay-runner, market-baseline-brier | validation |
| 3 | replay-runner | replay-asof-reconstruction, llm-cost-ceiling | backtesting |
| 4 | price-blind-arm | market-baseline-brier, llm-cost-ceiling | validation |
| 5 | methodology-writeup | replay-instrument-validation, preregistration | reporting |
| 5 | multi-sample-scoring | llm-cost-ceiling, market-baseline-brier | calibration |
| 5 | wallet-tracking-dashboard | per-wallet-track-record | reporting |
| 6 | auto-calibration-loop | sample-size-gates, brier-tracking | calibration |
| 6 | calibration-curve-dashboard | calibration-curve | reporting |

## Done (19)
| Priority | ID | Action | Area |
|----------|-----|--------|------|
| 1 | market-baseline-brier | For every resolved signal, compute and persist the Brier score of the market price at scan time alongside the existing scorer Brier. Expose our_estimate, brier_scorer and brier_market as explicit columns in data/powerbi_export/signals.csv, and surface both Brier figures in analysis/calibration.py output. | validation |
| 1 | realfill-dedup | Audit real_fill rows in leviathan.db and remove duplicate fills that do not match actual positions held. | data-quality |
| 1 | show-detail-fix | Decouple show_detail in compile_report from the scanner qualifying count; gate it on whether smart-money data itself has signals, so trader detail stops silently vanishing during signal dry spells. | reporting |
| 1 | trade-reconciliation | Reconcile paper signals against actual Kalshi fills to confirm each signal has a corresponding real trade. | execution |
| 2 | discovery-funnel-diagnostic | Per-stage drop-off counter + gating-metric distributions for discover_winners; diagnoses why the winner gate finds zero (sample sourcing vs. skill genuinely rare). No threshold/gate changes. Unblocks the locked per-wallet items. | smart-money |
| 2 | email-html-render | Render the daily report as email-safe HTML (multipart/alternative) matching the signed-off design, consuming goal_1 Kalshi links, sharing computed values with the text renderer so the two bodies can never diverge. | reporting |
| 2 | kalshi-event-ticker-capture | Persist event_ticker (already fetched at scan time) onto every logged signal; investigate the real kalshi.com market-page URL pattern. | data-quality |
| 2 | sample-size-gates | Document the minimum resolved-signal thresholds that gate each downstream analysis step. | validation |
| 2 | wilson-intervals | Add Wilson score confidence intervals to win-rate stats in the email report. | reporting |
| 3 | backtest-harness | Build a framework to replay historical signals against resolved market outcomes. | backtesting |
| 3 | gate-unlock-notifier | Email once when a BACKLOG.md gate transitions locked/unknown -> unlocked, reusing the existing report email path. | reporting |
| 3 | smart-money-drift-alerts | Alert when a tracked wallet materially shifts position size or direction between daily scans. | smart-money |
| 3 | title-scraping-fix | Fix market title capture so titles are populated correctly for all logged signal rows. | data-quality |
| 4 | empirical-base-rates-poly | Replace heuristic base rates with empirical rates derived from Polymarket historical outcomes. | calibration |
| 4 | position-reconciliation-job | Automate daily reconciliation of open paper signals against the Kalshi position API. | execution |
| 5 | betting-queue | Show top 5 unplaced signals sorted by urgency in daily report | reporting |
| 5 | ev-per-contract | Show EV/contract in signal blocks and top picks summary | reporting |
| 5 | high-price-filter | Filter out markets at or above 0.85 market price before writing to DB | data-quality |
| 5 | walk-forward-validation | Run rolling out-of-sample validation on the scoring model using the backtest harness. | backtesting |
