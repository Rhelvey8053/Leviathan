"""
Research-probe experiment: Claude+websearch estimates probability for a
stratified sample of markets, including ones the mechanical funnel rejects.

PARTS:
  A — stratified_sample(): ~50 markets across volume tiers, including
      filter_markets rejects, with strata breakdown printed.
  B — probe_market(): one Claude CLI call per market with websearch;
      tracks call count and runtime.
  C — results logged as source='research_probe' via logger.log_probe().
  D — forward scoring: resolve_outcomes() handles probe rows naturally;
      get_stats_probe() reports hit rate once markets settle.

Run:
    python analysis/research_probe.py

NOTE: run-one produces divergences (hypotheses) only. Edge verdict requires
resolved probe rows and is PENDING until markets settle.
"""

import copy
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import logger
import scanner

SNAPSHOT_DIR = os.path.join(ROOT, "data", "snapshots")
CONFIG_PATH  = os.path.join(ROOT, "config.json")

# Volume tier definitions: (label, min_vol_inclusive, max_vol_exclusive, sample_quota)
VOLUME_TIERS = [
    ("thin     (<500)",     0,       500,    12),
    ("light    (500-5k)",   500,     5_000,  12),
    ("medium   (5k-50k)",   5_000,   50_000, 10),
    ("heavy    (50k-150k)", 50_000,  150_000, 8),
    ("liquid   (>150k)",    150_000, float("inf"), 8),
]

