"""
Polymarket US integration — cross-references Kalshi flagged markets against
Polymarket US (a separate, CFTC-regulated product from international
Polymarket, distinct domain/backend) to detect pricing gaps.

Uses the public Markets API at gateway.polymarket.us (no auth required —
confirmed live 2026-09-14: the "Get Markets" endpoint's OpenAPI spec
declares `security: []`). POLYMARKET_KEY_ID/POLYMARKET_SECRET_KEY in .env
are NOT used here and are not needed for this module -- that credential
only authenticates trading/portfolio/WebSocket endpoints per Polymarket
US's own docs, none of which this module touches. Kept unused rather than
wired in to avoid any path that could authenticate a trading call from a
project whose whole design is read-only/paper-trading.

Schema differs from international Polymarket's outcomes/outcomePrices
arrays: each market has a `marketSides` array, one entry per side, each
with a `description` (e.g. "Yes"/"No" for binary questions, or a team
name for sports moneylines) and a `price` (string, 0-1). Genuinely binary
Yes/No markets (weather, politics, economics) match Kalshi's framing
directly; team-vs-team sports markets fall back to "first side" the same
way international Polymarket's _yes_price() does for non-Yes/No outcomes
-- an existing, accepted limitation, not new here.
"""

import math
import re
import requests
from difflib import SequenceMatcher

from core import fees

GATEWAY_BASE = "https://gateway.polymarket.us"

#: backlog: polymarket-us-tuning-for-real-value, direction (2)'s other half
#: (the min_match_score half already shipped 2026-09-14). A short,
#: generic normalized title (e.g. "Both Teams To Score" -> {both, teams,
#: score}, 3 words after stopword removal) has a small enough word set
#: that a coincidental overlap -- or SequenceMatcher's character-level
#: ratio, which is noisier on short strings -- can clear min_match_score
#: purely by chance, not because the two markets are actually the same
#: question. This was the exact real failure mode that made max_fetch
#: 300->1500 surface 3 spurious matches (2026-09-14 review) before
#: category restriction removed that specific sports-feed source of
#: short titles. Set to 4, not 3: "Both Teams To Score" itself normalizes
#: to exactly 3 words, so the floor must exceed that specific documented
#: example, not just be a round number. Gating on word count (not
#: character length) below _match_score's Jaccard/SequenceMatcher split,
#: so it protects both components at once, and applies regardless of
#: which side (Kalshi or Polymarket US) has the short title.
MIN_TITLE_WORDS = 4


def _fetch_markets_page(limit: int, category: str | None) -> list[dict]:
    """One category's worth of active, open markets, paginated internally."""
    markets = []
    offset  = 0

    while len(markets) < limit:
        params = {
            "limit":  min(100, limit - len(markets)),
            "offset": offset,
            "active": "true",
            "closed": "false",
        }
        if category:
            params["categories"] = category
        try:
            resp = requests.get(f"{GATEWAY_BASE}/v1/markets", params=params, timeout=15)
            resp.raise_for_status()
            page = resp.json().get("markets", [])
        except Exception as e:
            print(f"  [poly_us] fetch_markets failed at offset {offset}"
                  f"{f' (category={category})' if category else ''}: {e}")
            break

        if not page:
            break
        markets.extend(page)
        if len(page) < 100:
            break
        offset += 100

    return markets[:limit]


