# Leviathan Backlog
Last updated: 2026-08-22 | Metrics: resolved=14, fills=7

Action text below is summarized. Full narrative per item is `backlog/backlog.json`'s `action` field -- this file is auto-generated, never hand-edit it.

## Ready (1)
| Priority | ID | Action | Area |
|----------|-----|--------|------|
| 3 | smart-money-discovery-dashboard | User asked (2026-08-22) whether a dashboard insight for winning-trader/whale detection is possible despite the project's low resolved-bet sample size. | reporting |

## Locked (5)
| Priority | ID | Gate | Area |
|----------|-----|------|------|
| 4 | per-wallet-track-record | resolved_count_per_wallet_max >= 10 | smart-money |
| 5 | calibration-curve | resolved_count >= 50 | calibration |
| 5 | edge-decay-analysis | resolved_count >= 30 | validation |
| 5 | skill-vs-luck-weighting | resolved_count_per_wallet_max >= 10 | smart-money |
| 5 | slippage-tracking | fills_count >= 20 | execution |

## Blocked (7)
| Priority | ID | Waiting On | Area |
|----------|-----|-----------|------|
| 3 | replay-instrument-validation | replay-runner, market-baseline-brier | validation |
| 4 | cross-venue-expansion | net-edge-fee-depth-model, replay-instrument-validation | data-quality |
| 5 | methodology-writeup | replay-instrument-validation, preregistration | reporting |
| 5 | wallet-tracking-dashboard | per-wallet-track-record | reporting |
| 6 | auto-calibration-loop | sample-size-gates, brier-tracking | calibration |
| 6 | calibration-curve-dashboard | calibration-curve | reporting |
| 6 | graphify-skill-evaluation | - | infra |

