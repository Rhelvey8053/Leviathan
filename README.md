# Leviathan

Prediction market intelligence system for Kalshi. Scans thousands of open markets daily, cross-references prices across five platforms, tracks top Polymarket traders, detects whale activity, and emails a plain-text signal report.

**Status:** v1 — read-only, no trade execution. All signals are informational.

---

## What it does

| Layer | What happens |
|---|---|
| **Filter** | Scans 2,400+ Kalshi markets, drops efficiently-priced and low-liquidity markets |
| **Smart money** | Tracks 20 top Polymarket traders by monthly PnL; cross-references their open positions to Kalshi markets by title similarity |
| **Whale detection** | Flags unusually large trades and order-book imbalances |
| **Cross-reference** | Prices the same question on Polymarket, Manifold, Metaculus, PredictIt, and The Odds API |
| **Score** | Sends flagged markets to Claude (via Pro CLI — no API billing) with web search; 11 calibration rules anchor estimates to base rates and cross-market evidence |
| **Report** | Emails a plain-text daily report; tracks win rate and P&L over time |
| **Research probe** | Stratified-sample experiment: Claude+websearch estimates probability on 10 markets per run, including markets the main filter rejects, to test for edge outside the funnel |

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

Edit `config.json`. Key sections:

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

Registers a Task Scheduler job that fires every day at 7:00 AM. A separate job for the smart money watchlist scan can be registered from `scripts/setup_scheduler.ps1`.

---

## Pipeline — 8 steps

```
[1] Authenticate Kalshi + resolve any settled markets
[2] Fetch 2,400+ markets across 400 events
[3] Filter → deduplicate by event → tag smart money overlap → score/flag
[4] Cross-reference: Polymarket, Manifold, Metaculus, PredictIt, OddsAPI
[5] Whale detection, order-book depth, price trend history
[6] Claude CLI scores flagged markets (one batched subprocess call)
[7a] Log signals to leviathan.db; send weekly digest on Sundays
[7b] Smart money watchlist scan (positions → Kalshi cross-reference → cache)
[8] Compile and email report
```

**Watchlist markets** (confirmed smart money positions) bypass the normal flag requirement — they reach Claude scoring even if no drift or heuristic edge fired, with a `WATCHLIST` flag path.

---

## Smart money system

Two parallel systems track winning traders:

**Watchlist scan** (`analysis/smart_money_scan.py`) — runs at step 7b and daily via Task Scheduler. Fetches open positions for 20 pre-curated top Polymarket traders (by monthly PnL). Cross-references position titles to Kalshi market titles using Jaccard word-overlap + SequenceMatcher scoring with a 0.50 threshold and a minimum 2-keyword overlap gate. Sports bets and game markets are filtered out. Results are written to `data/smart_money/YYYY-MM-DD.md` (markdown) and `data/smart_money/latest_signals.json` (ticker cache). The ticker cache is read at step 3 to boost those markets to the top of the scoring queue.

**Account discovery** (`accounts.py`) — discovers winning wallets from recent Polymarket trade history, enriches them with win rate and active positions, and checks whether any of those wallets traded on the specific Polymarket condition that matches a flagged Kalshi market. Results appear as "Smart Money Activity" in the per-signal section of the report.

---

## Research probe

`analysis/research_probe.py` — a standalone experiment that runs separately from the main pipeline.

```bash
python analysis/research_probe.py
```

Samples ~50 markets across 5 volume tiers (including markets the main filter rejects), then probes up to `max_probe_markets` (config: `scoring.max_probe_markets`, default 10) with Claude+websearch. Smart money watchlist tickers are always probed first. Results are logged as `source='research_probe'` and resolve automatically when markets settle. `logger.get_stats_probe()` reports the hit rate once outcomes are known.

---

## Analysis scripts

