<!-- Last narrative update: 2026-07-02 — reorganized for dual technical/portfolio audience; no code or data claims changed -->

# LEVIATHAN // PREDICTION MARKET INTELLIGENCE

Leviathan is an automated signal detection system for [Kalshi](https://kalshi.com), a regulated US exchange where traders buy and sell contracts on the probability of real-world events — elections, economic reports, sports outcomes, and more. Each day it scans thousands of open contracts, cross-references the same events on five external platforms, tracks the open positions of the highest-PnL traders in the space, and scores candidate markets using a combination of heuristics and LLM-based probability estimation. The output is a structured daily email report and a persistent record of every signal — market price at the time of the call, our probability estimate, and eventual outcome — with the long-term goal of determining whether systematic edge is real, where it comes from, and whether it holds under live conditions.

---

## System Status

- **Phase:** Data accumulation — 8 resolved paper signals as of 2026-07-14 (next gate: n=20 before calibration analysis is meaningful)
- **Mode:** Read-only — no trade execution. All signals are paper.
- **Test suite:** 1617 tests, 0 failures
- **Verified track record (2026-07-14):** win rate 38%, Brier score 0.0578 (EXCELLENT), hypothetical P&L -$1.66 at $10/contract. PnL integrity confirmed via `scripts/verify_pnl.py` (0 deltas across all resolved rows — no backfill needed). Source: `analysis/calibration.py`. These are the only figures cited anywhere as the current track record — n=8 is far below the n=20 gate, so read them as an integrity checkpoint, not a performance claim.

### Validation approach

Signals are tracked from generation through settlement, each with a logged market price, probability estimate, and eventual outcome. Accuracy is measured using Brier score — a proper scoring rule for probability calibration, not just directional win/loss rate — so the system can distinguish between "correctly directional" and "well-calibrated." Feature development is gated on data conditions rather than calendar dates: calibration-dependent improvements (position sizing, threshold tuning, confidence weighting) are explicitly deferred until n≥20 resolved signals exist, because before that threshold any accuracy metric is too noisy to act on. No claim of profitability is made — the current record doesn't support one, and the system is designed to surface that honestly.

---

## How It Works

Each daily run executes an 8-step pipeline:

| Step | Layer | What happens |
|---|---|---|
| 1 | Auth | Connects to Kalshi and resolves any markets that have settled since the last run |
| 2 | Fetch | Downloads 2,400+ open markets across 400 active events |
| 3 | Filter | Drops liquid, efficiently-priced, and structurally uninteresting markets; deduplicates by event; tags any market where a tracked smart-money trader holds a position |
| 4 | Cross-reference | Finds the same question on Polymarket, Manifold, PredictIt, Metaculus, and The Odds API — price gaps between platforms are a primary signal input |
| 5 | Whale detection | Flags unusually large individual trades and order-book imbalances that may indicate informed positioning |
| 6 | Score | Scores flagged markets using Claude with live web search, anchored by 11 calibration rules that ground estimates in base rates and cross-market evidence |
| 7 | Log + Smart money | Persists signals to SQLite; runs the watchlist scan (fetches open positions for 20 tracked traders, cross-references to Kalshi markets by title similarity) |
| 8 | Report | Compiles and emails the daily plain-text report |

**Watchlist markets** (confirmed smart money positions) bypass the normal flag requirement — they reach Claude scoring even if no drift or heuristic edge fired, with a `WATCHLIST` flag path.

---

## What This Demonstrates

Leviathan was built as a self-directed systems project: no course requirement, no existing codebase to extend, no team. The scope — API integration across six external platforms, a multi-layer signal pipeline, SQLite persistence, automated reporting, Windows Task Scheduler integration, and a 1,490-test offline suite — was defined and executed independently. Each layer (scanner, scorer, logger, report compiler) is independently testable with no circular dependencies between modules.

The design reflects a deliberate choice to build measurement infrastructure before claiming results. The calibration script (`analysis/calibration.py`) computes Brier scores and win rates broken down by flag path, time horizon, confidence tier, and cross-market alignment. The backlog is explicitly structured around data conditions: several planned features are blocked until the resolved-signal count clears n=20, because prior to that threshold any accuracy metric is too noisy to act on. This is an easy discipline to skip when you're the only one checking.

The smart money system illustrates the same approach. Twenty trader addresses were pre-curated by monthly PnL from Polymarket's public leaderboard, but each is filtered at runtime against hard requirements — ≥10 resolved positions and ≥55% win rate — before any signal weight is applied. As of now, none of them clear the bar. That's the correct output, not a bug: the system is working as designed.

---

## Repository Layout

Every folder in the repo has one job. `main.py` is the only entry-point script left at the root — everything else lives in the folder that owns it:

| Folder | Purpose |
|---|---|
| `core/` | The pipeline engine — auth, scanning, scoring, logging, reporting. Everything `main.py` orchestrates lives here. |
| `sources/` | External market API clients — Polymarket, Manifold/PredictIt/Metaculus/OddsAPI, and winning-wallet discovery. |
| `analysis/` | Read-only diagnostic and calibration scripts that run against `data/leviathan.db`. Nothing here is part of the daily pipeline. |
| `backtesting/` | Offline, CSV-based backtest harness (including walk-forward validation) and the empirical base-rate scaffold. Doesn't touch the live DB. |
| `backlog/` | The backlog engine (`engine.py`) and weekly gate checker (`checker.py`) that maintain `backlog/backlog.json` and regenerate `BACKLOG.md`. |
| `mcp_server/` | MCP server exposing the signal log, resolved track record, and market-data lookup as tools — reads `data/leviathan.db` directly, live. |
| `scripts/` | Scheduled/maintenance entry points — daily smart-money scan, position reconciliation, PnL verification, Task Scheduler registration. |
| `tests/` | The full offline test suite (1,617 tests) plus `conftest.py`, which puts the repo root on `sys.path` for every test. |
| `data/` | All runtime state: the live `leviathan.db`, its old backups (`data/db_backups/`), PowerBI exports, market snapshots, smart-money/whale caches, and the dashboard `.pbix`. |
| `docs/` | Progress log (`PROGRESS.md`) and a human-triaged, append-only parking lot for premature/declined ideas (`IDEAS.md`) — never read by an agent for direction. |
| `reports/` | Saved output from one-off analysis runs (threshold sweeps, flag-mode comparisons). |

---

## Architecture

The codebase is structured as a modular pipeline — each layer is independently testable and has no circular dependencies.

| File | Purpose |
|---|---|
| `main.py` | Orchestrator — 8-step pipeline |
| `core/kalshi.py` | Kalshi REST API client (RSA-PSS auth) |
| `core/scanner.py` | Market filter, edge scoring, drift detection, watchlist tagging |
| `core/whales.py` | Large trade detection |
| `core/scorer.py` | Batched market scoring — local Claude CLI (default) or Anthropic Messages API via `core/llm.py` |
| `core/llm.py` | Anthropic Messages API client — forced tool_choice structured output, server-side web search, prompt caching |
| `core/logger.py` | SQLite persistence — signals, runs, fills, probes |
| `core/report.py` | Report compiler and email sender |
| `core/subscribers.py` | Newsletter subscriber management |
| `core/export_to_csv.py` | Exports `data/leviathan.db` tables to `data/powerbi_export/` |
| `core/fees.py` | Kalshi fee schedule and net-of-fee edge math |
| `sources/polymarket.py` | Polymarket Gamma API price cross-reference |
| `sources/external_markets.py` | Manifold + PredictIt + Metaculus + OddsAPI aggregator |
| `sources/metaculus.py` | Metaculus question search and probability fetch |
| `sources/odds_api.py` | The Odds API bookmaker lines |
| `sources/accounts.py` | Winning Polymarket wallet discovery and per-market scan |
| `config.json` | All thresholds, model settings, watchlist |
| `analysis/smart_money_scan.py` | Watchlist position fetch and Kalshi cross-reference |
| `analysis/research_probe.py` | Stratified Claude+websearch probability probe |
| `analysis/snapshot_markets.py` | Full market catalog snapshot (used by analysis scripts) |
| `analysis/filter_stats.py` | Pipeline diagnostic — drop reasons and flag breakdown |
| `analysis/track_record.py` | Historical P&L from logged signals |
| `analysis/calibration.py` | Calibration analysis — win rate by flag_path, horizon, alignment, net_edge, Brier score |
| `analysis/net_edge_analysis.py` | Net-of-spread edge distribution for flagged markets |
| `analysis/pass_analysis.py` | Scanner precision — PASS rate by flag_path, horizon, repeat false-positives |
| `analysis/eval.py` | Eval harness entry point — three-way Brier comparison (scorer/market/constant), calibration by decile |
| `analysis/eval_grader.py` | Deterministic grader — Brier score, hit rate, calibration by decile. No model in the loop. |
| `analysis/eval_dataset.py` | Freezes the resolved track record (via the MCP server's tool) as a versioned eval dataset |
| `analysis/eval_rescore.py` | Separate re-score harness proving the scoring pipeline is reproducible — not part of the default eval run |
| `backtesting/harness.py` | CSV-based backtest harness, including rolling walk-forward validation |
| `backtesting/base_rates.py` | Empirical base-rate scaffold (fed by the backtest harness) |
| `backlog/engine.py` | Backlog CLI — status summary, validated item add |
| `backlog/checker.py` | Weekly gate checker — evaluates locked-item triggers against live DB metrics |
| `scripts/daily_smart_money.py` | Scheduled daily watchlist scan runner |
| `scripts/setup_scheduler.ps1` | Registers daily Task Scheduler jobs |
| `scripts/daily_resolve_first.py` | Scheduled daily near-dated-market selector — accelerates n toward the n=20 gate |
| `scripts/setup_resolve_first_scheduler.ps1` | Registers the daily resolve-first Task Scheduler job |

---

## Data Sources

| Source | What it adds |
|---|---|
| Kalshi | Primary market — prices, order book, trade history |
| Polymarket | On-chain price cross-reference + smart money position tracking |
| Manifold | Community forecaster prices |
| PredictIt | Regulated US political market prices |
| Metaculus | Superforecaster consensus (requires free token) |
| The Odds API | Sharp bookmaker lines for sports markets (requires free key) |

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | Where to get it |
|---|---|
| `KALSHI_KEY_ID` | kalshi.com → Settings → API |
| `KALSHI_PRIVATE_KEY` | Same — RSA private key (PEM format) |
| `GMAIL_APP_PASSWORD` | Google account → Security → App Passwords |
| `ODDS_API_KEY` | [the-odds-api.com](https://the-odds-api.com) (free tier: 500 req/month) |
| `METACULUS_API_TOKEN` | [metaculus.com/api](https://www.metaculus.com/api/) (free) |

### 3. Configure settings

```bash
cp config.example.json config.json
```

Edit `config.json` (git-ignored — never committed). Key sections:

| Section | What to set |
|---|---|
| `environment` | `"prod"` or `"demo"` |
| `markets` | Volume floors, price bounds, close-time window, flag mode |
| `scoring` | Max markets per run, Claude model label, probe settings |
| `accounts.watchlist` | Pre-curated Polymarket trader addresses and monthly PnL |
| `report` | Email address, SMTP settings |

### 4. Run

```bash
python main.py
```

### 5. Schedule daily runs (Windows)

Run once as Administrator:

```powershell
.\scripts\schedule_setup.ps1
```

Registers a Task Scheduler job that fires every day at 7:00 AM. Two more scheduled jobs run separately: the smart money watchlist scan (`scripts/setup_scheduler.ps1`, 8:07 AM) and the resolve-first near-dated selector (`scripts/setup_resolve_first_scheduler.ps1`, 8:30 AM — after both prior jobs have refreshed the market snapshot).

---

## Analysis Scripts

| Script | What it does | Run |
|---|---|---|
| `analysis/filter_stats.py` | Shows pipeline stages, drop reasons, flag-path breakdown, top-10 flagged markets, watchlist overlap | `python analysis/filter_stats.py` |
| `analysis/research_probe.py` | Stratified Claude+websearch probe experiment | `python analysis/research_probe.py` |
| `analysis/threshold_sweep.py` | Grid search over edge/price/volume thresholds | `python analysis/threshold_sweep.py` |
| `analysis/flag_mode_compare.py` | Compares passthrough vs strict vs strict_with_heuristic | `python analysis/flag_mode_compare.py` |
| `analysis/drift_diagnosis.py` | Diagnoses drift signal fire rate by price bucket | `python analysis/drift_diagnosis.py` |
| `analysis/track_record.py` | Hypothetical P&L summary from logged signals | `python analysis/track_record.py` |
| `backtesting/harness.py` | CSV-based backtest + rolling walk-forward validation | `python backtesting/harness.py --signals ... --resolutions ... --output ... [--walk-forward]` |
| `analysis/calibration.py` | Win rate + Brier score by flag_path, confidence, horizon, alignment, net_edge | `python analysis/calibration.py` |
| `analysis/net_edge_analysis.py` | Net-of-spread edge distribution — shows what % of flagged markets are actually tradeable | `python analysis/net_edge_analysis.py` |
| `analysis/pass_analysis.py` | Scanner precision — PASS rate by flag path, time horizon, and repeat false-positive tickers | `python analysis/pass_analysis.py` |
| `analysis/snapshot_markets.py` | Fetches and saves full Kalshi market catalog snapshot | `python analysis/snapshot_markets.py` |
| `analysis/eval.py` | Eval harness — three-way Brier comparison (scorer/market/constant), calibration by decile, free/instant | `python analysis/eval.py` |
| `analysis/eval_rescore.py` | Separate re-score reproducibility check — costs real API/CLI usage, not part of the default eval run | `python analysis/eval_rescore.py --check` |
| `scripts/daily_smart_money.py` | Runs watchlist scan, saves report, commits and pushes | Scheduled via Task Scheduler |
| `scripts/daily_resolve_first.py` | Selects near-dated (≤14d), two-sided-book markets spread across price bands and logs them so they resolve fast | Scheduled via Task Scheduler |

---

## MCP Server

`mcp_server/server.py` exposes the signal log as MCP tools so the resolved track record can be interrogated conversationally instead of by opening files or writing one-off queries. v1 is stdio transport, tools only — reads `data/leviathan.db` directly (the same database the pipeline writes), never a copy or snapshot.

| Tool | What it does |
|---|---|
| `get_signal_log` | Most recent scored paper signals (PASS excluded), newest first. Optional `limit`, `resolved_only`, `ticker` filters. |
| `get_resolved_track_record` | The full resolved track record — every settled signal with its probability estimate and actual outcome. Same filter as the README's headline stats. |
| `lookup_market` | Scored market data for a given `ticker` (partial match) or signal `date` (`YYYY-MM-DD`). |

### Try it in the Inspector

```bash
pip install uv    # one-time — mcp dev shells out to uv run under the hood
mcp dev mcp_server/server.py
```

### Wire it into Claude Code

```bash
claude mcp add leviathan -- python mcp_server/server.py
```

Then ask things like *"how did my highest-scored markets resolve?"* in a new Claude Code session in this repo.

---

## Eval Harness

`analysis/eval.py` grades the scorer's original at-signal-time estimates against resolved ground truth — a deterministic, no-model-in-the-loop instrument for "is the edge real," not an impression.

### Brier reconciliation

The 0.0578 Brier score above looked suspiciously low at first glance: one row alone (a Cabinet-departure market, estimate 0.65, resolved NO) contributes 0.65² = 0.4225 in isolation. Reconciled by hand across all 8 resolved rows: **it's correct.** `core/logger.py get_brier_score()` computes `(our_estimate − actual outcome)²` averaged over the same 8-row resolved set used everywhere else in this README (verified algebraically and numerically identical to computing directly against the `outcome` YES/NO field). All 8 of these markets happened to resolve NO; seven of the eight estimates were already appropriately low, so their squared errors are near zero — diluting the one large miss down to a 0.058 average. `analysis/eval_grader.py`'s calibration-by-decile output makes this visible directly: the single market in the 0.6–0.7 estimate bucket has a 0% actual rate, i.e. the worst-calibrated bucket is the highest-conviction one.

### Three-way comparison (n=8, 2026-07-14)

`python analysis/eval.py` grades three things on the same frozen dataset — the scorer, the market price at signal time, and a constant 0.5 — because a Brier score alone means nothing without a benchmark:

| Baseline | Brier | Hit rate |
|---|---|---|
| Scorer | 0.0578 | 88% |
| Market price | 0.0022 | 100% |
| Constant 0.5 | 0.2500 | 0% |

**The market price beats the scorer on this sample.** All 8 markets were already priced low (0.5–10.5%) and correctly resolved NO — the market was already efficient here, and the scorer's one high-conviction contrarian call (0.65 vs a 10.5% market price) was wrong. At n=8 this is not evidence the scorer is worthless — it's a flag to watch as n grows toward the 20-signal calibration gate, not a conclusion.

### Dataset and re-score harness

- `analysis/eval_dataset.py` freezes the resolved track record through the MCP server's `get_resolved_track_record` tool (no second query path) into a versioned `analysis/eval_data/resolved_<date>.json`.
- `analysis/eval_rescore.py` is a **separate, explicitly-invoked** harness that re-runs the current scoring prompt against the frozen markets to prove the scoring *pipeline* is reproducible — useful for comparing prompt version A vs. B on markets that haven't resolved yet. It is not part of the default `eval.py` run and is not free: `backend="api"` costs real Anthropic API usage; `backend="cli"` (the config default) runs a real Claude CLI subprocess. Re-scoring *already-resolved* markets is contaminated by live web search surfacing the actual outcome — re-scored estimates are never used as forecasts anywhere in this repo; `eval.py`'s comparison above uses the original at-signal-time estimates only.

```bash
python analysis/eval.py                    # three-way comparison, default, free, instant
python analysis/eval_rescore.py --check    # separate: proves re-score reproducibility (costs real API/CLI usage)
```

**Live determinism check (2026-07-14, `backend="cli"`, the current config default):** re-scored the 8 frozen markets twice — **not identical**, as caveated above, since the CLI backend has no temperature control. Estimates shifted by a few points on most markets (e.g. 0.06 → 0.05), but one market swung sharply: the Cabinet-departure signal (original at-signal-time estimate 0.65, resolved NO) came back at 0.92 and 0.97 on re-score — *more* confidently wrong than the original call, not less. This is exactly the contamination risk documented above (re-scoring an already-resolved, publicly-known market) compounded by `backend="cli"`'s lack of reproducibility guarantees; it is not evidence about the scorer's quality and isn't used as one. Proving true reproducibility requires `backend="api"` with `temperature=0` (supported in code — `core/llm.py score_via_api(..., temperature=...)` — but untested live here due to an invalid `ANTHROPIC_API_KEY` in this environment).

---

## Managing Subscribers

```bash
python core/subscribers.py add someone@example.com
python core/subscribers.py list
python core/subscribers.py remove <token>
```

Each subscriber receives the report with a unique unsubscribe token in the footer.

---

## Testing

```bash
python -m pytest -q
```

1617 tests, all offline — no network calls, no Claude CLI invocations. SQLite tests use a throwaway `tmp_path` DB; `logger.DB_PATH` is monkeypatched before each test.

| Test file | What it covers |
|---|---|
| `tests/test_logger.py` | Payoff math, schema migration, fill matching, stats separation, get_stats_by_sig, log_run |
| `tests/test_scanner.py` | Filter gates, flag modes, drift thresholds, watchlist tagging, heuristic base rates |
| `tests/test_whales.py` | Whale detection logic, scan_all_markets |
| `tests/test_scorer.py` | build_prompt() signals, flag reasons, calibration rules, cross-market/poly/whale/OB/spread |
| `tests/test_report.py` | Signal block, _qualifying, compile_report, compile_weekly_digest, flag path labels |
| `tests/test_research_probe.py` | Stratified sampling, probe logging, forward scoring |
| `tests/test_smart_money.py` | Binary position filter, sports title filter, keyword gate, match scoring |
| `tests/test_polymarket.py` | _yes_price, build_index, find_match, match_markets, fetch_and_build_index, cross-market promotion |

---

## Notes

- **Read-only in v1** — no order placement, amendment, or cancellation. Only GET endpoints are called.
- **Scoring backend is configurable** (`llm.backend` in `config.json`) — `cli` (default) runs the local Claude CLI with `ANTHROPIC_API_KEY` stripped from its environment, using your Pro OAuth session with no per-token billing. `api` calls the Anthropic Messages API directly via `core/llm.py` — forced tool_choice structured output, server-side web search, and prompt caching, at real per-token cost.
- **`data/leviathan.db`** stores all signals, runs, fills, and probe rows locally. Not committed to git.
- **Win rate and P&L** are hypothetical — no real money is traded by the system. Real fills from your own Kalshi account can be pulled in via `logger.pull_real_fills()`.
- **Smart money cache** (`data/smart_money/latest_signals.json`) is committed to git so the watchlist boost persists across machines without re-running the scan.
