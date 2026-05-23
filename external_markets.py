"""
External prediction market aggregator.

Fetches probability estimates from Manifold Markets and PredictIt, then
cross-references them against Kalshi flagged markets. When multiple
independent platforms agree on a price gap, the signal confidence rises.
When they diverge, that's also informative.

Sources:
  Manifold Markets  — broad community of forecasters, binary + multi-outcome
  PredictIt         — regulated US political market, sharp money, clean prices
"""

import requests
from polymarket import _match_score  # reuse the same fuzzy matching logic


# ── Fetch ─────────────────────────────────────────────────────────────────────

def _fetch_manifold(limit: int = 500) -> list[dict]:
    """
    Returns open Manifold binary markets sorted by liquidity (highest first).
    Only markets with a probability value are included.
    """
    markets = []
    before  = None

    while len(markets) < limit:
        params = {
            "limit":  min(100, limit - len(markets)),
            "filter": "open",
            "sort":   "liquidity",
        }
        if before:
            params["before"] = before

        try:
            resp = requests.get(
                "https://api.manifold.markets/v0/markets",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            page = resp.json()
        except Exception:
            break

        if not isinstance(page, list) or not page:
            break

        markets.extend(page)
        if len(page) < 100:
            break
        before = page[-1].get("id")

    return [
        m for m in markets
        if m.get("outcomeType") == "BINARY" and m.get("probability") is not None
    ]


def _fetch_predictit() -> list[dict]:
    """
    Returns all PredictIt open markets.
    Single-contract markets are straightforward binary YES/NO.
    Two-contract markets (e.g. Republican vs Democrat) are also returned
    — callers decide how to interpret them.
    """
    try:
        resp = requests.get(
            "https://www.predictit.org/api/marketdata/all/",
            timeout=15,
            headers={"User-Agent": "Leviathan/1.0"},
        )
        resp.raise_for_status()
        return [
            m for m in resp.json().get("markets", [])
            if m.get("status") == "Open"
        ]
    except Exception:
        return []


# ── Normalize ─────────────────────────────────────────────────────────────────

def _norm_manifold(m: dict) -> dict:
    return {
        "title":       m.get("question", ""),
        "probability": float(m["probability"]),
        "volume":      float(m.get("volume") or 0),
        "source":      "Manifold",
        "url":         m.get("url", ""),
    }


def _norm_predictit(m: dict) -> list[dict]:
    """
    Normalizes a PredictIt market.

    Single-contract: bestBuyYesCost is the implied YES probability.
    Two-contract (binary party/outcome): treat first contract's YES cost as
    the probability for that specific outcome (title-matching will align it).
    Multi-contract (3+): skip — not meaningfully comparable to a Kalshi binary.
    """
    contracts = [c for c in m.get("contracts", []) if c.get("status") == "Open"]
    url       = m.get("url", "")
    results   = []

    if len(contracts) == 1:
        c    = contracts[0]
        prob = c.get("bestBuyYesCost") or c.get("lastTradePrice")
        if prob is not None:
            results.append({
                "title":       m.get("name", ""),
                "probability": float(prob),
                "volume":      0,
                "source":      "PredictIt",
                "url":         url,
            })

    elif len(contracts) == 2:
        # Two-sided market: both contracts are individual outcomes
        for c in contracts:
            prob = c.get("bestBuyYesCost") or c.get("lastTradePrice")
            if prob is not None:
                # Title = market name + contract name
                title = f"{m.get('name', '')} — {c.get('name', '')}"
                results.append({
                    "title":       title,
                    "probability": float(prob),
                    "volume":      0,
                    "source":      "PredictIt",
                    "url":         url,
                })

    return results


# ── Index + matching ──────────────────────────────────────────────────────────

def build_index(manifold: list[dict], predictit: list[dict]) -> list[dict]:
    """
    Combines and normalizes markets from all sources into a single flat index.
    Each entry: {title, probability, volume, source, url}
    """
    index = []

    for m in manifold:
        try:
            index.append(_norm_manifold(m))
        except Exception:
            pass

    for m in predictit:
        try:
            index.extend(_norm_predictit(m))
        except Exception:
            pass

    return [e for e in index if e.get("title") and e.get("probability") is not None]


def find_matches(kalshi_title: str, index: list[dict], threshold: float = 0.45) -> list[dict]:
    """
    Finds all external markets that match a Kalshi title above the threshold.
    Returns all matches (not just the best) so multi-source consensus is visible.
    Sorted by match score descending.
    """
    scored = []
    for ext in index:
        score = _match_score(kalshi_title, ext["title"])
        if score >= threshold:
            scored.append({**ext, "match_score": round(score, 3)})

    # Deduplicate by source — keep best match per source
    best_per_source: dict[str, dict] = {}
    for m in sorted(scored, key=lambda x: x["match_score"], reverse=True):
        src = m["source"]
        if src not in best_per_source:
            best_per_source[src] = m

    return sorted(best_per_source.values(), key=lambda x: x["match_score"], reverse=True)


# ── Main entry point ──────────────────────────────────────────────────────────

def cross_reference(flagged_markets: list[dict], config: dict) -> dict[str, list[dict]]:
    """
    For each flagged Kalshi market, finds matching markets on Manifold and
    PredictIt and computes the price gap relative to Kalshi's mid price.

    Returns: {kalshi_ticker: [match_dict, ...]}
    Each match_dict has: title, probability, price_gap, source, url, match_score
    """
    cfg       = config.get("external_markets", {})
    threshold = cfg.get("min_match_score", 0.45)
    min_gap   = cfg.get("min_price_gap",   0.0)

    if not cfg.get("enabled", True):
        return {}

    manifold_limit = cfg.get("manifold_limit", 500)

    print("  [ext] Fetching Manifold...", end=" ", flush=True)
    manifold = _fetch_manifold(manifold_limit)
    print(f"{len(manifold)} binary markets")

    print("  [ext] Fetching PredictIt...", end=" ", flush=True)
    predictit = _fetch_predictit()
    print(f"{len(predictit)} markets")

    index = build_index(manifold, predictit)
    print(f"  [ext] Index built: {len(index)} normalized entries")

    results = {}
    for m in flagged_markets:
        ticker     = m.get("ticker", "")
        title      = m.get("title", "")
        kalshi_mid = m.get("mid_price")
        if not title or kalshi_mid is None:
            continue

        matches = find_matches(title, index, threshold)
        enriched = []
        for match in matches:
            gap = match["probability"] - kalshi_mid
            if abs(gap) < min_gap:
                continue
            enriched.append({
                **match,
                "price_gap": round(gap, 4),
                "kalshi_mid": kalshi_mid,
            })

        if enriched:
            results[ticker] = enriched

    matched = len(results)
    total_hits = sum(len(v) for v in results.values())
    print(f"  [ext] {matched} Kalshi markets matched ({total_hits} cross-market comparisons)")

    return results


def consensus_summary(ext_matches: list[dict], kalshi_mid: float) -> dict:
    """
    Summarizes cross-market consensus for a single Kalshi market.

    Returns:
      sources_higher  — count of external markets priced above Kalshi
      sources_lower   — count below
      avg_ext_price   — mean probability across all external matches
      consensus_gap   — avg_ext_price - kalshi_mid
      consensus_dir   — "YES" if most sources higher, "NO" if lower, None if split
    """
    if not ext_matches:
        return {}

    prices = [m["probability"] for m in ext_matches]
    avg    = sum(prices) / len(prices)
    higher = sum(1 for p in prices if p > kalshi_mid)
    lower  = sum(1 for p in prices if p < kalshi_mid)

    return {
        "sources_higher":  higher,
        "sources_lower":   lower,
        "avg_ext_price":   round(avg, 4),
        "consensus_gap":   round(avg - kalshi_mid, 4),
        "consensus_dir":   "YES" if higher > lower else ("NO" if lower > higher else None),
    }
