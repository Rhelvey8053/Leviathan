"""
Kalshi per-trade fee calculator.

Kalshi charges a fee on contract execution based on the contract price.
The fee schedule uses a variance-based formula: higher fees near p=0.5
(maximum uncertainty) tapering to zero at p=0 or p=1.

Source: Kalshi fee schedule (docs.kalshi.com/api-docs, fee section).
Fee rate: 7% applied to price * (1 - price) per contract, rounded up to
the nearest cent. Verify this multiplier against current Kalshi docs before
live trading — Kalshi may update rates.
"""

import math


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
