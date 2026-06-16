# Leviathan

Prediction market intelligence bot for Kalshi. Scans thousands of open markets daily, cross-references prices across multiple platforms, detects whale activity, tracks winning traders, and emails a signal report.

## What it does

- Scans 2,000+ Kalshi markets per run, filters for mispricing candidates
- Cross-references against Polymarket, Manifold, PredictIt, Metaculus, and The Odds API
- Detects large whale trades and order book imbalances
- Tracks winning Polymarket wallets and reports their positioning
- Scores flagged markets using Claude (via your Pro subscription — no API billing)
- Emails a daily plain-text signal report; supports multiple subscribers
- Auto-resolves past calls when markets settle and tracks win rate over time
- Runs daily via Windows Task Scheduler

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
| `ODDS_API_KEY` | [the-odds-api.com](https://the-odds-api.com) (free, 500 req/month) |
| `METACULUS_API_TOKEN` | [metaculus.com/api](https://www.metaculus.com/api/) (free) |

### 3. Configure settings

Edit `config.json` to set:
- `environment`: `"prod"` or `"demo"`
- `report.email_to`: your email address
- Thresholds, volume floors, scoring parameters

### 4. Run

```bash
python main.py
```

### 5. Schedule daily runs (Windows)

Run once as Administrator:

```powershell
.\scripts\schedule_setup.ps1
```

Registers a Task Scheduler job that fires every day at 7:00 AM.

## Managing subscribers

```bash
python subscribers.py add someone@example.com
python subscribers.py list
python subscribers.py remove <token>
```

Each subscriber receives the report with a unique unsubscribe token in the footer.

## File map

| File | Purpose |
|---|---|
| `main.py` | Orchestrator — runs the full 8-step pipeline |
| `kalshi.py` | Kalshi REST API client (RSA-PSS auth) |
| `scanner.py` | Market filtering, edge scoring, signal detection |
| `whales.py` | Large trade detection |
| `scorer.py` | Claude CLI subprocess — scores markets with web search |
| `polymarket.py` | Polymarket price cross-reference |
| `external_markets.py` | Manifold + PredictIt + Metaculus + OddsAPI aggregator |
| `metaculus.py` | Metaculus question search |
| `odds_api.py` | The Odds API bookmaker lines |
| `accounts.py` | Winning Polymarket wallet discovery and tracking |
| `logger.py` | SQLite persistence (signals + run history) |
| `report.py` | Plain-text report compiler and email sender |
| `subscribers.py` | Newsletter subscriber management |
| `scripts/schedule_setup.ps1` | Windows Task Scheduler registration script |
| `config.json` | All thresholds and settings |

## Data sources

| Source | What it adds |
|---|---|
| Kalshi | Primary market — prices, order book, trade history |
| Polymarket | On-chain price cross-reference + smart money tracking |
| Manifold | Community forecaster prices |
| PredictIt | Regulated US political market prices |
| Metaculus | Superforecaster consensus (requires free token) |
| The Odds API | Sharp bookmaker lines for sports markets (requires free key) |

## Notes

- Read-only in v1 — no trade execution
- Scoring runs through the local `claude` CLI using your Pro subscription (no Anthropic API credits consumed)
- `leviathan.db` stores all signals and run history locally; not committed to git
- Win rate and P&L tracking are hypothetical — no real money is traded
