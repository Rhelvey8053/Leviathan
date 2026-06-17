"""
Polymarket integration — cross-references Kalshi flagged markets against
Polymarket to detect pricing gaps.

Uses the public Gamma API (no auth required). Prices are embedded in each
market object, so one bulk fetch gives us everything we need.
"""

import json
import re
import requests
from difflib import SequenceMatcher

GAMMA_BASE = "https://gamma-api.polymarket.com"


def fetch_markets(limit: int = 500) -> list[dict]:
    """
    Fetches active, open Polymarket markets with embedded prices.
    Returns a flat list — one call, paginated as needed.
    """
    markets = []
    offset  = 0

    while len(markets) < limit:
        try:
            resp = requests.get(
                f"{GAMMA_BASE}/markets",
                params={
                    "limit":  min(100, limit - len(markets)),
                    "offset": offset,
                    "active": "true",
                    "closed": "false",
                },
                timeout=15,
            )
            resp.raise_for_status()
            page = resp.json()
        except Exception as e:
            print(f"  [poly] fetch_markets failed at offset {offset}: {e}")
            break

        if not page:
            break
        markets.extend(page)
        if len(page) < 100:
            break
        offset += 100

    return markets[:limit]


def _yes_price(market: dict) -> float | None:
    """Extract the YES probability (0.0–1.0) from a Polymarket market object."""
    try:
        outcomes = market.get("outcomes")
        prices   = market.get("outcomePrices")

        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        if isinstance(prices, str):
            prices = json.loads(prices)

        if not outcomes or not prices:
            return None

        for i, outcome in enumerate(outcomes):
            if str(outcome).lower() in ("yes", "true", "1"):
                return float(prices[i])

        # Fallback: assume first outcome is YES
        return float(prices[0])
    except Exception:
        return None


def _normalize(title: str) -> set[str]:
    """Normalize a market title to a word set for Jaccard matching."""
    stopwords = {"will", "the", "a", "an", "in", "by", "on", "of", "to",
                 "be", "is", "are", "at", "for", "and", "or", "than", "that"}
    words = re.sub(r"[^a-z0-9\s]", "", title.lower()).split()
    return {w for w in words if w not in stopwords and len(w) > 1}


def _match_score(kalshi_title: str, poly_title: str) -> float:
    """
    Combined similarity score using Jaccard word overlap and sequence ratio.
    Jaccard handles word-order differences; sequence ratio catches paraphrases.
    """
    ka = _normalize(kalshi_title)
    pa = _normalize(poly_title)
    jaccard = len(ka & pa) / len(ka | pa) if (ka | pa) else 0.0
    seq     = SequenceMatcher(None, kalshi_title.lower(), poly_title.lower()).ratio()
    return max(jaccard, seq * 0.9)  # slight discount on sequence to prefer word overlap


def build_index(poly_markets: list[dict]) -> list[dict]:
    """
    Pre-processes the Polymarket market list into a lean index for fast matching.
    Extracts the YES price up front so we don't re-parse per comparison.
    """
    index = []
    for m in poly_markets:
        question = (m.get("question") or m.get("title") or "").strip()
        if not question:
            continue
        price = _yes_price(m)
        if price is None:
            continue
        index.append({
            "question":   question,
            "slug":       m.get("slug", ""),
            "condition_id": m.get("conditionId", ""),
            "volume":     float(m.get("volume") or 0),
            "yes_price":  price,
        })
    return index


def find_match(kalshi_title: str, index: list[dict], threshold: float = 0.50) -> dict | None:
    """Returns the best-matching Polymarket market above the threshold, or None."""
    best_score = 0.0
    best       = None
    for pm in index:
        score = _match_score(kalshi_title, pm["question"])
        if score > best_score:
            best_score = score
            best       = pm
    if best and best_score >= threshold:
        return {**best, "match_score": round(best_score, 3)}
    return None


def fetch_and_build_index(config: dict) -> list[dict]:
    """
    Fetches active Polymarket markets and returns a pre-built matching index.
    Call once per run; pass the result to match_markets() to avoid double fetching.
    """
    cfg   = config.get("polymarket", {})
    limit = cfg.get("max_fetch", 500)
    return build_index(fetch_markets(limit))


def match_markets(
    markets: list[dict],
    index: list[dict],
    config: dict,
    *,
    min_gap: float | None = None,
    min_match_score: float | None = None,
) -> dict[str, dict]:
    """
    Matches a list of Kalshi markets against a pre-built Polymarket index.
    Returns a dict keyed by Kalshi ticker.

    min_gap and min_match_score override config values when provided.

    Result per ticker:
      poly_question, poly_price, poly_slug, condition_id, match_score, price_gap
    """
    cfg       = config.get("polymarket", {})
    threshold = min_match_score if min_match_score is not None else cfg.get("min_match_score", 0.50)
    gap_floor = min_gap         if min_gap         is not None else cfg.get("min_price_gap",   0.0)

    results = {}
    for m in markets:
        ticker = m.get("ticker", "")
        title  = m.get("title", "")
        if not title:
            continue

        match = find_match(title, index, threshold)
        if not match:
            continue

        kalshi_mid = m.get("mid_price")
        price_gap  = (match["yes_price"] - kalshi_mid) if kalshi_mid is not None else None

        if price_gap is not None and abs(price_gap) < gap_floor:
            continue

        results[ticker] = {
            "poly_question": match["question"],
            "poly_price":    match["yes_price"],
            "poly_slug":     match["slug"],
            "condition_id":  match["condition_id"],
            "match_score":   match["match_score"],
            "price_gap":     round(price_gap, 4) if price_gap is not None else None,
        }

    return results


def enrich_flagged(flagged_markets: list[dict], config: dict) -> dict[str, dict]:
    """
    Backward-compatible entry point called from main.py.
    Fetches Polymarket data once and matches against all flagged Kalshi markets.
    Prefer fetch_and_build_index() + match_markets() when you need to reuse the index.
    """
    index = fetch_and_build_index(config)
    return match_markets(flagged_markets, index, config)
