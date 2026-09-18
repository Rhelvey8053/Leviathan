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
            "question":      question,
            "base_question": base_question,
            "raw_title":      title,
            "slug":          m.get("slug", ""),
            "market_id":     str(m.get("id", "")),
            "volume":        float(m.get("volume") or 0),
            "yes_price":     price,
        })
    return index


def _kalshi_match_title(m: dict) -> str:
    """
    Combines a Kalshi market's event_title with its own title for MATCHING
    purposes only -- never mutates m["title"] itself, which downstream
    consumers (signals persistence, dashboard, calibration) need to stay
    exactly the specific-band title Kalshi gave it, not this enriched
    version. Mirrors build_index()'s question+title combination on the
    Polymarket side of the identical problem (see that function's own
    docstring): a market's own title for a ladder/band family often
    doesn't carry what actually identifies the real-world question --
    for Kalshi weather markets specifically, that's the city, which lives
    only on the event's own title (see
    core.kalshi.attach_event_category_metadata's docstring, which is what
    populates event_title -- absent on any market that never went through
    it, in which case this falls back to the bare title, never worse than
    before this fix existed).
    """
    event_title = (m.get("event_title") or "").strip()
    title       = (m.get("title") or "").strip()
    if event_title and title and title.lower() not in event_title.lower():
        return f"{event_title} — {title}"
    return event_title or title


# ── Ladder/band numeric matching (backlog: polymarket-us-tuning-for-real-
# value, 2026-09-18) ─────────────────────────────────────────────────────────
#
# event_title fixed WHICH city/date a Kalshi ladder market belongs to, but
# not WHICH BAND within it -- confirmed live that combining event_title with
# the band-specific title still doesn't reliably clear either platform's
# real match_score threshold, because the two platforms format a band as
# very different text ("82-83°" vs "82 to 83") that word/character overlap
# can't reliably bridge. The actual band boundary is a NUMBER, so parse it
# as one and compare numbers, not more text-similarity tuning.
#
# Deliberately EXACT-only, never overlap/nearest-bucket: live-checked across
# every US city both platforms currently cover for "highest temperature" on
# the same date (2026-09-18) -- Chicago's boundaries align exactly between
# the two platforms (69/71/73/75/77), but Los Angeles and San Francisco's are
# offset by 1 degree (Kalshi's are even numbers, Polymarket US's are odd, for
# reasons neither platform documents). A Kalshi [72,74) band and a Polymarket
# US [73,75) band describe DIFFERENT real-world outcomes -- pairing them on
# "close enough" overlap would compute a price_gap between two different
# questions, a fabricated signal, not a smaller version of a real one.
# Returning no match for an offset city is the correct output, not a
# shortfall to work around.

_KALSHI_BAND_ABOVE_RE = re.compile(r"[>≥]\s*(-?\d+(?:\.\d+)?)")
_KALSHI_BAND_BELOW_RE  = re.compile(r"[<≤]\s*(-?\d+(?:\.\d+)?)")
_KALSHI_BAND_RANGE_RE  = re.compile(r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)")

_POLY_BAND_ABOVE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s+or\s+(?:above|more|higher)", re.I)
_POLY_BAND_BELOW_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s+or\s+(?:below|less|lower)", re.I)
_POLY_BAND_RANGE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s+to\s+(-?\d+(?:\.\d+)?)", re.I)


def parse_kalshi_band(title: str) -> "tuple[float, float] | None":
    """
    Parses a Kalshi ladder-market title's numeric band into (low, high),
    using math.inf for an open end. Handles the three formats confirmed
    live 2026-09-18 against real KXHIGH*/KXLOWT* titles: ">X"/"≥X"
    (open above), "<X"/"≤X" (open below), "X-Y" (bounded range, e.g.
    "Will the maximum temperature be 82-83° on Sep 18, 2026?"). Checks
    above/below before range so a range regex can't accidentally consume
    part of a ">"/"<" expression first. Returns None (never a fabricated
    bound) when no recognizable band pattern is found -- a non-ladder
    market, or a format not seen yet; callers must treat that as "can't
    compare numerically."
    """
    m = _KALSHI_BAND_ABOVE_RE.search(title)
    if m:
        return (float(m.group(1)), math.inf)
    m = _KALSHI_BAND_BELOW_RE.search(title)
    if m:
        return (-math.inf, float(m.group(1)))
    m = _KALSHI_BAND_RANGE_RE.search(title)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (lo, hi) if lo <= hi else (hi, lo)
    return None


