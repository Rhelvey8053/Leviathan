# Leviathan v1 — Claude Code Build Spec

## Project Overview

Leviathan is a Kalshi prediction market intelligence bot. It scans open
markets for mispricings, detects unusual large-trade activity as a proxy
for whale positioning, estimates true probabilities using Claude + web
search, and emails a daily signal report.

**v1 is read-only.** No trade execution. The goal is to validate edge
before touching real capital.

**Runtime:** Python script, run manually from terminal or scheduled via cron.
**Output:** Email report to your@email.com
**Environment:** Start in Kalshi demo, switch to prod once edge is validated.

---

## File Structure

```
leviathan/
├── main.py          # orchestrator
├── kalshi.py        # Kalshi API client (all API interactions)
├── scanner.py       # mispricing detection
├── whales.py        # large trade / unusual volume detection
├── scorer.py        # probability estimation via Claude + web search
├── logger.py        # call logging + KPI tracking
├── report.py        # compile + email daily report
├── config.json      # user settings
├── calls.csv        # auto-generated signal log (gitignored)
├── requirements.txt
└── .env.example
```

---

## Module Specs

### main.py — Orchestrator

```
Pipeline order:
1. Load config + authenticate Kalshi
2. kalshi.py — fetch all open markets
3. scanner.py — score markets for mispricing vs estimated probability
4. whales.py — detect large trade activity across flagged markets
5. scorer.py — run Claude + web search on top flagged markets
6. logger.py — log all signals with metadata
7. report.py — compile and email report
```

Print a status line per step. Catch errors per step so one failure
doesn't kill the whole run.

---

### kalshi.py — Kalshi API Client

All Kalshi API interactions live here. Nothing else touches the API directly.

**Base URL:** `https://demo-api.kalshi.co/trade-api/v2` (demo)
Switch to `https://trading-api.kalshi.co/trade-api/v2` for prod via config.

**Authentication:** API key in header: `Authorization: Bearer {KALSHI_API_KEY}`

**Functions to implement:**

```python
authenticate() -> dict
# Validates API key works. Returns account info.
# Fails loudly if key is invalid — don't proceed with bad auth.

fetch_markets(status: str = "open", limit: int = 200) -> list[dict]
# Returns open markets with: ticker, title, yes_bid, yes_ask,
# no_bid, no_ask, volume, close_time, category

fetch_market(ticker: str) -> dict
# Returns single market with full detail including order book

fetch_trades(ticker: str, limit: int = 100) -> list[dict]
# Returns recent trades for a market:
# {price, count, size, created_time, taker_side}
# This is the data source for whale detection

fetch_market_history(ticker: str, period_seconds: int = 86400) -> list[dict]
# Returns price history for the last N seconds
```

---

### scanner.py — Mispricing Detection

Compares current market price against a base rate estimate.
Flags markets where the gap is wide enough to be interesting.

**Key insight from real-money testing:** Highly-watched economic releases
(CPI, Fed rate decisions, jobs reports) are already efficiently priced —
even high-confidence AI calls lose there. The edge lives in mid-tier volume
markets where fewer sophisticated traders are paying attention.

**Step 1 — Filter before scoring:**

```python
filter_markets(markets: list[dict]) -> list[dict]
# Remove markets that are likely already efficient before any scoring.
# A market gets filtered OUT if ANY of the following are true:
#
# 1. Volume too high: volume > config.markets.max_volume_filter (default: 50000)
#    — heavily traded markets are efficiently priced by sophisticated money
#
# 2. Volume too low: volume < config.markets.min_volume (default: 1000)
#    — illiquid markets are hard to enter/exit, avoid
#
# 3. Efficient market keywords in title (case-insensitive match):
#    ["CPI", "Federal Reserve", "Fed rate", "nonfarm payroll", "jobs report",
#     "GDP", "inflation rate", "FOMC", "unemployment rate"]
#    — these are the most-watched releases, priced efficiently within minutes
#
# 4. Closing too soon: close_time < now + 24 hours
#    — not enough time for edge to play out
#
# 5. Closing too far out: close_time > now + 90 days
#    — too much uncertainty, signal degrades
#
# Returns filtered list — only markets worth scoring
```

**Step 2 — Score filtered markets:**