## Done (63)
| Priority | ID | Action | Area |
|----------|-----|--------|------|
| 1 | brier-tracking | get_brier_score()/get_market_baseline_brier_score() already existed but only ever computed a single CURRENT-MOMENT aggregate over all resolved signals at call time -- nothing persisted a… | calibration |
| 1 | ci-kalshi-auth-env-2026-08 | 3 GitHub Actions CI runs failed on tests.yml (added earlier this session) -- traced via the failure-notification emails to a separate, pre-existing automated "smart money scan" commit process pushing… | infra |
| 1 | confluence-detection | main.py already computed _count_agreeing_signals() (Polymarket price gap, cross-market consensus, whale detection, orderbook imbalance, watchlist alignment) but only persisted it as ext_n_signals… | validation |
| 1 | db-audit-2026-08 | Full audit of all 5 tables (signals, runs, settled_markets, replay_signals, blind_scores) for missing/inconsistent data. | data-quality |
| 1 | down-ballot-election-recalibration | "down-ballot election" (n=43, gap +0.287, predicted 52% vs actual 23.3%) was a mixed population, not a single miscalibration. | calibration |
| 1 | export-validation-pass-exclusion | core.export_to_csv._print_validation's Resolved/Wins/Losses/Win-Rate counts included PASS-direction rows, which resolve_outcomes() always grades LOSS by construction (direction == outcome can never… | data-quality |
| 1 | ext-signal-activation | Investigated: ext_estimate/ext_edge/ext_n_signals/ext_alpha are 100% blank not due to a bug but because their >=2-agreeing-signals trigger has never once been met -- reconstructed… | calibration |
| 1 | heuristic-backtest-tool | New analysis/heuristic_backtest.py: free (no LLM/API cost) calibration study of core.scanner's title-keyword heuristic table against the settled_markets corpus (12,600 real Kalshi resolutions) --… | calibration |
| 1 | heuristic_label-vs-base_rate-desync | core.scanner.estimate_base_rate() and get_heuristic_label() were two independently-ordered pattern lists (195 vs 479 entries) that had drifted apart. | data-quality |
| 1 | hurricane-recalibration | Investigated: unlike the three prior ladder/many-way fixes, "hurricane" (n=29, gap +0.416, predicted 45% vs actual 3.4%) was a genuine MIXED bag of two structurally distinct sub-patterns, confirmed… | calibration |
| 1 | kalshi-sdk-evaluation-2026-08 | Live-tested both SDK candidates against the real Kalshi API with real credentials (read-only calls: get_balance, get_markets), not just docs/README research. | infra |
| 1 | log-pass-schema-parity | core.logger.log_pass()'s INSERT never included event_ticker, series_ticker, watchlist_signal, sig_edge, sig_drift, sig_br_none, net_edge_after_fee or ev_after_fee_per_contract in its column list --… | data-quality |
| 1 | market-baseline-brier | For every resolved signal, compute and persist the Brier score of the market price at scan time alongside the existing scorer Brier. | validation |
| 1 | multi-sample-scoring | score_markets() (core/scorer.py) took config["scoring"]["multi_sample_n"] (default 1, unchanged behavior) -- when set >1, runs the SAME batch/prompt through the configured backend that many times… | calibration |
| 1 | near-dated-markets-supplement | fetch_events()'s events-catalog path (main.py's and analysis/snapshot_markets.py's primary market fetch) structurally never surfaces near-dated markets -- confirmed empirically 2026-08-01: 0 of 2722… | data-quality |
| 1 | near-dated-window-chunking | Investigated why resolve_first.py (near-dated-markets-supplement's mechanism for finding short-term bets) had logged just 1 row since being built on 2026-08-01, despite its daily scheduled task… | data-quality |
| 1 | preregistration | Write docs/PREREGISTRATION.md stating, in advance and dated, the result at n=50 that would falsify the edge hypothesis and halt signal development. | validation |
| 1 | price-threshold-recalibration | Investigated: core.scanner's "price threshold" heuristic (bare 'reach $'/'hits $'/'above $'/'below $'/'under $' etc., rate 0.35) predicted 35% but real rate was 82-100% -- but NOT because 0.35 was… | calibration |
| 1 | production-delivery-milestone-recalibration | Investigated: same root cause as price-threshold-recalibration, applied immediately after finding it. | calibration |
| 1 | prop-market-skill-filter | Added prop_market_exclude_prefixes config (mirrors near_dated_exclude_prefixes/KXMVE) and a ticker-prefix check in core.scanner.filter_markets(), applied before every other filter. | data-quality |
| 1 | realfill-dedup | Audit real_fill rows in leviathan.db and remove duplicate fills that do not match actual positions held. | data-quality |
| 1 | resolved-count-metric-desync | backlog/checker.py::compute_metrics()'s resolved_count SQL diverged from core/logger.py::get_stats()['resolved'] (the number scripts/gate_notifier.py's actual gate-unlock emails use) two ways: no… | infra |
| 1 | show-detail-fix | Decouple show_detail in compile_report from the scanner qualifying count; gate it on whether smart-money data itself has signals, so trader detail stops silently vanishing during signal dry spells. | reporting |
| 1 | show-renewal-recalibration | Investigated: the "show renewal" heuristic (bare 'season 2'..'season 9'/'movie'/'film' keywords, rate 0.25) wasn't about renewal at all -- sampled real matches and every one was a many-way… | calibration |
| 1 | sports-award-recalibration | Drafted autonomously by the first live run of the new investigate-and-draft weekly audit (Leviathan-WeeklyAudit, 2026-08-02), then independently re-verified by hand before commit (full-corpus diff… | calibration |
| 1 | subscriber-report-rework-2026-08 | Compared the live subscriber_preview.html output against the user's reference design (Downloads folder) -- the CSS/template hadn't drifted (GOAL_subscriber_report.md Phases 1-5 all render correctly),… | reporting |
| 1 | subscriber-report-wiring | Created Leviathan-SubscriberReport (daily 9am, matches the existing 4-task pattern) running scripts/render_subscriber_preview.py, output redirected to logs/subscriber_report.log. | reporting |
| 1 | trade-reconciliation | Reconcile paper signals against actual Kalshi fills to confirm each signal has a corresponding real trade. | execution |
| 1 | whale-flag-lv-guarantee | User asked why the run header's whale-flag count (e.g. | calibration |
| 1 | whale-only-none-direction-crash | Real production incident, same day as whale-flag-lv-guarantee shipped: a manually-triggered 2026-08-05 run (real Kalshi/Claude calls, same as any scheduled run) crashed inside compile_report() with… | reporting |
| 1 | win-catchall-recalibration | analysis/heuristic_backtest.py (2026-08-01, free/no-LLM-cost study against the 12,600-row settled_markets corpus) found core.scanner's generic `" win "` catch-all pattern (base_rate 0.52, the single… | calibration |
| 2 | citations-provenance-grounding | Ground scorer output in the market's supplied sources via the Anthropic Citations API, so each rationale claim carries a machine-checkable cited-text span (document index + char offset) rather than… | validation |
| 2 | discovery-funnel-diagnostic | Per-stage drop-off counter + gating-metric distributions for discover_winners; diagnoses why the winner gate finds zero (sample sourcing vs. | smart-money |
| 2 | email-html-render | Render the daily report as email-safe HTML (multipart/alternative) matching the signed-off design, consuming goal_1 Kalshi links, sharing computed values with the text renderer so the two bodies can… | reporting |
| 2 | fix-fetch-market-history-endpoint | core.kalshi.fetch_market_history() calls /markets/{ticker}/history, which does not exist on Kalshi's API -- confirmed empirically on 2026-07-25: every ticker tried, including active high-volume… | data-quality |
| 2 | kalshi-event-ticker-capture | Persist event_ticker (already fetched at scan time) onto every logged signal; investigate the real kalshi.com market-page URL pattern. | data-quality |
| 2 | llm-cost-ceiling | Add a configurable daily spend cap in core/llm.py that accumulates cost_usd across calls and raises once breached. | infra |
| 2 | powerbi-schema-hardening | Add run_id to data/powerbi_export/signals.csv as a foreign key to runs.csv. | reporting |
| 2 | replay-asof-reconstruction | Given a ticker and a historical date, reconstruct the market state as it stood then, sourcing from data/snapshots where available and Kalshi history beyond that. | backtesting |
| 2 | replay-settled-fetcher | Pull Kalshi settled markets with their final outcomes, reaching further back than the local snapshot archive begins. | backtesting |
| 2 | sample-size-gates | Document the minimum resolved-signal thresholds that gate each downstream analysis step. | validation |
| 2 | wilson-intervals | Add Wilson score confidence intervals to win-rate stats in the email report. | reporting |
| 3 | backtest-harness | Build a framework to replay historical signals against resolved market outcomes. | backtesting |
| 3 | gate-unlock-notifier | Email once when a BACKLOG.md gate transitions locked/unknown -> unlocked, reusing the existing report email path. | reporting |
| 3 | replay-runner | Drive backtesting/harness.py over the reconstructed corpus and grade each replayed score against the known settled outcome. | backtesting |
| 3 | smart-money-drift-alerts | Alert when a tracked wallet materially shifts position size or direction between daily scans. | smart-money |
| 3 | title-scraping-fix | Fix market title capture so titles are populated correctly for all logged signal rows. | data-quality |
| 4 | empirical-base-rates-poly | Replace heuristic base rates with empirical rates derived from Polymarket historical outcomes. | calibration |
| 4 | kalshi-sdk-migration-implementation | Built the adapter, but not the field-by-field typed-model remapping the Ready-item scope note called for. | infra |
| 4 | net-edge-fee-depth-model | net_edge_after_fee priced a trade off the top-of-book quote only -- it had no idea whether the visible order book could actually fill unit_size contracts on the side the picked direction needed. | execution |
| 4 | per-heuristic-scorecard | get_stats_by_heuristic_label() (core/logger.py) already existed -- win rate/P&L/avg_edge grouped by heuristic_label -- and was already wired into a real display in analysis/calibration.py's "BY… | reporting |
| 4 | position-reconciliation-job | Automate daily reconciliation of open paper signals against the Kalshi position API. | execution |
| 4 | price-blind-arm | Add a scoring mode that omits the Current market price line and all market-anchoring instructions. | validation |
| 4 | signal-csv-strategy-review-2026-08 | User asked for a strategy-standpoint review of signals.csv -- are we capturing everything useful, is there anything we could collect but aren't. | reporting |
| 4 | signal-scan-log-split-2026-08 | User opened signals.csv directly in Excel and found it looked mostly empty/broken. | data-quality |
| 4 | streamlit-dashboard-2026-08 | Built a free local Streamlit dashboard (dashboard/) as a Power BI alternative -- three pages (Overview, Signal Breakdown, Signal Log) reading the existing data/powerbi_export CSV export, additive… | reporting |
| 4 | unattended-ops | Alert on absence rather than presence: notify if no successful run has completed within N hours. | infra |
| 4 | whale-actionability-scorecard | User asked what we're actually doing with whale-flag data beyond identifying it -- the report's WHALE ACTIVITY table just lists sightings (a market a whale traded, no track record attached), so there… | reporting |
| 5 | betting-queue | Show top 5 unplaced signals sorted by urgency in daily report | reporting |
| 5 | ev-per-contract | Show EV/contract in signal blocks and top picks summary | reporting |
| 5 | heuristic-sunsetting | Ran analysis/heuristic_backtest.py fresh and screened every label with n>=10 for underperformance -- but the correct comparison isn't raw Brier vs. | calibration |
| 5 | high-price-filter | Filter out markets at or above 0.85 market price before writing to DB | data-quality |
| 5 | walk-forward-validation | Run rolling out-of-sample validation on the scoring model using the backtest harness. | backtesting |