PROBE_SYSTEM = (
    "You are a prediction market analyst. For each market, search for relevant "
    "recent information and estimate the true probability of the YES outcome. "
    "Return ONLY valid JSON — no markdown, no extra text.\n\n"

    "CALIBRATION RULES (follow strictly):\n"
    "1. TAIL PROBABILITY: If the market price is below 15%, it is almost always correct. "
    "The crowd has already discounted this. Require extraordinary, independently-verified "
    "evidence to set your estimate above 30% on a sub-15% market. If in doubt, PASS.\n"
    "2. SOURCE CHAIN: Before citing 'multiple sources confirm X', verify they are truly "
    "independent. Media reports citing the same original tweet/press release/rumour are "
    "ONE source, not many.\n"
    "3. ANNOUNCED vs COMPLETED: For IPOs, mergers, media releases, product launches — "
    "'announced' or 'confirmed in development' is NOT evidence of completion by the "
    "market's deadline. Deals fall through. Release dates slip constantly.\n"
    "4. ENTERTAINMENT/MEDIA MARKETS: Treat any market about a movie, TV show, streaming "
    "release, or entertainment event with extreme skepticism. Even confirmed productions "
    "routinely miss announced dates. Base rate for on-time delivery is ~25%. CRITICAL: "
    "If a market about a media/entertainment release is priced below 10%, your estimate "
    "MUST be below 15% regardless of any announcement you find. 'In production', "
    "'confirmed', 'announced release date' are NOT evidence of on-time delivery.\n"
    "5. IPO ANNOUNCEMENT MARKETS ('When will X officially announce an IPO?'): Base rate "
    "is ~25% for any given 3-6 month window. 'Confidentially filed', 'preparing to IPO', "
    "'considering going public', 'rumored 2026 IPO', or 'banks hired' are standard "
    "pre-IPO steps — NOT evidence of imminent announcement. Only an actual S-1 filing "
    "or confirmed official date should push your estimate meaningfully above the base rate.\n"
    "6. CABINET/STAFF DEPARTURE MARKETS ('Will any member of X Cabinet leave before Y?'): "
    "Base rate is ~65% within the first 20 months of a Trump term based on historical "
    "turnover. A market priced below 50% is likely underpriced — weight the historical "
    "base rate heavily.\n"
    "7. SPORTS DEBUT MARKETS ('Will X make his MLB/NBA/NHL debut by Y?'): Base rate for "
    "an unconfirmed prospect is ~35% within a 6-month window. 'On the 40-man roster', "
    "'expected to be called up', or 'in spring training' is still 35% base rate. Only "
    "an active roster assignment with a confirmed start date is strong evidence.\n"
    "8. AI/TECH MODEL RELEASE MARKETS ('Will OpenAI/Anthropic/Google release X by Y?'): "
    "Apply Rules 3 and 4 strictly. Base rate is ~25% for any given 3-6 month window. "
    "'In development', 'expected to launch', 'roadmap mentions', 'leaked benchmarks', "
    "and 'CEO hints at release' are NOT evidence of on-time delivery. Only an official "
    "public release with a live product meets the deadline.\n"
    "9. CROSS-MARKET DIVERGENCE: When CROSS-MARKET or POLYMARKET data shows the same "
    "question priced significantly differently on another platform, treat it as strong "
    "evidence. Polymarket is liquid with professional traders and is often better "
    "calibrated than Kalshi for political and world events. Multi-platform consensus "
    "(Polymarket AND Manifold both higher/lower) is a much stronger signal than a "
    "single-platform gap.\n"
    "10. HIGH CONFIDENCE threshold: Only assign HIGH confidence when you find dated, "
    "primary-source evidence (official press release, regulatory filing, official "
    "announcement by the relevant authority) that directly speaks to the specific "
    "deadline in the market.\n"
    "11. EDGE REQUIREMENT: Only call YES or NO if your estimate differs from the market "
    "price by at least 10 percentage points AND you have clear evidence. Otherwise PASS.\n"
    "12. LEGISLATIVE MARKETS ('Will X bill pass the Senate/House by Y?'): Base rate for "
    "any specific bill reaching a floor vote and passing is ~35%. A market priced above "
    "55% requires specific primary-source evidence of unusual momentum: cloture already "
    "cleared, the other chamber already passed it, or a confirmed floor vote with whip "
    "count showing the votes are there. 'Has momentum' or 'could pass' do NOT qualify.\n"
    "13. PRICE/LEVEL MARKETS ('Will X reach $Y?' or 'Will X be above/below Y%?'): Near "
    "50/50 by construction — the crowd has already priced in the current trajectory. "
    "Only deviate meaningfully from 50% if you find a specific, dated catalyst the "
    "crowd has clearly not priced. Default to PASS unless current price is >20pp from 50%.\n"
    "14. EARNINGS BEAT/MISS MARKETS ('Will X beat earnings estimates in Q?'): Base rate "
    "is ~50% by definition — analysts recalibrate continuously. Only deviate meaningfully "
    "if you find a specific verified catalyst (channel-check, pre-announced result, "
    "unusual guidance revision) the market has not absorbed. Default to PASS.\n"
    "15. DIPLOMATIC SUMMIT MARKETS ('Will X meet with Y?'): Base rate ~40% for any "
    "3-6 month window. 'Talks scheduled', 'both sides willing', or 'channel open' is "
    "standard background. Only a confirmed date with official public statements from "
    "both governments qualifies as strong evidence.\n"
    "16. REELECTION MARKETS ('Will X win re-election?'): Treat like general election "
    "markets. Incumbents have a modest structural advantage (~52%), but current polling "
    "and economic conditions dominate near the election. Only HIGH confidence if dated "
    "polling average shows >5pp sustained lead in likely-voter models within 30 days.\n"
    "17. CORPORATE LEADERSHIP MARKETS ('Will X become CEO/CFO/Chair of Y?'): Base rate "
    "~35% for any specific appointment window. 'Board considering', 'rumored front-runner', "
    "'headhunters hired', or 'activist pressure' are standard pre-steps that often don't "
    "materialize. Only a confirmed board announcement or SEC 8-K filing is strong evidence.\n"
    "18. UN SECURITY COUNCIL MARKETS ('Will the UNSC pass a resolution on X?'): Base "
    "rate ~15% due to Chinese/Russian veto risk. 'Western support' or 'draft circulating' "
    "is NOT evidence — Russia and China veto dozens of such drafts. Only a non-contested "
    "procedural vote or documented unanimous agreement justifies >30%."
)

PROBE_SCHEMA = """
Return a JSON object with exactly these fields:
{
  "ticker": "string",
  "claude_estimate": 0.00,
  "predicted_direction": "YES | NO | PASS",
  "confidence": "HIGH | MED | LOW",
  "rationale": "one sentence"
}

predicted_direction = "YES"  if claude_estimate > market_price + 0.03
predicted_direction = "NO"   if claude_estimate < market_price - 0.03
predicted_direction = "PASS" if within ±3% of market_price
"""


# ── Part A: Stratified sampling ───────────────────────────────────────────────

def _vol(m: dict) -> float:
    return float(m.get("volume_fp") or m.get("volume") or 0)