```python
score_market(market: dict) -> dict
# Inputs: market dict (already filtered)
# 
# 1. Extract yes_ask price (cost to buy YES = implied probability)
# 2. Calculate mid price: (yes_bid + yes_ask) / 2
# 3. Run base rate check (see below)
# 4. Calculate raw_edge = abs(base_rate_estimate - mid_price)
# 5. Return {ticker, title, mid_price, base_rate, raw_edge, flag}
# flag = True if raw_edge > config.edge_threshold

estimate_base_rate(market: dict) -> float
# Simple heuristic pass before calling Claude (saves tokens)
# Look at market title keywords:
# - "Will X happen by [date]" — check if similar events happened historically
# - If title contains known base rate signals, return float
# - Otherwise return None (scorer.py handles it with Claude)
```

**Edge threshold:** Configurable in config.json. Start at 0.08 (8 percentage
points between market price and our estimate). Only markets above this
get passed to scorer.py.

**Sweet spot:** Markets with volume between 1,000–50,000 and closing in
2–30 days. Thin enough that not everyone is watching, liquid enough to
enter a position if v2 adds execution.

---

### whales.py — Large Trade Detection

Kalshi doesn't expose individual account histories like Polymarket on-chain.
Whale tracking here is trade-size based — detect unusually large single
trades or volume spikes as a proxy for informed money moving.

```python
detect_whale_activity(ticker: str, trades: list[dict]) -> dict
# Inputs: ticker + recent trades from kalshi.py fetch_trades()
#
# 1. Calculate avg trade size for this market
# 2. Flag individual trades > config.whale_size_multiplier * avg (default: 5x)
# 3. Detect volume spikes: last hour volume vs prev 23hr avg
# 4. Note direction: were the big trades buying YES or NO?
#
# Returns:
# {
#   ticker,
#   whale_detected: bool,
#   large_trades: list of trades above threshold,
#   volume_spike: bool,
#   whale_direction: "YES" | "NO" | None,
#   avg_trade_size: float,
#   max_trade_size: float
# }

scan_all_markets(tickers: list[str]) -> list[dict]
# Runs detect_whale_activity across all flagged tickers
# Returns only markets where whale_detected = True
```

---

### scorer.py — Probability Estimation

The intelligence layer. Takes markets flagged by scanner.py and whale.py,
calls Claude with web search to estimate true probability.

**Single batched call per run** — don't call Claude once per market.
Group flagged markets and score them together.

```python
score_markets(flagged_markets: list[dict]) -> list[dict]
# Calls Anthropic API with web_search tool enabled
#
# System prompt:
# "You are a prediction market analyst. For each market provided,
#  estimate the true probability of the YES outcome occurring.
#  Use web search to find relevant recent information.
#  Return only valid JSON. Be calibrated, not confident."
#
# User prompt includes:
# - Market title
# - Current yes price (market implied probability)
# - Close date
# - Whale activity if detected
# - Any base rate context from scanner.py
#
# Returns JSON array:
# [{
#   ticker,
#   market_price,
#   our_estimate,
#   edge,
#   direction: "YES" | "NO" | "PASS",
#   confidence: "HIGH" | "MED" | "LOW",
#   reasoning: "2-3 sentences max",
#   sources_checked: ["headline or url"]
# }]

build_prompt(markets: list[dict]) -> str
# Assembles the user prompt cleanly
# Includes whale direction as a signal modifier:
# "Note: large trades detected buying YES in last 6 hours"
```

**Model:** `claude-sonnet-4-6` for scorer only — this is the decision layer,
quality matters more than cost here. Use Haiku for everything else.

---

### logger.py — Signal Logger

Logs every signal Leviathan generates to calls.csv.
This becomes the track record — proof of edge over time.

**Schema (calls.csv):**

```
call_id        | auto-increment
timestamp      | ISO 8601
ticker         | Kalshi market ticker
title          | market title
market_price   | implied probability at time of call
our_estimate   | Leviathan's probability estimate
edge           | our_estimate - market_price
direction      | YES / NO / PASS
confidence     | HIGH / MED / LOW
whale_detected | bool
whale_direction| YES / NO / null
outcome        | null until market resolves
result         | WIN / LOSS / null until resolved
pnl_if_traded  | hypothetical P&L at $5/contract — null until resolved
run_id         | links to run metadata
```

**Functions:**

```python
log_signal(signal: dict) -> None
# Appends to calls.csv

log_run(run_data: dict) -> None
# Appends to runs.csv:
# {run_id, timestamp, markets_scanned, signals_generated,
#  whale_flags, model_used, tokens_used, cost_usd, runtime_ms}

get_stats() -> dict
# Returns {total_calls, win_rate, avg_edge_captured,
#          total_hypothetical_pnl, best_call, worst_call}
# win_rate only calculated on resolved markets (outcome != null)
```

