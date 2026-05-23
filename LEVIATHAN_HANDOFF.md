# Leviathan — Agent Handoff Document

This document contains everything a fresh Claude instance needs to understand,
extend, or rebuild this project. Read it fully before making changes.

---

## What This Is

**Leviathan** is a Kalshi prediction market intelligence bot. It scans open
markets for mispricings, cross-references pricing against Polymarket / Manifold
/ PredictIt, detects unusual whale activity, tracks winning wallet accounts on
Polymarket, and emails a daily signal report.

**v1 is read-only.** No trade execution. The goal is to validate edge before
touching real capital.

- **Runtime:** Python 3.10+, run manually or on a schedule
- **Output:** HTML email report to reed.helvey@gmail.com
- **Environment:** Kalshi production API

---

## File Map

```
leviathan/
├── main.py              # 8-step pipeline orchestrator
├── kalshi.py            # Kalshi API client (RSA auth, all endpoints)
├── scanner.py           # Market filtering, scoring, signal detection
├── whales.py            # Large trade / unusual volume detection
├── scorer.py            # Claude + web_search probability estimation
├── polymarket.py        # Polymarket cross-reference (Gamma API)
├── external_markets.py  # Manifold + PredictIt cross-reference
├── accounts.py          # Polymarket winning wallet tracker
├── logger.py            # calls.csv + runs.csv KPI logging
├── report.py            # HTML email report (compile + send via SMTP)
├── config.json          # All thresholds and settings
├── requirements.txt     # anthropic, requests, python-dotenv, cryptography
├── .env                 # Secrets (gitignored)
├── .env.example         # Template
├── .gitignore
├── calls.csv            # Auto-generated signal log (gitignored)
├── runs.csv             # Auto-generated run log (gitignored)
└── winning_accounts.json  # Cached Polymarket winner wallets (gitignored)
```

---

## Pipeline (main.py)

```
Step 1  Authenticate with Kalshi API
Step 2  Fetch markets via events catalog (avoids MVE parlay flood)
Step 3  Filter + score markets for mispricing (scanner.py)
Step 4  Cross-reference with Polymarket, Manifold, PredictIt + smart money scan
Step 5  Whale detection + order book depth (whales.py + kalshi.py)
Step 6  Score with Claude + web_search (scorer.py)
Step 7  Log signals to calls.csv / runs.csv (logger.py)
Step 8  Compile HTML email and send via Gmail SMTP (report.py)
```

Each step is wrapped in try/except — one failure does not kill the run.

---

## Environment Variables (.env)

```
KALSHI_KEY_ID=<UUID from Kalshi dashboard>
KALSHI_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
<key body>
-----END RSA PRIVATE KEY-----"
ANTHROPIC_API_KEY=sk-ant-...
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

**KALSHI_PRIVATE_KEY** must be quoted with double quotes in the .env file so
python-dotenv reads the multi-line PEM block as one value.

**Gmail app password:** myaccount.google.com → Security → 2-Step Verification
→ App passwords. Required for SMTP send from script.

---

## Kalshi API — Critical Details

**Discovered during build — not in original spec:**

### Base URLs
- Production: `https://api.elections.kalshi.com/trade-api/v2`
- Demo: `https://demo-api.kalshi.co/trade-api/v2`

The spec originally said `trading-api.kalshi.co` for production — **this does
not resolve.** The correct production URL is `api.elections.kalshi.com`.

### Authentication
Kalshi v2 uses RSA key signing, NOT a simple Bearer token.

Headers required on every request:
```
KALSHI-ACCESS-KEY: <key_id>
KALSHI-ACCESS-TIMESTAMP: <milliseconds since epoch>
KALSHI-ACCESS-SIGNATURE: <base64(RSA-PSS-SHA256(timestamp + METHOD + full_path))>
```

**Critical:** padding must be **PSS** (not PKCS1v15). Millisecond timestamp.
Full path must include `/trade-api/v2/` prefix.

```python
msg = f"{ts_ms}{method.upper()}/trade-api/v2{path}".encode("utf-8")
pss = asym_padding.PSS(mgf=asym_padding.MGF1(hashes.SHA256()), salt_length=asym_padding.PSS.MAX_LENGTH)
sig = private_key.sign(msg, pss, hashes.SHA256())
```

### Market Field Names (v2 API — different from v1 spec)
The API returns dollar-denominated fields, NOT cent-based:
- `yes_bid_dollars` — bid price (0.0–1.0)
- `yes_ask_dollars` — ask price (0.0–1.0)
- `volume_fp` — volume as float
- `volume_24h_fp` — 24h volume
- `last_price_dollars` — last traded price
- `previous_price_dollars` — previous price
- There is **no** `volume`, `yes_bid`, or `yes_ask` field

### Market Fetch Strategy
Fetching `/markets?status=open` returns thousands of **MVE parlay markets**
(multivariate event combination bets) sorted by creation time. Standard binary
markets are buried deep in pagination.