def _mid(m: dict) -> float | None:
    bid = float(m.get("yes_bid_dollars") or m.get("yes_bid") or 0)
    ask = float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0)
    return (bid + ask) / 2 if (bid + ask) > 0 else None


def _days_to_close(m: dict) -> float:
    raw = m.get("close_time") or m.get("expiration_time") or ""
    if not raw:
        return float("inf")
    try:
        close = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return (close - datetime.now(timezone.utc)).total_seconds() / 86400
    except Exception:
        return float("inf")


def stratified_sample(
    markets: list[dict],
    target_n: int = 50,
    filter_cfg: dict | None = None,
    max_days_to_close: float | None = None,
    seed: int = 42,
) -> list[dict]:
    """
    Returns ~target_n markets stratified by volume tier.
    Annotates each with filter_pass=True/False so the composition is visible.
    Deliberately includes filter_markets rejects to test edge outside the funnel.
    If max_days_to_close is set, markets closing further out are excluded before sampling.
    """
    rng = random.Random(seed)

    # Apply close-time filter before binning
    if max_days_to_close is not None:
        before = len(markets)
        markets = [m for m in markets if _days_to_close(m) <= max_days_to_close]
        print(f"  [probe] max_days_to_close={max_days_to_close}: {len(markets)}/{before} markets kept")

    # Identify filter survivors for annotation
    survivors: set[str] = set()
    if filter_cfg is not None:
        try:
            passed = scanner.filter_markets(markets, filter_cfg)
            survivors = {m.get("ticker", "") for m in passed}
        except Exception:
            pass

    # Bin markets by volume tier
    tier_bins: dict[str, list[dict]] = {t[0]: [] for t in VOLUME_TIERS}
    for m in markets:
        v = _vol(m)
        for label, lo, hi, _ in VOLUME_TIERS:
            if lo <= v < hi:
                tier_bins[label].append(m)
                break

    sample = []
    for label, lo, hi, quota in VOLUME_TIERS:
        pool = tier_bins[label]
        rng.shuffle(pool)
        chosen = pool[:quota]
        annotated = []
        for m in chosen:
            mid = _mid(m)
            m = dict(m)
            m["mid_price"]   = mid
            m["filter_pass"] = m.get("ticker", "") in survivors
            m["vol_tier"]    = label
            annotated.append(m)
        sample.extend(annotated)

    rng.shuffle(sample)
    return sample[:target_n]


def print_strata(sample: list[dict]) -> None:
    print(f"\n=== Stratified sample: {len(sample)} markets ===\n")
    tier_counts: dict = {}
    pass_count = 0
    for m in sample:
        t = m.get("vol_tier", "?")
        tier_counts[t] = tier_counts.get(t, 0) + 1
        if m.get("filter_pass"):
            pass_count += 1

    print(f"  Filter survivors in sample: {pass_count}/{len(sample)}")
    print(f"  Filter rejects in sample:   {len(sample) - pass_count}/{len(sample)}")
    print()
    print(f"  {'Volume tier':<28}  {'Count':>5}")
    print("  " + "-" * 36)
    for label, _, _, _ in VOLUME_TIERS:
        n = tier_counts.get(label, 0)
        print(f"  {label:<28}  {n:>5}")
    print()


# ── Part B: Probe each market ─────────────────────────────────────────────────