| Script | What it does | Run |
|---|---|---|
| `analysis/filter_stats.py` | Shows pipeline stages, drop reasons, flag-path breakdown, top-10 flagged markets, watchlist overlap | `python analysis/filter_stats.py` |
| `analysis/research_probe.py` | Stratified Claude+websearch probe experiment | `python analysis/research_probe.py` |
| `analysis/threshold_sweep.py` | Grid search over edge/price/volume thresholds | `python analysis/threshold_sweep.py` |
| `analysis/flag_mode_compare.py` | Compares passthrough vs strict vs strict_with_heuristic | `python analysis/flag_mode_compare.py` |
| `analysis/drift_diagnosis.py` | Diagnoses drift signal fire rate by price bucket | `python analysis/drift_diagnosis.py` |
| `analysis/backtest.py` | Hypothetical P&L summary from logged signals | `python analysis/backtest.py` |
| `analysis/snapshot_markets.py` | Fetches and saves full Kalshi market catalog snapshot | `python analysis/snapshot_markets.py` |
| `scripts/daily_smart_money.py` | Runs watchlist scan, saves report, commits and pushes | Scheduled via Task Scheduler |

---

## File map

| File | Purpose |
|---|---|
| `main.py` | Orchestrator — 8-step pipeline |
| `kalshi.py` | Kalshi REST API client (RSA-PSS auth) |
| `scanner.py` | Market filter, edge scoring, drift detection, watchlist tagging |
| `whales.py` | Large trade detection |
| `scorer.py` | Claude CLI subprocess — batched market scoring with web search |
| `polymarket.py` | Polymarket Gamma API price cross-reference |
| `external_markets.py` | Manifold + PredictIt + Metaculus + OddsAPI aggregator |
| `metaculus.py` | Metaculus question search and probability fetch |
| `odds_api.py` | The Odds API bookmaker lines |
| `accounts.py` | Winning Polymarket wallet discovery and per-market scan |
| `logger.py` | SQLite persistence — signals, runs, fills, probes |
| `report.py` | Report compiler and email sender |
| `subscribers.py` | Newsletter subscriber management |
| `config.json` | All thresholds, model settings, watchlist |
| `analysis/smart_money_scan.py` | Watchlist position fetch and Kalshi cross-reference |
| `analysis/research_probe.py` | Stratified Claude+websearch probability probe |
| `analysis/snapshot_markets.py` | Full market catalog snapshot (used by analysis scripts) |
| `analysis/filter_stats.py` | Pipeline diagnostic — drop reasons and flag breakdown |
| `analysis/backtest.py` | Historical P&L from logged signals |
| `scripts/daily_smart_money.py` | Scheduled daily watchlist scan runner |
| `scripts/setup_scheduler.ps1` | Registers daily Task Scheduler jobs |

---

## Data sources

| Source | What it adds |
|---|---|
| Kalshi | Primary market — prices, order book, trade history |
| Polymarket | On-chain price cross-reference + smart money position tracking |
| Manifold | Community forecaster prices |
| PredictIt | Regulated US political market prices |
| Metaculus | Superforecaster consensus (requires free token) |
| The Odds API | Sharp bookmaker lines for sports markets (requires free key) |

---

## Managing subscribers

```bash
python subscribers.py add someone@example.com
python subscribers.py list
python subscribers.py remove <token>
```

Each subscriber receives the report with a unique unsubscribe token in the footer.

---

## Testing

```bash
python -m pytest -q
```

836 tests, all offline — no network calls, no Claude CLI invocations. SQLite tests use a throwaway `tmp_path` DB; `logger.DB_PATH` is monkeypatched before each test.

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
- **Scoring via Claude Pro** — the CLI subprocess strips `ANTHROPIC_API_KEY` so it uses your Pro OAuth session. No per-token API billing.
- **`leviathan.db`** stores all signals, runs, fills, and probe rows locally. Not committed to git.
- **Win rate and P&L** are hypothetical — no real money is traded by the system. Real fills from your own Kalshi account can be pulled in via `logger.pull_real_fills()`.
- **Smart money cache** (`data/smart_money/latest_signals.json`) is committed to git so the watchlist boost persists across machines without re-running the scan.