**Solution:** fetch via `/events` endpoint first:
1. `GET /events?status=open` → returns event catalog
2. For each event, `GET /markets?event_ticker=<ticker>` → returns that event's markets
3. Skip events where `event_ticker` contains `KXMVE`

This bypasses the MVE flood entirely.

### Order Book
`GET /markets/{ticker}/orderbook` returns full depth.
Note: MVE markets return 404 on this endpoint — handle gracefully.

### Trades
`GET /markets/{ticker}/trades?limit=100`
MVE markets also 404 here — silently ignore.

---

## Scanner Signals

Three upstream signals computed before Claude scoring (all from existing market
data — no extra API calls):

| Signal | Field | Trigger | Meaning |
|--------|-------|---------|---------|
| Spread anomaly | `spread_pct`, `spread_wide` | bid/ask spread > 5% of mid | Market maker uncertainty |
| Price drift | `price_drift`, `drift_flag` | mid > 5% from last traded price | Mean reversion candidate |
| Whale reversal | `whale_reversal` | whale direction opposes price trend | Informed contrarian |

Order book signal (requires API call per flagged market):

| Signal | Field | Trigger | Meaning |
|--------|-------|---------|---------|
| OB imbalance | `ob_imbalance`, `ob_flag`, `ob_direction` | bid or ask > 65% of total depth | Directional pressure |

### Time Horizon Buckets

Markets are classified before scoring:

```
INTRADAY  < 1 day    min_volume: 50
WEEKLY    1–7 days   min_volume: 200
MONTHLY   7–30 days  min_volume: 500
QUARTERLY 30–90 days min_volume: 500
LONG      90–180d    min_volume: 200
```

Sorting before Claude: INTRADAY first (most time-sensitive). Within each
bucket: sorted by volume descending.

---

## Scorer (Claude + web_search)

- Model: `claude-sonnet-4-6`
- Tool: `web_search_20250305`
- System prompt and tool definition have `cache_control: ephemeral` — cache
  hits reduce cost ~90% on repeat runs
- Batch cap: 5 markets per run (configurable via `max_markets_per_run`)
- Retry: 3 attempts with 60s/120s backoff on rate limit errors
- JSON extraction: searches for ```json fenced block anywhere in full response
  text, falls back to first `[` to last `]`

**Context Claude receives per market:**
- Time horizon + framing note
- Current Kalshi mid price
- Close date
- Whale alert (if detected)
- Whale reversal signal (if fired)
- Drift signal (if fired)
- Spread signal (if fired)
- Order book imbalance (if fired)
- Smart money wallets (if any winning Polymarket wallets are positioned)
- Cross-market prices from Polymarket, Manifold, PredictIt with gaps
- Base rate estimate from heuristics (if applicable)

---

## Cross-Market Integration

### Polymarket (polymarket.py)
- API: `https://gamma-api.polymarket.com/markets`
- No auth required
- Prices embedded in `outcomePrices` field (JSON string, parse it)
- Matching: combined Jaccard word overlap + SequenceMatcher ratio