def _find_claude() -> str:
    cmd = shutil.which("claude")
    if cmd:
        return cmd
    candidates = [
        r"C:\Users\Administrator\AppData\Local\AnthropicClaude\claude.exe",
        r"C:\Program Files\Claude\claude.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise RuntimeError("claude CLI not found in PATH.")


def _build_probe_prompt(market: dict) -> str:
    from datetime import timedelta
    mid    = market.get("mid_price")
    ticker = market.get("ticker", "")
    title  = market.get("title", "")[:140]
    close  = market.get("close_time") or market.get("expiration_time", "")
    price_str = f"{mid * 100:.1f}%" if mid is not None else "unknown"

    # Compute time horizon note for Claude context
    horizon_note = ""
    try:
        close_dt = datetime.fromisoformat(close.replace("Z", "+00:00"))
        days = (close_dt - datetime.now(timezone.utc)).days
        if days <= 0:
            horizon_note = "closes today — weight breaking news and current momentum only"
        elif days <= 7:
            horizon_note = "closes within 7 days — near-term catalysts most relevant"
        elif days <= 30:
            horizon_note = "closes within 30 days — balance recent news with base rates"
        elif days <= 90:
            horizon_note = "closes within 90 days — fundamentals and structural factors carry more weight"
        else:
            horizon_note = "closes 90+ days out — base rates and long-run trends dominate"
    except Exception:
        pass

    return (
        f"Research this Kalshi prediction market and estimate the probability of YES.\n\n"
        f"Ticker:         {ticker}\n"
        f"Title:          {title}\n"
        f"Market price:   {price_str}  (YES)\n"
        f"Closes:         {close}\n"
        + (f"Time context:   {horizon_note}\n" if horizon_note else "") +
        f"\n{PROBE_SCHEMA}"
    )


def probe_market(market: dict, config: dict) -> dict:
    """
    Calls the Claude CLI with websearch for one market.
    Returns a probe result dict with claude_estimate, predicted_direction,
    confidence, rationale, and runtime_ms.
    Raises RuntimeError on CLI failure.
    """
    claude_cmd = _find_claude()
    prompt     = _build_probe_prompt(market)
    mid        = market.get("mid_price", 0) or 0

    clean_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    t0 = time.time()

    result = subprocess.run(
        [
            claude_cmd,
            "--print",
            "--system-prompt", PROBE_SYSTEM,
            "--allowedTools", "WebSearch",
            "--output-format", "text",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=180,
        encoding="utf-8",
        errors="replace",
        env=clean_env,
    )

    runtime_ms = int((time.time() - t0) * 1000)

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"probe_market: claude CLI exit {result.returncode} on {market.get('ticker')}: {err[:200]}"
        )

    raw = result.stdout.strip()
    # Extract JSON object from response
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw, re.DOTALL)
    if fence:
        raw_json = fence.group(1).strip()
    else:
        start = raw.find("{")
        end   = raw.rfind("}")
        raw_json = raw[start:end + 1] if start != -1 and end > start else raw

    parsed = json.loads(raw_json)

    est        = float(parsed.get("claude_estimate", mid))
    divergence = round(est - mid, 4)

    return {
        "ticker":               market.get("ticker", ""),
        "title":                market.get("title", ""),
        "market_price_at_probe": round(mid, 4),
        "claude_estimate":      round(est, 4),
        "divergence":           divergence,
        "predicted_direction":  parsed.get("predicted_direction", "PASS"),
        "confidence":           parsed.get("confidence", ""),
        "rationale":            parsed.get("rationale", ""),
        "runtime_ms":           runtime_ms,
        "filter_pass":          market.get("filter_pass", False),
        "vol_tier":             market.get("vol_tier", ""),
    }


# ── Main runner ───────────────────────────────────────────────────────────────

def load_snapshot() -> tuple[list[dict], dict]:
    files = sorted(
        f for f in os.listdir(SNAPSHOT_DIR)
        if f.startswith("markets_") and f.endswith(".json")
    )
    if not files:
        raise FileNotFoundError(f"No snapshot in {SNAPSHOT_DIR}")
    with open(os.path.join(SNAPSHOT_DIR, files[-1]), encoding="utf-8") as f:
        data = json.load(f)
    return data["markets"], data["header"]


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_watchlist_tickers() -> set[str]:
    """Load smart money cross-referenced Kalshi tickers from latest_signals cache."""
    cache = os.path.join(ROOT, "data", "smart_money", "latest_signals.json")
    if not os.path.exists(cache):
        return set()
    try:
        import json as _json
        with open(cache, encoding="utf-8") as f:
            data = _json.load(f)
        return set(data.get("kalshi_tickers", []))
    except Exception:
        return set()