def fetch_markets(limit: int = 300, categories: list[str] | None = None) -> list[dict]:
    """
    Fetches active, open Polymarket US markets with embedded prices.

    categories (backlog: polymarket-us-tuning-for-real-value): the
    gateway API's own `categories` param does NOT accept a comma-
    separated list (confirmed live 2026-09-14 -- "climate,politics"
    returns zero rows, it's matched as one literal category string, not
    an OR filter) -- one request per category, merged and deduped by
    market id. None (default) fetches the uncategorized general feed,
    same as before this parameter existed.

    Why this matters: the uncategorized feed is sports-dominated
    (confirmed live: a 200-market uncategorized sample was 100%
    "sports"), which barely overlaps with what Leviathan actually flags
    (weather, politics, entertainment). Restricting to the categories
    that genuinely exist here and plausibly overlap Kalshi's own
    coverage -- climate, politics, economics -- surfaces real candidate
    matches instead of spending the whole fetch budget on team names
    that will never match a Kalshi title.
    """
    if not categories:
        return _fetch_markets_page(limit, None)

    per_category = max(1, limit // len(categories))
    seen_ids: set[str] = set()
    markets: list[dict] = []
    for cat in categories:
        for m in _fetch_markets_page(per_category, cat):
            mid = str(m.get("id", ""))
            if mid and mid in seen_ids:
                continue
            seen_ids.add(mid)
            markets.append(m)
    return markets[:limit]


def _yes_price(market: dict) -> float | None:
    """
    Extract the YES probability (0.0–1.0) from a Polymarket US market object.

    Confirmed live 2026-09-14: a side's own "price" key is sometimes
    entirely absent/null on one side while present on the other (e.g. Yes
    price=None, No price="0.02") -- not a malformed response, just how
    this API represents some markets. Falls back to 1 - No's price before
    giving up, rather than dropping the market from the index outright.
    """
    try:
        sides = market.get("marketSides")
        if not sides:
            return None

        yes_price, no_price = None, None
        for side in sides:
            desc = str(side.get("description", "")).lower()
            price = side.get("price")
            if price is None:
                continue
            if desc in ("yes", "true", "1"):
                yes_price = float(price)
            elif desc in ("no", "false", "0"):
                no_price = float(price)

        if yes_price is not None:
            return yes_price
        if no_price is not None:
            return round(1.0 - no_price, 4)

        # Fallback: assume first side is YES (matches international
        # Polymarket's _yes_price() convention for non-Yes/No outcomes,
        # e.g. team-vs-team sports moneylines) -- only reached when
        # neither side is labeled Yes/No at all.
        return float(sides[0]["price"])
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

    Returns 0.0 (never matches) when either title's normalized word count
    is below MIN_TITLE_WORDS -- see that constant's own comment for why.
    """
    ka = _normalize(kalshi_title)
    pa = _normalize(poly_title)
    if len(ka) < MIN_TITLE_WORDS or len(pa) < MIN_TITLE_WORDS:
        return 0.0
    jaccard = len(ka & pa) / len(ka | pa) if (ka | pa) else 0.0
    seq     = SequenceMatcher(None, kalshi_title.lower(), poly_title.lower()).ratio()
    return max(jaccard, seq * 0.9)  # slight discount on sequence to prefer word overlap


def build_index(poly_markets: list[dict]) -> list[dict]:
    """
    Pre-processes the Polymarket US market list into a lean index for fast matching.
    Extracts the YES price up front so we don't re-parse per comparison.

    Confirmed live 2026-09-14: Polymarket US repeats the identical
    `question` string across every market in a "family" -- every
    temperature-band market for a given city/date shares one `question`
    ("Highest temperature in Los Angeles on September 13?"), and every
    per-candidate election market shares one too ("Kansas Governor
    Election Winner"). Only `title`/`titleShort` (e.g. "75 or below",
    "Cindy Holscher (D)") identifies which specific market it is. Using
    `question` alone (the original, unreviewed version of this function)
    meant every market in a family scored identically against any given
    Kalshi title, so find_match() would silently return an arbitrary
    band/candidate rather than a real match -- caught in review before
    this was ever enabled, not live. Combining question + title fixes
    this the same way Kalshi's own titles for equivalent ladder markets
    already embed the band directly in the title text.
    """
    index = []
    for m in poly_markets:
        base_question = (m.get("question") or "").strip()
        title         = (m.get("title") or "").strip()
        if title and title.lower() not in base_question.lower():
            question = f"{base_question} — {title}" if base_question else title
        else:
            question = base_question
        if not question:
            continue
        price = _yes_price(m)
        if price is None:
            continue
        index.append({
            "question":   question,
            "slug":       m.get("slug", ""),
            "market_id":  str(m.get("id", "")),
            "volume":     float(m.get("volume") or 0),
            "yes_price":  price,
        })
    return index


def find_match(kalshi_title: str, index: list[dict], threshold: float = 0.50) -> dict | None:
    """Returns the best-matching Polymarket US market above the threshold, or None."""
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
    Fetches active Polymarket US markets and returns a pre-built matching index.
    Call once per run; pass the result to match_markets() to avoid double fetching.

    config.polymarket_us.categories (backlog: polymarket-us-tuning-for-
    real-value): optional list, e.g. ["climate", "politics", "economics"].
    Empty/absent (default) preserves the original uncategorized-feed
    behavior for any existing caller.
    """
    cfg        = config.get("polymarket_us", {})
    limit      = cfg.get("max_fetch", 300)
    categories = cfg.get("categories") or None
    return build_index(fetch_markets(limit, categories=categories))


def match_markets(
    markets: list[dict],
    index: list[dict],
    config: dict,
    *,
    min_gap: float | None = None,
    min_match_score: float | None = None,
) -> dict[str, dict]:
    """
    Matches a list of Kalshi markets against a pre-built Polymarket US index.
    Returns a dict keyed by Kalshi ticker.

    min_gap and min_match_score override config values when provided.

    Result per ticker:
      poly_us_question, poly_us_price, poly_us_slug, market_id,
      match_score, price_gap, net_price_gap

    Mirrors sources/polymarket.py's match_markets() exactly, including
    net_price_gap's fee-shrinkage treatment (core.fees.kalshi_fee/
    polymarket_fee at config.betting.unit_size) -- purely informational,
    never changes price_gap itself or any flag/promotion threshold, which
    all key off the raw price_gap.
    """
    cfg       = config.get("polymarket_us", {})
    threshold = min_match_score if min_match_score is not None else cfg.get("min_match_score", 0.50)
    gap_floor = min_gap         if min_gap         is not None else cfg.get("min_price_gap",   0.0)
    unit_size = config.get("betting", {}).get("unit_size", 10)

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
        poly_price = match["yes_price"]
        price_gap  = (poly_price - kalshi_mid) if kalshi_mid is not None else None

        if gap_floor > 0 and price_gap is None:
            continue
        if price_gap is not None and abs(price_gap) < gap_floor:
            continue

        net_price_gap = None
        if price_gap is not None and unit_size > 0:
            k_fee = fees.kalshi_fee(kalshi_mid, unit_size)
            p_fee = fees.polymarket_fee(poly_price, unit_size, m.get("category"))
            total_fee_pp = (k_fee + p_fee) / unit_size
            shrunk = max(abs(price_gap) - total_fee_pp, 0.0)
            net_price_gap = round(math.copysign(shrunk, price_gap), 4) if price_gap != 0 else 0.0

        results[ticker] = {
            "poly_us_question": match["question"],
            "poly_us_price":    poly_price,
            "poly_us_slug":     match["slug"],
            "market_id":        match["market_id"],
            "match_score":      match["match_score"],
            "price_gap":        round(price_gap, 4) if price_gap is not None else None,
            "net_price_gap":    net_price_gap,
        }

    return results


def enrich_flagged(flagged_markets: list[dict], config: dict) -> dict[str, dict]:
    """
    Entry point for main.py: fetches Polymarket US data once and matches
    against all flagged Kalshi markets. Prefer fetch_and_build_index() +
    match_markets() when you need to reuse the index (e.g. also running
    cross-market promotion against the unflagged pool).
    """
    index = fetch_and_build_index(config)
    return match_markets(flagged_markets, index, config)