def parse_poly_us_band(title: str) -> "tuple[float, float] | None":
    """
    Parses a Polymarket US ladder-market title's numeric band into (low,
    high), using math.inf for an open end. Handles the three formats
    confirmed live 2026-09-18 against real Polymarket US temperature-band
    titles: "X or above/more/higher" (open above), "X or below/less/lower"
    (open below), "X to Y" (bounded range). Same never-fabricate-a-bound
    contract as parse_kalshi_band().
    """
    m = _POLY_BAND_ABOVE_RE.search(title)
    if m:
        return (float(m.group(1)), math.inf)
    m = _POLY_BAND_BELOW_RE.search(title)
    if m:
        return (-math.inf, float(m.group(1)))
    m = _POLY_BAND_RANGE_RE.search(title)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (lo, hi) if lo <= hi else (hi, lo)
    return None


def _group_by(items: list[dict], key_fn) -> dict:
    groups: dict = {}
    for item in items:
        k = key_fn(item)
        if k:
            groups.setdefault(k, []).append(item)
    return groups


_TEMP_METRIC_RE = re.compile(r"\b(highest|maximum|lowest|minimum)\b", re.I)


def _temp_metric(text: str) -> "str | None":
    """
    Returns "high" or "low" for a temperature-family title/question that
    names its metric, else None (non-temperature families, e.g. elections,
    are never gated by this -- they simply have no metric word to find).

    Added after live-testing match_ladder_markets against real data
    2026-09-18 found "Lowest temperature in San Francisco on Sep 18,
    2026?" scores 0.737 against "Highest temperature in San Francisco on
    September 18?" -- only 0.013 below the 0.75 family_threshold, because
    swapping one metric word in an otherwise-identical template barely
    moves a generic word/character similarity score. That's too thin a
    margin to trust for keeping "highest" and "lowest" families apart --
    a fuzzy-score-only gate could silently pair a low-temperature Kalshi
    market against a high-temperature Polymarket US band, a wrong-metric
    match that's arguably worse than the wrong-city case the 0.75
    threshold was raised for (see match_ladder_markets' own docstring).
    Checked explicitly instead of trusted to the fuzzy score's margin.
    """
    m = _TEMP_METRIC_RE.search(text)
    if not m:
        return None
    return "high" if m.group(1).lower() in ("highest", "maximum") else "low"


