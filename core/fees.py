"""
Kalshi + Polymarket per-trade fee calculators.

Both venues use the same shaped formula — a fee proportional to
price * (1 - price), maximal at maximum uncertainty (p=0.5) and zero at
the extremes — just with different rates. Kept in one module so
cross-venue gap calculations (backlog: cross-venue-expansion) net out a
consistent, comparable cost on both sides rather than modeling one
venue's cost precisely and ignoring the other's.

Kalshi source: docs.kalshi.com/api-docs, fee section. Fee rate: 7% applied
to price * (1 - price) per contract, rounded up to the nearest cent.
Verify this multiplier against current Kalshi docs before live trading —
Kalshi may update rates.

Polymarket source: Polymarket's 2026 category-based taker fee schedule
(fee = shares * category_rate * price * (1 - price); makers pay zero and
earn a rebate funded by taker fees — not modeled here, since Leviathan
never places real orders on either venue and a taker-fee assumption is
the conservative, worst-case cost to model for a signal). Verify current
rates against Polymarket's own fee docs before treating this as anything
more than a modeled approximation.
"""

import math

# Polymarket's 2026 taker-fee rate by its own market category. Live-verified
# 2026-09-04 (web research, not training-data recall): 0.04 for
# politics/finance/tech/mentions, 0.05 for sports/economics/culture/
# weather/other, 0.07 for crypto, 0.0 for geopolitics (free for everyone).
POLYMARKET_FEE_RATES = {
    "politics":    0.04,
    "finance":     0.04,
    "tech":        0.04,
    "mentions":    0.04,
    "sports":      0.05,
    "economics":   0.05,
    "culture":     0.05,
    "weather":     0.05,
    "other":       0.05,
    "crypto":      0.07,
    "geopolitics": 0.0,
}

# Kalshi's own category taxonomy (core/scanner.py, sources/accounts.py)
# doesn't match Polymarket's fee-category schema one-for-one — the two
# platforms categorize independently. This is an approximate mapping onto
# the closest Polymarket fee bucket for the same real-world event, not an
# exact cross-reference; anything unmapped falls back to "other" (0.05),
# the middle rate, rather than either extreme.
_KALSHI_TO_POLYMARKET_CATEGORY = {
    "sports":                  "sports",
    "politics":                "politics",
    "elections":               "politics",
    "entertainment":           "culture",
    "companies":               "finance",
    "financials":              "finance",
    "climate and weather":     "weather",
    "economics":               "economics",
    "crypto":                  "crypto",
    "science and technology":  "tech",
    "commodities":             "other",
}


def polymarket_category_fee_rate(kalshi_category: str | None) -> float:
    """
    Maps a Kalshi market's own category string onto Polymarket's fee-rate
    schedule. Case-insensitive; unrecognized or missing categories fall
    back to the "other" rate (0.05) rather than assuming free (geopolitics)
    or the most expensive tier (crypto).
    """
    key = (kalshi_category or "").strip().lower()
    poly_bucket = _KALSHI_TO_POLYMARKET_CATEGORY.get(key, "other")
    return POLYMARKET_FEE_RATES[poly_bucket]


def polymarket_fee(price: float, shares: int, kalshi_category: str | None = None) -> float:
    """
    Total Polymarket taker fee for `shares` shares at `price`, using the
    category-mapped rate (see polymarket_category_fee_rate). Assumes a
    taker (aggressive) order on both venues — the conservative, worst-case
    cost — since makers pay zero but Leviathan never places real orders on
    either venue to know which side a hypothetical trade would be.

    Formula: fee = shares * rate * price * (1 - price), rounded to the
    nearest cent (Polymarket's own docs don't specify Kalshi-style ceiling
    rounding, so plain rounding is used here rather than assuming one).
    """
    if price <= 0 or price >= 1 or shares <= 0:
        return 0.0
    rate = polymarket_category_fee_rate(kalshi_category)
    return round(rate * price * (1.0 - price) * shares, 2)


def kalshi_fee(price: float, contracts: int) -> float:
    """
    Total Kalshi execution fee for `contracts` contracts at `price`.

    Formula: fee = ceil(0.07 * price * (1 - price) * contracts * 100) / 100

    The * 100 / 100 converts to cents-then-back-to-dollars with ceiling rounding,
    so the result is always a whole-cent amount >= 0.

    Args:
        price: Contract price in [0, 1] (e.g. 0.30 for a 30¢ YES contract)
        contracts: Number of contracts (Leviathan uses unit_size from config)

    Returns:
        Total fee in dollars (e.g. 0.15 for 10 contracts at p=0.30)
    """
    if price <= 0 or price >= 1 or contracts <= 0:
        return 0.0
    raw = 0.07 * price * (1.0 - price) * contracts * 100
    return math.ceil(raw) / 100
