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
    "Return ONLY valid JSON — no markdown, no extra text."
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


def stratified_sample(
    markets: list[dict],
    target_n: int = 50,
    filter_cfg: dict | None = None,
    seed: int = 42,
) -> list[dict]:
    """
    Returns ~target_n markets stratified by volume tier.
    Annotates each with filter_pass=True/False so the composition is visible.
    Deliberately includes filter_markets rejects to test edge outside the funnel.
    """
    rng = random.Random(seed)

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
    mid    = market.get("mid_price")
    ticker = market.get("ticker", "")
    title  = market.get("title", "")[:140]
    close  = market.get("close_time") or market.get("expiration_time", "")
    price_str = f"{mid * 100:.1f}%" if mid is not None else "unknown"

    return (
        f"Research this Kalshi prediction market and estimate the probability of YES.\n\n"
        f"Ticker:         {ticker}\n"
        f"Title:          {title}\n"
        f"Market price:   {price_str}  (YES)\n"
        f"Closes:         {close}\n\n"
        f"{PROBE_SCHEMA}"
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


def run_probe(config: dict | None = None) -> dict:
    """
    Full probe run:
      1. Load snapshot and build stratified sample.
      2. Probe up to max_probe_markets markets with Claude+websearch.
      3. Log results via logger.log_probe().
      4. Print divergence table and call count.
    Returns summary dict.
    """
    if config is None:
        config = load_config()

    max_probe = config.get("scoring", {}).get("max_probe_markets", 10)
    markets, header = load_snapshot()

    print(f"Snapshot: {header.get('fetched_at')} ({header.get('market_count')} markets)")

    sample = stratified_sample(markets, target_n=50, filter_cfg=config)
    print_strata(sample)

    to_probe = sample[:max_probe]
    print(f"Probing {len(to_probe)} markets (max_probe_markets={max_probe})...\n")

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

    # Divergence table
    print(f"\n{'='*90}")
    print(f"  {'Ticker':<44} {'Mid':>6}  {'Est':>6}  {'Div':>7}  {'Dir':<5}  {'Conf':<5}  Filter")
    print(f"  {'-'*85}")
    for r in sorted(results, key=lambda x: -abs(x["divergence"])):
        sign = "+" if r["divergence"] >= 0 else ""
        fp   = "PASS" if r.get("filter_pass") else "REJECT"
        print(
            f"  {r['ticker']:<44} "
            f"{r['market_price_at_probe']:>6.3f}  "
            f"{r['claude_estimate']:>6.3f}  "
            f"{sign}{r['divergence']:>6.3f}  "
            f"{r['predicted_direction']:<5}  "
            f"{r['confidence']:<5}  "
            f"{fp}"
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