def match_ladder_markets(
    kalshi_markets: list[dict], index: list[dict], config: dict,
    *, family_threshold: float = 0.75,
) -> dict[str, dict]:
    """
    Two-stage matcher for ladder/band market families. Stage 1: groups
    Kalshi markets by event_ticker and Polymarket US index entries by
    base_question, then finds the best-scoring Polymarket US family for
    each Kalshi event using event_title vs base_question ALONE (no band
    text) -- confirmed live this scores far higher (~0.7-0.8) than
    combining in the band-specific title (~0.55-0.57, see
    _kalshi_match_title's own docstring), because the family-identifying
    text (city + date) is exactly what both sides share, and appending
    band text only dilutes that. Stage 2: within a matched family, pairs
    individual markets ONLY when parse_kalshi_band()/parse_poly_us_band()
    return EXACTLY equal (low, high) tuples -- see the module comment
    above this function for why exact, not nearest/overlapping.

    family_threshold defaults to 0.75, not the generic 0.6 used for
    fuzzy-text matches elsewhere in this module. Measured directly
    2026-09-18 across 6 real US cities' "Highest temperature in <city> on
    <date>?" event titles: same-city pairs (Kalshi's "Sep 18, 2026"
    phrasing vs Polymarket US's "September 18" phrasing) score 0.78-0.80,
    while every cross-city pair among those 6 cities tops out at 0.704
    (Chicago vs Miami) -- because the shared template words ("Highest
    temperature in ... on ... 18?") dominate a generic word/character
    similarity score and the city name is only one differentiating token,
    the ordinary 0.6 threshold would have matched Chicago's Kalshi event
    to Los Angeles's or Miami's Polymarket US family (both score >0.6),
    silently pairing two different cities' prices. 0.75 sits with margin
    on both sides of the measured gap; it is NOT a proven bound for every
    possible city name pair, only verified safe for the six tested.

    A Kalshi market with no event_ticker, or whose band doesn't parse, or
    whose event has no confidently-matching Polymarket US family, is
    simply absent from the result -- never a guessed pairing. Returns a
    dict keyed by Kalshi ticker, same shape as match_markets()'s per-
    ticker result (poly_us_question, poly_us_price, poly_us_slug,
    market_id, match_score, price_gap, net_price_gap) so it's a drop-in
    input to the same downstream consumers.
    """
    cfg       = config.get("polymarket_us", {})
    gap_floor = cfg.get("min_price_gap", 0.0)
    unit_size = config.get("betting", {}).get("unit_size", 10)

    kalshi_by_event = _group_by(kalshi_markets, lambda m: m.get("event_ticker", ""))
    poly_families   = _group_by(index, lambda e: e.get("base_question", ""))
    family_keys     = list(poly_families.keys())

    results: dict[str, dict] = {}
    for event_ticker, event_markets in kalshi_by_event.items():
        event_title = next((m.get("event_title", "") for m in event_markets if m.get("event_title")), "")
        if not event_title:
            continue

        event_metric = _temp_metric(event_title)

        best_family_key = None
        best_family_score = 0.0
        for fk in family_keys:
            if event_metric is not None and _temp_metric(fk) not in (None, event_metric):
                continue
            score = _match_score(event_title, fk)
            if score > best_family_score:
                best_family_score = score
                best_family_key   = fk
        if best_family_key is None or best_family_score < family_threshold:
            continue

        poly_bands = []  # (band, entry) for this family, parsed once per event
        for entry in poly_families[best_family_key]:
            band = parse_poly_us_band(entry["raw_title"])
            if band is not None:
                poly_bands.append((band, entry))

        for m in event_markets:
            ticker = m.get("ticker", "")
            kalshi_band = parse_kalshi_band(m.get("title", ""))
            if kalshi_band is None:
                continue
            match_entry = next((entry for band, entry in poly_bands if band == kalshi_band), None)
            if match_entry is None:
                continue

            kalshi_mid = m.get("mid_price")
            poly_price = match_entry["yes_price"]
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
                "poly_us_question": match_entry["question"],
                "poly_us_price":    poly_price,
                "poly_us_slug":     match_entry["slug"],
                "market_id":        match_entry["market_id"],
                "match_score":      round(best_family_score, 3),
                "price_gap":        round(price_gap, 4) if price_gap is not None else None,
                "net_price_gap":    net_price_gap,
            }

    return results


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

    Ladder/band markets (backlog: polymarket-us-tuning-for-real-value,
    2026-09-18 numeric-band follow-up) are tried first via
    match_ladder_markets() -- family match on event_title alone plus an
    EXACT numeric-band pairing, which is the only reliable path for these
    markets (see match_ladder_markets' own docstring for why). Any ticker
    it resolves is used as-is and skipped in the fuzzy-text pass below;
    every other ticker (non-ladder markets like elections, or ladder
    markets whose band/family didn't resolve) falls through to the
    original find_match()/_kalshi_match_title() text path unchanged.
    """
    cfg       = config.get("polymarket_us", {})
    threshold = min_match_score if min_match_score is not None else cfg.get("min_match_score", 0.50)
    gap_floor = min_gap         if min_gap         is not None else cfg.get("min_price_gap",   0.0)
    unit_size = config.get("betting", {}).get("unit_size", 10)

    results = match_ladder_markets(markets, index, config)

    for m in markets:
        ticker = m.get("ticker", "")
        if ticker in results:
            continue
        title  = m.get("title", "")
        if not title:
            continue

        match = find_match(_kalshi_match_title(m), index, threshold)
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
