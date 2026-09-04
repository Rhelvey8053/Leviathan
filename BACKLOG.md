# Leviathan Backlog
Last updated: 2026-09-04 | Metrics: resolved=25, fills=7

Action text below is summarized. Full narrative per item is `backlog/backlog.json`'s `action` field -- this file is auto-generated, never hand-edit it.

## Ready (7)
| Priority | ID | Action | Area |
|----------|-----|--------|------|
| 2 | task-scheduler-manual-trigger-stuck-queued | Found 2026-08-24 while verifying automation_health_check.py and daily_digest.py's live scheduled-task runs: manually triggering an S4U-logon scheduled task (Start-ScheduledTask or schtasks /run) gets… | infra |
| 3 | cftc-rule-40-11-event-contract-rulemaking | Found 2026-08-25 via direct research (CFTC.gov press release, Federal Register, Greenberg Traurig's legal summary of the NPRM -- not just secondary news), prompted by expanding Liam's… | data-quality |
| 3 | windows-defender-cpu-contention-2026-08-30 | Found 2026-08-30 while investigating a garbled weekly_code_audit.py run and a main.py catch-up run that appeared stuck on one step for a long stretch (both eventually completed -- confirmed slow, not… | infra |
| 4 | cross-venue-expansion | Ingest more than two venues via a normalized aggregator layer, match identical markets across them, and surface fee-adjusted cross-venue gaps. | data-quality |
| 4 | smart-money-fills-persistence-build | Split out 2026-08-26 from smart-money-fills-table-missing (which only fixed the silent-failure visibility problem, not the underlying gap). | infra |
| 4 | trial-stronger-model-main-scoring | Using the now-live config.llm.cli_model_override (see wire-llm-model-cli-flag, done -- no depends_on here since that item is already done as of this item's own creation, not a real gate), run a… | calibration |
| 5 | methodology-writeup | Write a public methodology document covering the pipeline architecture, the market-price anchoring problem, the baseline comparison, and the pre-registered kill criteria. | reporting |

## Locked (6)
| Priority | ID | Gate | Area |
|----------|-----|------|------|
| 4 | empirical-base-rates-poly | sufficient_per_heuristic_label_resolved_data == 1 | calibration |
| 4 | per-wallet-track-record | resolved_count_per_wallet_max >= 10 | smart-money |
| 5 | calibration-curve | resolved_count >= 50 | calibration |
| 5 | edge-decay-analysis | resolved_count >= 30 | validation |
| 5 | skill-vs-luck-weighting | resolved_count_per_wallet_max >= 10 | smart-money |
| 5 | slippage-tracking | fills_count >= 20 | execution |

## Blocked (4)
| Priority | ID | Waiting On | Area |
|----------|-----|-----------|------|
| 5 | wallet-tracking-dashboard | per-wallet-track-record | reporting |
| 6 | auto-calibration-loop | sample-size-gates, brier-tracking | calibration |
| 6 | calibration-curve-dashboard | calibration-curve | reporting |
| 6 | graphify-skill-evaluation | - | infra |

## Done (96)
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
| 2 | automation-health-monitoring | Built and verified 2026-08-24. | infra |
| 2 | citations-provenance-grounding | Ground scorer output in the market's supplied sources via the Anthropic Citations API, so each rationale claim carries a machine-checkable cited-text span (document index + char offset) rather than… | validation |
| 2 | discovery-funnel-diagnostic | Per-stage drop-off counter + gating-metric distributions for discover_winners; diagnoses why the winner gate finds zero (sample sourcing vs. | smart-money |
| 2 | email-html-render | Render the daily report as email-safe HTML (multipart/alternative) matching the signed-off design, consuming goal_1 Kalshi links, sharing computed values with the text renderer so the two bodies can… | reporting |
| 2 | fix-fetch-market-history-endpoint | core.kalshi.fetch_market_history() calls /markets/{ticker}/history, which does not exist on Kalshi's API -- confirmed empirically on 2026-07-25: every ticker tried, including active high-volume… | data-quality |
| 2 | fix-weekly-code-audit-timeout | Found 2026-08-24 via daily_digest.py's new weekly-log-tail section -- previously silent, since output only ever went to logs/weekly_code_audit.log, which nobody had reason to open. | infra |
| 2 | kalshi-event-ticker-capture | Persist event_ticker (already fetched at scan time) onto every logged signal; investigate the real kalshi.com market-page URL pattern. | data-quality |
| 2 | kalshi-wa-geofencing-exposure-check | Investigated 2026-08-24, confirmed with the user directly: the Kalshi account/operator is NOT based in Washington State, so the WA geofencing order does not restrict this project's own market access. | infra |
| 2 | llm-cost-ceiling | Add a configurable daily spend cap in core/llm.py that accumulates cost_usd across calls and raises once breached. | infra |
| 2 | near-dated-fetch-headroom-increase | Companion to resolve-first-top-n-per-bucket, same investigation. | data-quality |
| 2 | polymarket-data-api-rate-limit-pacing | Root-caused and fixed 2026-08-23. | smart-money |
| 2 | powerbi-schema-hardening | Add run_id to data/powerbi_export/signals.csv as a foreign key to runs.csv. | reporting |
| 2 | replay-asof-reconstruction | Given a ticker and a historical date, reconstruct the market state as it stood then, sourcing from data/snapshots where available and Kalshi history beyond that. | backtesting |
| 2 | replay-settled-fetcher | Pull Kalshi settled markets with their final outcomes, reaching further back than the local snapshot archive begins. | backtesting |
| 2 | resolve-first-top-n-per-bucket | User asked to expand scope on active bets to collect resolved_count data faster, without breaking core practices (many backlog gates -- calibration-curve n>=50, edge-decay-analysis n>=30,… | data-quality |
| 2 | sample-size-gates | Document the minimum resolved-signal thresholds that gate each downstream analysis step. | validation |
| 2 | smart-money-fills-table-missing | Found 2026-08-24/25 by scripts/weekly_code_audit.py's live audit run (the same run used to verify the Write->Edit permission fix): the resolved_count_per_wallet_max backlog-gate metric queries a… | infra |
| 2 | wake-triggered-task-catchup | Built 2026-08-24, registered and confirmed State=Ready (not stuck Queued, unlike the two tasks registered earlier the same day). | infra |
| 2 | wilson-intervals | Add Wilson score confidence intervals to win-rate stats in the email report. | reporting |
| 3 | backtest-harness | Build a framework to replay historical signals against resolved market outcomes. | backtesting |
| 3 | cli-backend-token-telemetry | User asked to research reducing Leviathan's own token footprint (after a separate detour into whether OmniRoute could help -- rejected, since routing Claude Code through it means replacing native… | infra |
| 3 | daily-operations-digest | Built and verified 2026-08-24. | infra |
| 3 | dailyrun-logontype-interactive | Found 2026-08-25 while re-registering Leviathan-DailyRun to pick up a RestartCount/RestartInterval change: scripts/schedule_setup.ps1's Principal block uses -LogonType Interactive, unlike every other… | infra |
| 3 | dailyrun-missed-run-2026-08-30-silent-failure-gaps | Found 2026-08-30: main.py's scheduled 7am run launched a real process (confirmed via Task Scheduler operational event log, PID 20048, ran 21m58s) and Task Scheduler reported clean exit 0, but zero… | infra |
| 3 | dashboard-caption-accuracy-passthrough | User feedback after the Smart Money redesign (smart-money-winning-whales-panel): a direct question ("so the streak on the table is associated with a wallet correct?") caught a real inaccuracy in my… | reporting |
| 3 | dependabot-setup | .github/dependabot.yml added 2026-08-24, covering pip (root requirements.txt, used by CI; dashboard/requirements.txt, not CI-checked but a real dependency manifest) and github-actions… | infra |
| 3 | gate-unlock-notifier | Email once when a BACKLOG.md gate transitions locked/unknown -> unlocked, reusing the existing report email path. | reporting |
| 3 | market-price-divergence-tracking | Built and verified 2026-08-23/24. | calibration |
| 3 | mcp-server-v2-operational-tools | User (as PM, following Liam/monday.com's retirement) asked what new plugins or connectors could streamline the project. | infra |
| 3 | model-used-field-disconnected-from-cli-override | Found 2026-09-01 while checking the first Opus-trial run's result row: run_meta['model_used'] (main.py:246) read config.scoring.scorer_model -- a static, cosmetic-only field never passed to any… | infra |
| 3 | monday-com-retired-backlog-dashboard-page | User's monday.com trial expired 2026-08-30. | infra |
| 3 | replay-instrument-validation | Using the replay corpus at n>=300, verify the measurement apparatus: grading handles early closes, voided markets and multi-outcome events; baseline Brier computes correctly across the full price… | validation |
| 3 | replay-runner | Drive backtesting/harness.py over the reconstructed corpus and grade each replayed score against the known settled outcome. | backtesting |
| 3 | replay-runner-crash-on-malformed-cli-response | Found 2026-08-28 running a replay-instrument-validation corpus-build batch: it crashed with 'str' object has no attribute 'keys'. | validation |
| 3 | research-diligence-before-pass | User request: "I don't want to pass on bets... | calibration |
| 3 | resolve-first-never-carried-category | User feedback: dashboard/live category diversity felt low. | data-quality |
| 3 | resolved-count-per-category-max-wrong-column | Found 2026-08-27 while fixing empirical-base-rates-poly's gating (see that item's notes). | calibration |
| 3 | signal-category-mostly-blank-despite-real-data | Found 2026-08-25 while adding a win-rate-by-category chart to the dashboard at the owner's request -- only 4 of 51 rows in signals.csv have a populated `category` (Sports, Economics, Entertainment,… | data-quality |
| 3 | smart-money-discovery-dashboard | Built and verified 2026-08-23/24. | reporting |
| 3 | smart-money-drift-alerts | Alert when a tracked wallet materially shifts position size or direction between daily scans. | smart-money |
| 3 | smart-money-winning-whales-panel | User feedback: the old dashboard/pages/4_Smart_Money.py page was not clear about what the most recent trades were, raw Kalshi tickers were unreadable, and there was no way to see or act on which… | reporting |
| 3 | title-scraping-fix | Fix market title capture so titles are populated correctly for all logged signal rows. | data-quality |
| 3 | weekly-code-audit-exit-code-not-proof-of-report | Found 2026-08-30 while investigating that day's missed DailyRun (see dailyrun-missed-run-2026-08-30-silent-failure-gaps): Leviathan-CodeAudit's Sunday run showed LastTaskResult=0 in Task Scheduler,… | infra |
| 3 | wire-llm-model-cli-flag | Second, independent lever from the same user request ('expand scope... | infra |
| 4 | cross-model-corroboration | User pushback ('I don't see how this wouldn't benefit the project and you're rejecting it entirely') on an earlier OmniRoute rejection was correct to push on -- that rejection conflated two different… | calibration |
| 4 | kalshi-sdk-migration-implementation | Built the adapter, but not the field-by-field typed-model remapping the Ready-item scope note called for. | infra |
| 4 | net-edge-fee-depth-model | net_edge_after_fee priced a trade off the top-of-book quote only -- it had no idea whether the visible order book could actually fill unit_size contracts on the side the picked direction needed. | execution |
| 4 | per-heuristic-scorecard | get_stats_by_heuristic_label() (core/logger.py) already existed -- win rate/P&L/avg_edge grouped by heuristic_label -- and was already wired into a real display in analysis/calibration.py's "BY… | reporting |
| 4 | position-reconciliation-job | Automate daily reconciliation of open paper signals against the Kalshi position API. | execution |
| 4 | price-blind-arm | Add a scoring mode that omits the Current market price line and all market-anchoring instructions. | validation |
| 4 | rolled-market-repeat-detection | Investigated then built 2026-08-24. | calibration |
| 4 | signal-csv-strategy-review-2026-08 | User asked for a strategy-standpoint review of signals.csv -- are we capturing everything useful, is there anything we could collect but aren't. | reporting |
| 4 | signal-scan-log-split-2026-08 | User opened signals.csv directly in Excel and found it looked mostly empty/broken. | data-quality |
| 4 | streamlit-dashboard-2026-08 | Built a free local Streamlit dashboard (dashboard/) as a Power BI alternative -- three pages (Overview, Signal Breakdown, Signal Log) reading the existing data/powerbi_export CSV export, additive… | reporting |
| 4 | unattended-ops | Alert on absence rather than presence: notify if no successful run has completed within N hours. | infra |
| 4 | verify-liam-post-context-doc-alignment | Liam's most recent monday.com report as of 2026-08-22 (timestamped 2026-08-20 08:00 AM CT) recommended moving auto-calibration-loop and replay-instrument-validation to Ready -- both wrong per… | infra |
| 4 | whale-actionability-scorecard | User asked what we're actually doing with whale-flag data beyond identifying it -- the report's WHALE ACTIVITY table just lists sightings (a market a whale traded, no track record attached), so there… | reporting |
| 5 | betting-queue | Show top 5 unplaced signals sorted by urgency in daily report | reporting |
| 5 | ev-per-contract | Show EV/contract in signal blocks and top picks summary | reporting |
| 5 | heuristic-sunsetting | Ran analysis/heuristic_backtest.py fresh and screened every label with n>=10 for underperformance -- but the correct comparison isn't raw Brier vs. | calibration |
| 5 | high-price-filter | Filter out markets at or above 0.85 market price before writing to DB | data-quality |
| 5 | metaculus-community-prediction-inaccessible | Found 2026-08-25 while setting up the (previously dormant, missing-token) Metaculus integration at the owner's request. | data-quality |
| 5 | subscriber-hosting-billing-decision | User asked this session about turning the subscriber digest into an actual paid-subscription product, then explicitly sidelined it to focus on token-usage reduction instead. | reporting |
| 5 | subscriber-report-removed-2026-08 | User question: if the strategy is proven profitable, why send picks to subscribers instead of trading it directly? Investigated before acting -- found neither justification for the feature actually… | infra |
| 5 | walk-forward-validation | Run rolling out-of-sample validation on the scoring model using the backtest harness. | backtesting |