def run_probe(config: dict | None = None) -> dict:
    """
    Full probe run:
      1. Load snapshot and build stratified sample.
      2. Force-include smart money watchlist tickers (probed first).
      3. Probe up to max_probe_markets markets with Claude+websearch.
      4. Log results via logger.log_probe().
      5. Print divergence table and call count.
    Returns summary dict.
    """
    if config is None:
        config = load_config()

    max_probe         = config.get("scoring", {}).get("max_probe_markets", 10)
    max_days_to_close = config.get("scoring", {}).get("max_probe_days_to_close", None)
    markets, header   = load_snapshot()

    print(f"Snapshot: {header.get('fetched_at')} ({header.get('market_count')} markets)")

    # Index markets by ticker for watchlist lookup
    by_ticker = {m.get("ticker", ""): m for m in markets}

    # Force-include smart money watchlist tickers — probe these first
    watchlist_tickers = _load_watchlist_tickers()
    priority: list[dict] = []
    if watchlist_tickers:
        for t in sorted(watchlist_tickers):
            m = by_ticker.get(t)
            if m:
                mid = _mid(m)
                m   = dict(m)
                m["mid_price"]   = mid
                m["filter_pass"] = True  # watchlist tickers are pre-vetted
                m["vol_tier"]    = "watchlist"
                m["watchlist"]   = True
                priority.append(m)
        print(f"  [probe] watchlist: {len(priority)}/{len(watchlist_tickers)} tickers found in snapshot")

    sample = stratified_sample(markets, target_n=50, filter_cfg=config,
                               max_days_to_close=max_days_to_close)
    print_strata(sample)

    # Merge: watchlist first, then stratified (dedup by ticker)
    seen: set[str] = {m.get("ticker", "") for m in priority}
    combined = list(priority)
    for m in sample:
        if m.get("ticker", "") not in seen:
            combined.append(m)

    to_probe = combined[:max_probe]
    n_wl     = sum(1 for m in to_probe if m.get("watchlist"))
    print(f"Probing {len(to_probe)} markets ({n_wl} watchlist priority, max_probe_markets={max_probe})...\n")

    results      = []
    total_calls  = 0
    total_ms     = 0
    errors       = 0

    for i, m in enumerate(to_probe, 1):
        ticker = m.get("ticker", "")
        mid    = m.get("mid_price")
        price_str = f"{mid:.3f}" if mid is not None else "N/A"
        print(f"  [{i:02d}/{len(to_probe)}] {ticker:<44} price={price_str}  ", end="", flush=True)
        try:
            probe  = probe_market(m, config)
            total_calls += 1
            total_ms    += probe["runtime_ms"]
            logger.log_probe(probe)
            results.append(probe)
            sign = "+" if probe["divergence"] >= 0 else ""
            print(
                f"est={probe['claude_estimate']:.3f}  div={sign}{probe['divergence']:.3f}  "
                f"{probe['predicted_direction']:<4}  {probe['confidence']}  ({probe['runtime_ms']}ms)"
            )
        except Exception as e:
            errors += 1
            print(f"ERROR: {e}")

    # Build watchlist set for table annotation
    wl_set = _load_watchlist_tickers()

    # Divergence table
    print(f"\n{'='*90}")
    print(f"  {'Ticker':<44} {'Mid':>6}  {'Est':>6}  {'Div':>7}  {'Dir':<5}  {'Conf':<5}  Filter")
    print(f"  {'-'*85}")
    for r in sorted(results, key=lambda x: -abs(x["divergence"])):
        sign = "+" if r["divergence"] >= 0 else ""
        fp   = "PASS" if r.get("filter_pass") else "REJECT"
        wl   = " [WL]" if r.get("ticker") in wl_set else ""
        print(
            f"  {r['ticker']:<44} "
            f"{r['market_price_at_probe']:>6.3f}  "
            f"{r['claude_estimate']:>6.3f}  "
            f"{sign}{r['divergence']:>6.3f}  "
            f"{r['predicted_direction']:<5}  "
            f"{r['confidence']:<5}  "
            f"{fp}{wl}"
        )

    print(f"\n{'='*90}")
    print(f"  Total CLI calls:     {total_calls}")
    print(f"  Errors:              {errors}")
    print(f"  Total runtime:       {total_ms / 1000:.1f}s  (avg {total_ms // max(total_calls, 1)}ms/market)")
    print(f"  Probe rows in DB:    {len(results)}")
    print()
    print("  NOTE: divergences above are hypotheses only. Edge verdict requires")
    print("  resolved outcomes and is PENDING until markets settle.")
    print(f"{'='*90}\n")

    return {
        "probed":       len(to_probe),
        "succeeded":    total_calls,
        "errors":       errors,
        "total_ms":     total_ms,
        "results":      results,
    }


if __name__ == "__main__":
    cfg = load_config()
    run_probe(cfg)