### Manifold Markets (external_markets.py)
- API: `https://api.manifold.markets/v0/markets?filter=open&sort=liquidity`
- No auth required
- Only `BINARY` markets with `probability` field are used
- Paginates via `before` cursor (last market's `id`)

### PredictIt (external_markets.py)
- API: `https://www.predictit.org/api/marketdata/all/`
- No auth required, returns all markets in one call
- Single-contract markets: `bestBuyYesCost` = YES probability
- Two-contract markets: each contract normalized separately (title includes contract name)
- Multi-contract (3+): skipped

### Metaculus
- Returns 403 on all endpoints — cannot access without auth/cookies.

---

## Smart Money Tracker (accounts.py)

- Source: Polymarket on-chain data via `https://data-api.polymarket.com`
- Discovery: fetch 300 recent trades → unique wallets → fetch each wallet's
  open positions → score by position count, avg % PnL, total cash PnL
- Winner criteria (config): `min_positions=3`, `min_pct_pnl=10%`, `min_cash_pnl=$25`
- Cache: `winning_accounts.json` — refreshed every 24h
- Scan: for each flagged market with a Polymarket match, fetch the winner's
  recent trades and check if any match the conditionId
- Direction: taken from the `outcome` field on the most recent trade

**Key API endpoints:**
```
GET https://data-api.polymarket.com/trades?limit=300          # recent all-market trades
GET https://data-api.polymarket.com/trades?user=ADDRESS       # user trade history
GET https://data-api.polymarket.com/positions?user=ADDRESS    # open positions with PnL
```

---

## Report (report.py)

Sends `multipart/alternative` — HTML for modern clients, plain text fallback.

HTML design tokens (all defined at top of report.py):
```python
BG      = "#0d0f14"   # page background
SURFACE = "#141921"   # card background
SURFACE2= "#1a2235"   # nested elements
BORDER  = "#1e2a3c"
TEXT    = "#e2e8f0"
TEXT2   = "#6b7e96"
TEXT3   = "#2d3f52"
BRAND   = "#7c6af7"
C_HIGH  = "#34d399"
C_MED   = "#fbbf24"
C_LOW   = "#f87171"
```

Signal cards are grouped by time horizon in the email.
Inter font via Google Fonts (degrades to system font in Outlook).

---

## config.json Reference

```json
{
  "environment": "prod",                  // "demo" or "prod"
  "markets": {
    "max_fetch": 1000,                    // fallback /markets pagination cap
    "max_events": 150,                    // events to fetch for market discovery
    "edge_threshold": 0.08,              // heuristic edge required to flag
    "min_volume": 500,                    // global fallback (bucket_min_volume takes precedence)
    "max_volume_filter": 150000,          // above this = too liquid
    "min_days_to_close": 0,              // 0 = allow same-day markets
    "max_days_to_close": 180,
    "bucket_min_volume": {               // per-time-horizon volume floors
      "INTRADAY": 50, "WEEKLY": 200,
      "MONTHLY": 500, "QUARTERLY": 500, "LONG": 200
    },
    "efficient_market_keywords": [...],  // titles matching these are filtered out
    "categories": []                     // empty = all categories
  },
  "whales": {
    "size_multiplier": 5,               // trade > 5x avg = whale
    "volume_spike_hours": 1,
    "lookback_trades": 100
  },
  "scoring": {
    "scorer_model": "claude-sonnet-4-6",
    "max_markets_per_run": 5,           // Claude batch cap (controls cost)
    "confidence_threshold": "MED"       // LOW/MED/HIGH — signals below this filtered from report
  },
  "accounts": {
    "enabled": true,
    "min_positions": 3,                 // wallet must have ≥3 open positions
    "min_pct_pnl": 10.0,              // avg position return ≥10%
    "min_cash_pnl": 25.0,             // total unrealized ≥$25
    "max_wallets_to_track": 50,
    "max_wallets_per_scan": 20,        // check top 20 winners per market
    "cache_ttl_hours": 24,
    "discovery_sample_size": 300
  },
  "external_markets": {
    "enabled": true,
    "manifold_limit": 500,
    "min_match_score": 0.45,
    "min_price_gap": 0.0
  },
  "polymarket": {
    "enabled": true,
    "max_fetch": 500,
    "min_match_score": 0.50,
    "min_price_gap": 0.0
  },
  "report": {
    "email_to": "reed.helvey@gmail.com",
    "email_from": "",                   // defaults to email_to if empty
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587
  }
}
```

---

## Known Issues / Gotchas

1. **calls.csv / runs.csv** — if open in Excel on Windows, writes fail with
   PermissionError. Logger catches this gracefully and prints a warning.
   Close the files in Excel before running.

2. **Manifold returns 0 binary markets** when the API returns multi-choice
   markets first. The paginator may need keyword-based search instead of
   bulk liquidity sort to reliably find binary markets.

3. **Rate limiting** — Anthropic limits to 30k input tokens/min at lower tiers.
   Scorer retries with 60s/120s backoff. Reduce `max_markets_per_run` if
   hitting this consistently.

4. **Smart money conditionId matching** — accounts.py passes `poly.condition_id`
   to scan for trades. If the Polymarket match didn't find a conditionId
   (fuzzy match returned slug but no conditionId), the scan is skipped silently.

5. **Kalshi demo vs prod** — API keys are environment-specific. A key created
   on the production dashboard will return NOT_FOUND on the demo API.
   The production URL also changed: it's now `api.elections.kalshi.com`,
   not `trading-api.kalshi.co` (old spec) or `trading-api.kalshi.com`.

6. **Windows console encoding** — main.py wraps stdout/stderr with UTF-8 to
   prevent crashes from Unicode characters in API responses on cp1252 terminals.

---

## v2 Backlog

- Auto-resolve outcomes when Kalshi markets close (update calls.csv)
- Kelly criterion position sizing (for when execution is added)
- Polymarket write API + Kalshi execution (v2 — needs separate prod key)
- Order book imbalance using multi-level depth (currently only top level)
- Metaculus integration (currently blocked — requires browser auth)
- Scheduled runs via Windows Task Scheduler or cron
- Web dashboard for track record visualization
- Polymarket copy-trading from identified winning wallets

---

## Setup From Scratch

```bash
# 1. Clone repo
git clone <repo_url>
cd leviathan

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up credentials
cp .env.example .env
# Edit .env — add KALSHI_KEY_ID, KALSHI_PRIVATE_KEY (full PEM in double quotes),
# ANTHROPIC_API_KEY, GMAIL_APP_PASSWORD

# 4. Run
python main.py
```

The first run will:
- Authenticate with Kalshi
- Fetch ~879 markets from 150 events
- Score 5 markets with Claude (~$0.30)
- Create calls.csv and runs.csv automatically
- Discover winning Polymarket wallets and cache them
- Send an HTML email report