**Note:** outcomes are null until markets resolve. Leviathan does not
auto-update outcomes in v1 — that's a v2 feature. For now, Reed
manually updates the outcome column as markets close.

---

### report.py — Daily Email Report

Compiles all signals into a clean email. Sent to your@email.com
via Gmail SMTP or SendGrid (use SMTP with Gmail app password — simpler
than OAuth for outbound only).

**Email format:**

```
Subject: Leviathan Report — [date] | [n] signals | [n] whale flags

SIGNALS TODAY
  [For each signal above edge threshold, sorted by edge descending]

  🟢 HIGH CONFIDENCE
  [ticker] — [title]
  Market: [price]%  |  Our estimate: [estimate]%  |  Edge: [edge]%
  Direction: BUY YES / BUY NO
  Whale activity: [Yes — buying YES / No]
  Reasoning: [2-3 sentence summary]

  🟡 MEDIUM CONFIDENCE
  [same format]

WHALE ACTIVITY (no signal)
  Markets with large trade activity that didn't meet edge threshold:
  [ticker] — [title] — [direction] — [size vs avg]

TRACK RECORD
  Total calls: [n]    Resolved: [n]    Win rate: [n]%
  Avg edge captured: [n]%
  Hypothetical P&L ($10/contract): $[n]

RUN STATS
  Markets scanned: [n]    Signals generated: [n]
  Model: claude-sonnet-4-6    Cost: $[cost]    Runtime: [n]s
```

---

## config.json

```json
{
  "environment": "demo",

  "markets": {
    "max_fetch": 200,
    "edge_threshold": 0.08,
    "min_volume": 1000,
    "max_volume_filter": 50000,
    "min_days_to_close": 1,
    "max_days_to_close": 90,
    "efficient_market_keywords": [
      "CPI", "Federal Reserve", "Fed rate", "nonfarm payroll",
      "jobs report", "GDP", "inflation rate", "FOMC", "unemployment rate"
    ],
    "categories": []
  },

  "whales": {
    "size_multiplier": 5,
    "volume_spike_hours": 1,
    "lookback_trades": 100
  },

  "scoring": {
    "scorer_model": "claude-sonnet-4-6",
    "support_model": "claude-haiku-4-5-20251001",
    "max_markets_per_run": 20,
    "confidence_threshold": "MED"
  },

  "report": {
    "email_to": "your@email.com",
    "email_from": "",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587
  }
}
```

**config notes:**
- `categories`: filter to specific market categories e.g. `["Politics", "Economics"]`. Empty = all categories.
- `min_volume`: ignore illiquid markets below this total volume
- `max_markets_per_run`: cap how many markets get scored by Claude to control cost
- `confidence_threshold`: only email signals at or above this level

---

## Environment Variables (.env)

```
KALSHI_API_KEY=your-kalshi-api-key
ANTHROPIC_API_KEY=sk-ant-...
GMAIL_APP_PASSWORD=your-16-char-app-password
```

Gmail app password: myaccount.google.com → Security → 2-Step Verification
→ App passwords. Required for SMTP send from script.

---

## Requirements.txt

```
anthropic
requests
python-dotenv
```

No Gmail OAuth needed — outbound email via SMTP only.

---

## Constraints & Standards

- Python 3.10+
- snake_case everywhere
- Read-only Kalshi API — no order endpoints touched in v1
- Demo environment by default — prod requires explicit config change
- Classification always batched — never loop Claude calls per market
- calls.csv and runs.csv are gitignored
- .env is gitignored
- All thresholds in config.json — no magic numbers in code
- No silent failures — log errors and continue, never crash silently

---

## Build Order for Claude Code

1. `.env.example` + `config.json`
2. `kalshi.py` — authenticate + fetch_markets, test connection first
3. `scanner.py` — score markets against base rates, no Claude yet
4. `whales.py` — trade size analysis
5. `logger.py` — standalone, no dependencies
6. `scorer.py` — Claude + web search, test on 3-5 markets
7. `report.py` — SMTP email, test send before wiring into main
8. `main.py` — wire everything together
9. `requirements.txt`

---

## v2 Backlog (don't build now)

- Auto-resolve outcomes when markets close
- Polymarket integration
- Position sizing logic (Kelly criterion)
- Read/write API key + auto-execution
- Web dashboard for track record
- Subscription signal product
