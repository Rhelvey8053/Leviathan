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
from .polymarket import _match_score  # reuse the same fuzzy matching logic
from . import metaculus
from . import odds_api as _odds_api

_STOP = {
    "will", "the", "a", "an", "be", "in", "on", "by", "to", "of", "or",
    "and", "is", "for", "at", "it", "its", "who", "what", "when", "which",
    "that", "this", "have", "has", "do", "does", "not", "than", "more",
}


# ── Fetch ─────────────────────────────────────────────────────────────────────

def _search_manifold(titles: list[str], results_per_query: int = 8) -> list[dict]:
    """
    Searches Manifold for binary markets relevant to each Kalshi title.
    Uses /v0/search-markets (keyword-based) — more reliable than bulk liquidity fetch.
    Returns deduplicated binary markets that have a probability set.
    """
    seen_ids: set[str] = set()
    results: list[dict] = []

    for title in titles:
        words     = [w.strip(".,?!:()") for w in title.split()]
        key_words = [w for w in words if w.lower() not in _STOP and len(w) > 2]
        term      = " ".join(key_words[:5])[:50].strip()
        if not term:
            continue

        try:
            resp = requests.get(
                "https://api.manifold.markets/v0/search-markets",
                params={
                    "term":         term,
                    "filter":       "open",
                    "contractType": "BINARY",
                    "limit":        results_per_query,
                },
                timeout=10,
            )
            resp.raise_for_status()
            for m in resp.json():
                mid = m.get("id")
                if mid and mid not in seen_ids and m.get("probability") is not None:
                    seen_ids.add(mid)
                    results.append(m)
        except Exception as e:
            print(f"  [ext] Manifold query failed for '{term[:30]}': {e}")

    return results


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
    except Exception as e:
        print(f"  [ext] PredictIt fetch failed: {e}")
        return []


# ── Normalize ─────────────────────────────────────────────────────────────────

def _norm_manifold(m: dict) -> dict:
    # Metaculus results arrive pre-normalized with "title"/"source" already set;
    # raw Manifold API responses use "question" and have no "source" key.
    return {
        "title":       m.get("question") or m.get("title", ""),
        "probability": float(m["probability"]),
        "volume":      float(m.get("volume") or 0),
        "source":      m.get("source", "Manifold"),
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

def build_index(manifold: list[dict], predictit: list[dict],
                odds: list[dict] | None = None) -> list[dict]:
    """
    Combines and normalizes markets from all sources into a single flat index.
    Each entry: {title, probability, volume, source, url}
    manifold: already-normalized dicts (Manifold + Metaculus pass through directly)
    predictit: raw PredictIt market dicts (normalized internally)
    odds: already-normalized OddsAPI dicts (pass through directly)
    """
    index = []

    for m in manifold:
        try:
            index.append(_norm_manifold(m))
        except Exception as e:
            print(f"  [ext] Skipping malformed Manifold entry: {e}")

    for m in predictit:
        try:
            index.extend(_norm_predictit(m))
        except Exception as e:
            print(f"  [ext] Skipping malformed PredictIt entry: {e}")

    for m in (odds or []):
        index.append(m)

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

    results_per_query = cfg.get("manifold_results_per_query", 8)
    meta_per_query    = cfg.get("metaculus_results_per_query", 6)

    titles = [m.get("title", "") for m in flagged_markets if m.get("title")]

    print(f"  [ext] Searching Manifold ({len(titles)} queries)...", end=" ", flush=True)
    manifold = _search_manifold(titles, results_per_query)
    print(f"{len(manifold)} binary markets")

    import os as _os
    if cfg.get("metaculus_enabled", True) and _os.getenv("METACULUS_API_TOKEN"):
        print(f"  [ext] Searching Metaculus ({len(titles)} queries)...", end=" ", flush=True)
        meta_markets = metaculus.fetch_for_titles(titles, meta_per_query)
        print(f"{len(meta_markets)} questions")
    else:
        meta_markets = []
        if cfg.get("metaculus_enabled", True):
            print("  [ext] Metaculus skipped (add METACULUS_API_TOKEN to .env)")

    print("  [ext] Fetching PredictIt...", end=" ", flush=True)
    predictit = _fetch_predictit()
    print(f"{len(predictit)} markets")

    odds_markets = []
    if cfg.get("odds_api_enabled", True):
        print("  [ext] Fetching OddsAPI...", end=" ", flush=True)
        events       = _odds_api.fetch_events(config)
        odds_markets = _odds_api.normalize_events(events)
        cached       = "(cached)" if _odds_api._cache_valid() else ""
        print(f"{len(odds_markets)//2} games {cached}".strip())

    index = build_index(manifold + meta_markets, predictit, odds_markets)
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
