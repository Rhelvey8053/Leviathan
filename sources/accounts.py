"""
Smart money tracking via Polymarket's public on-chain data.

Discovers winning wallets, enriches them with profile data (name, win rate,
active markets), caches them, and scans matched markets for their positioning.
"""

import json
import os
import time
import requests

DATA_API   = "https://data-api.polymarket.com"
POLY_URL   = "https://polymarket.com/profile"
_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_FILE = os.path.join(_ROOT, "data", "winning_accounts.json")


# ── API helpers ───────────────────────────────────────────────────────────────

def _get(path: str, params: dict = None, timeout: int = 12) -> list | dict | None:
    try:
        resp = requests.get(f"{DATA_API}/{path}", params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def fetch_recent_trades(limit: int = 300) -> list[dict]:
    result = _get("trades", {"limit": limit})
    return result if isinstance(result, list) else []


def fetch_user_trades(address: str, limit: int = 100) -> list[dict]:
    result = _get("trades", {"user": address, "limit": limit})
    return result if isinstance(result, list) else []


def fetch_user_positions(address: str) -> list[dict]:
    result = _get("positions", {"user": address, "limit": 500})
    return result if isinstance(result, list) else []


# ── Profile enrichment ────────────────────────────────────────────────────────

def _extract_profile(trades: list[dict], address: str) -> dict:
    """
    Extracts display name, pseudonym, and profile URL from trade records.
    Polymarket embeds name/pseudonym directly in trade responses.
    Falls back to truncated address if no name set.
    """
    for t in trades:
        if t.get("proxyWallet", "").lower() == address.lower():
            name      = (t.get("name") or "").strip()
            pseudonym = (t.get("pseudonym") or "").strip()
            display   = name or pseudonym or ""
            return {
                "display_name": display,
                "name":         name,
                "pseudonym":    pseudonym,
                "profile_url":  f"{POLY_URL}/{address}",
            }
    return {
        "display_name": "",
        "name":         "",
        "pseudonym":    "",
        "profile_url":  f"{POLY_URL}/{address}",
    }


def _score_wallet(positions: list[dict]) -> dict | None:
    """
    Scores a wallet on their RESOLVED position performance only.

    Coin-flip markets (sub-daily crypto tick bets) and sports-game bets are
    excluded from all scoring — they test luck, not forecasting skill.
    Ranking metrics (win_rate, resolved_cash_pnl) come from resolved positions
    only (redeemable=True), so a wallet with 0 resolved positions cannot qualify.
    """
    if not positions:
        return None

    # Lazy import to avoid circular dependency; _is_sports_title is a pure predicate
    try:
        from analysis.smart_money_scan import _is_sports_title as _sports
    except ImportError:
        _sports = lambda t: False  # noqa: E731

    resolved_pct_pnls  = []  # pct PnL for resolved (redeemable) positions only
    resolved_cash_pnls = []  # cash PnL for resolved positions only
    active_mkts        = []

    for p in positions:
        try:
            pct   = float(p.get("percentPnl") or 0)
            cash  = float(p.get("cashPnl")    or 0)
            title = (p.get("title") or "").strip()

            # Exclude coin-flip and sports-game markets — P&L here is noise, not skill
            if _is_coinflip(title) or _sports(title):
                continue

            slug    = (p.get("eventSlug") or p.get("slug") or "").strip()
            outcome = (p.get("outcome") or "").strip()
            if title and not p.get("redeemable"):
                active_mkts.append({
                    "title":   title,
                    "slug":    slug,
                    "outcome": outcome,
                    "pct_pnl": pct,
                    "url":     f"https://polymarket.com/event/{slug}" if slug else "",
                })

            if p.get("redeemable"):
                resolved_pct_pnls.append(pct)
                resolved_cash_pnls.append(cash)
        except (TypeError, ValueError):
            pass

    wins     = sum(1 for p in resolved_pct_pnls if p > 0)
    n_res    = len(resolved_pct_pnls)
    win_rate = round(wins / n_res * 100, 1) if n_res else None

    active_mkts.sort(key=lambda m: abs(m["pct_pnl"]), reverse=True)

    return {
        "position_count":      len(positions),
        "resolved_count":      n_res,
        "resolved_avg_pct_pnl": round(sum(resolved_pct_pnls) / n_res, 2) if n_res else None,
        "resolved_cash_pnl":   round(sum(resolved_cash_pnls), 2),
        "win_rate":            win_rate,
        "active_markets":      active_mkts[:5],
    }


_COINFLIP_PATTERNS = [
    "up or down", "up/down", "bitcoin up", "btc up", "eth up",
    " 5m", " 1m", " 10m", " 15m", "price up", "price down",
    "higher or lower", "above or below",
]

def _is_coinflip(title: str) -> bool:
    t = title.lower()
    return any(p in t for p in _COINFLIP_PATTERNS)


#: Gate evaluation order for _classify_wallet, ending in the terminal "PASS"
#: state. Used both to attribute a wallet's death stage and, by index
#: comparison, to determine whether a wallet reached/survived any given gate.
GATE_ORDER = ["resolved_count", "win_rate", "position_count", "pct_pnl", "cash_pnl", "PASS"]


def _classify_wallet(stats: dict, config: dict) -> str:
    """
    Returns the name of the first gate a wallet fails, in GATE_ORDER, or
    "PASS" if it clears every gate. _is_winner is defined in terms of this
    (== "PASS"); the diagnostic funnel (diagnose_discovery) uses the same
    classification so the two can never silently disagree.

    DIAGNOSTIC-ONLY DECOMPOSITION: the original gate 2 was a single combined
    `position_count AND pct_pnl AND cash_pnl` boolean. It is decomposed here
    into three ordered checks (position_count -> pct_pnl -> cash_pnl) so a
    failing wallet can be attributed to exactly one of the three. An AND is
    order-independent for the final boolean, so this decomposition does NOT
    change PASS/FAIL — only the attributed "reason" depends on the order,
    which is defined explicitly here.

    Config keys read:
      accounts.min_resolved_count  — floor on resolved positions (default 10)
      accounts.min_win_rate        — resolved win rate floor, % (default 55.0)
      accounts.min_positions       — total position count floor (default 5)
      accounts.min_pct_pnl         — resolved avg % PnL floor (default 10.0)
      accounts.min_cash_pnl        — resolved cash PnL floor in $ (default 100;
                                      $25 is trivially achievable on luck alone)
    """
    cfg = config.get("accounts", {})
    min_resolved  = cfg.get("min_resolved_count", 10)
    min_win_rate  = cfg.get("min_win_rate", 55.0)
    min_positions = cfg.get("min_positions", 5)
    min_pct_pnl   = cfg.get("min_pct_pnl", 10.0)
    min_cash_pnl  = cfg.get("min_cash_pnl", 100.0)

    # Gate 1: must have a verified track record on resolved, non-coinflip markets
    if stats["resolved_count"] < min_resolved:
        return "resolved_count"
    if stats["win_rate"] is None or stats["win_rate"] < min_win_rate:
        return "win_rate"

    # Gate 2 (decomposed): resolved metrics only (not open-position unrealised P&L)
    resolved_avg_pct = stats.get("resolved_avg_pct_pnl")
    resolved_cash    = stats.get("resolved_cash_pnl", 0.0) or 0.0

    if stats["position_count"] < min_positions:
        return "position_count"
    if resolved_avg_pct is None or resolved_avg_pct < min_pct_pnl:
        return "pct_pnl"
    if resolved_cash < min_cash_pnl:
        return "cash_pnl"

    return "PASS"


def _is_winner(stats: dict, config: dict) -> bool:
    """
    Returns True only if the wallet has a verified forecasting track record.

    All qualifying thresholds are applied to RESOLVED positions only.
    avg_pct_pnl and total_cash_pnl across open positions are ignored — they
    measure unrealised gains (survivorship bias), not verified skill.

    See _classify_wallet for the gate definitions and config keys read.
    """
    return _classify_wallet(stats, config) == "PASS"


# ── Discovery ─────────────────────────────────────────────────────────────────

def discover_winners(config: dict) -> list[dict]:
    cfg         = config.get("accounts", {})
    sample_size = cfg.get("discovery_sample_size", 300)
    max_wallets = cfg.get("max_wallets_to_track", 50)

    print(f"  [accounts] Sampling {sample_size} recent trades for wallet discovery...")
    trades  = fetch_recent_trades(sample_size)
    wallets = list({t["proxyWallet"] for t in trades if t.get("proxyWallet")})
    print(f"  [accounts] {len(wallets)} unique wallets found, scoring...")

    winners = []
    for address in wallets:
        positions = fetch_user_positions(address)
        stats     = _score_wallet(positions)
        if not (stats and _is_winner(stats, config)):
            continue

        # Enrich with profile: check bulk trades first, then fetch user's own trades
        profile = _extract_profile(trades, address)
        if not profile["display_name"]:
            user_trades = fetch_user_trades(address, limit=5)
            profile = _extract_profile(user_trades, address)
            if not profile["display_name"]:
                # Last resort: name may be on any trade from that wallet
                for t in user_trades:
                    n = (t.get("name") or "").strip()
                    p = (t.get("pseudonym") or "").strip()
                    if n or p:
                        profile["display_name"] = n or p
                        profile["name"]         = n
                        profile["pseudonym"]    = p
                        break

        winners.append({
            "address":    address,
            "profile_url": profile["profile_url"],
            "display_name": profile["display_name"],
            "name":        profile["name"],
            "pseudonym":   profile["pseudonym"],
            **stats,
        })

    # Rank by resolved win rate (primary) then resolved cash P&L (secondary).
    # Sorting on unrealised avg_pct_pnl rewards lucky open positions, not skill.
    winners.sort(
        key=lambda w: (w.get("win_rate") or 0, w.get("resolved_cash_pnl") or 0),
        reverse=True,
    )
    return winners[:max_wallets]


# ── Discovery diagnostics ─────────────────────────────────────────────────────
# Instrumentation only. Does NOT change any threshold, sample size, or gate,
# and does NOT touch winning_accounts.json — it exists to answer whether the
# discovery gate finds zero winners because the sample is mis-specified
# (wallets die at min_resolved_count before skill is ever evaluated) or
# because sustained forecasting skill is genuinely rare in this pool.

#: Maps each of the four numeric gates PART C tracks to its stats dict key.
#: position_count is deliberately excluded — see the goal spec.
_NUMERIC_GATE_METRIC_KEY = {
    "resolved_count": "resolved_count",
    "win_rate":       "win_rate",
    "pct_pnl":        "resolved_avg_pct_pnl",
    "cash_pnl":       "resolved_cash_pnl",
}


def _percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation percentile over an already-sorted list."""
    k = (len(sorted_values) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def _distribution(values: list) -> dict:
    """
    min/median/p90/max over the non-None values in `values`. None entries
    (e.g. win_rate for a wallet with 0 resolved positions) are excluded and
    counted separately rather than crashing the calculation.
    """
    excluded = sum(1 for v in values if v is None)
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return {"n": 0, "excluded": excluded, "min": None, "median": None, "p90": None, "max": None}
    return {
        "n":       len(clean),
        "excluded": excluded,
        "min":     clean[0],
        "median":  _percentile(clean, 0.5),
        "p90":     _percentile(clean, 0.9),
        "max":     clean[-1],
    }


def diagnose_discovery(config: dict) -> dict:
    """
    Runs the discover_winners funnel once (single fetch pass — each wallet's
    positions are fetched exactly once), counting survivors at every stage
    and capturing the distribution of each of the four numeric gates' metric
    among wallets that reached that gate.

    Promotes no wallet, writes nothing to winning_accounts.json, and changes
    no threshold. Read-only instrumentation over the exact production gate
    (_classify_wallet), so its PASS count always agrees with _is_winner.
    """
    cfg = config.get("accounts", {})
    sample_size = cfg.get("discovery_sample_size", 300)

    trades = fetch_recent_trades(sample_size)
    n_trades = len(trades)

    wallets = list({t["proxyWallet"] for t in trades if t.get("proxyWallet")})
    n_wallets = len(wallets)

    n_positions_returned = 0
    classifications: list[tuple[str, dict, str]] = []  # (address, stats, classification)

    for address in wallets:
        positions = fetch_user_positions(address)  # single fetch pass — never re-fetched
        if positions:
            n_positions_returned += 1
        stats = _score_wallet(positions)
        if stats is None:
            continue
        classification = _classify_wallet(stats, config)
        classifications.append((address, stats, classification))

    n_scored = len(classifications)
    n_resolved_ge_1 = sum(1 for _, s, _ in classifications if s["resolved_count"] >= 1)

    gate_index = {g: i for i, g in enumerate(GATE_ORDER)}

    def _survived(gate: str) -> int:
        """Count of wallets whose classification is strictly past this gate."""
        return sum(1 for _, _, c in classifications if gate_index[c] > gate_index[gate])

    gate_survivors = {g: _survived(g) for g in GATE_ORDER[:-1]}  # exclude "PASS" itself

    funnel = [
        ("0. trades fetched",                                              n_trades),
        ("1. unique wallets",                                              n_wallets),
        ("2. positions returned",                                         n_positions_returned),
        ("3. scored",                                                     n_scored),
        ("4. resolved_count >= 1",                                        n_resolved_ge_1),
        (f"5. gate resolved_count>=min ({cfg.get('min_resolved_count', 10)})",  gate_survivors["resolved_count"]),
        (f"6. gate win_rate>=min ({cfg.get('min_win_rate', 55.0)})",            gate_survivors["win_rate"]),
        (f"7. gate position_count>=min ({cfg.get('min_positions', 5)})",       gate_survivors["position_count"]),
        (f"8. gate pct_pnl>=min ({cfg.get('min_pct_pnl', 10.0)})",             gate_survivors["pct_pnl"]),
        (f"9. gate cash_pnl>=min ({cfg.get('min_cash_pnl', 100.0)}) == WINNERS", gate_survivors["cash_pnl"]),
    ]

    def _reached(gate: str) -> list[tuple[dict, str]]:
        """Wallets evaluated at this gate — survived everything strictly before it."""
        return [(s, c) for _, s, c in classifications if gate_index[c] >= gate_index[gate]]

    distributions = {}
    for gate, metric_key in _NUMERIC_GATE_METRIC_KEY.items():
        population = _reached(gate)
        values = [s.get(metric_key) for s, _ in population]
        dist = _distribution(values)
        n_reached = len(population)
        n_passed = sum(1 for _, c in population if gate_index[c] > gate_index[gate])
        dist["n_reached"] = n_reached
        dist["pct_passing"] = round(n_passed / n_reached * 100, 1) if n_reached else None
        distributions[gate] = dist

    return {
        "n_trades_requested": sample_size,
        "n_trades_fetched":   n_trades,
        "funnel":             funnel,
        "distributions":      distributions,
        "n_winners":          gate_survivors["cash_pnl"],
    }


def format_diagnostic_report(result: dict) -> str:
    """Formats diagnose_discovery()'s result as a printable funnel + distribution report."""
    lines = []
    lines.append("=" * 92)
    lines.append("SMART MONEY DISCOVERY FUNNEL DIAGNOSTIC")
    lines.append("=" * 92)
    lines.append("")
    lines.append(f"Sample requested: {result['n_trades_requested']}  |  "
                  f"Sample actually fetched: {result['n_trades_fetched']}")
    lines.append("")
    lines.append(f"  {'Stage':<50} {'Survivors':>10} {'% of prior':>11}")
    lines.append(f"  {'-'*50} {'-'*10} {'-'*11}")
    prior = None
    for label, count in result["funnel"]:
        pct = f"{count / prior * 100:.1f}%" if prior else "--"
        lines.append(f"  {label:<50} {count:>10} {pct:>11}")
        prior = count
    lines.append("")
    lines.append(f"WINNERS: {result['n_winners']}")
    lines.append("")
    lines.append("GATING METRIC DISTRIBUTIONS (among wallets that reached each gate)")
    lines.append(f"  {'Gate':<16} {'n reached':>9} {'excl(None)':>10} "
                  f"{'min':>9} {'median':>9} {'p90':>9} {'max':>9} {'% passing':>10}")
    for gate, d in result["distributions"].items():
        def _f(v):
            return f"{v:.2f}" if isinstance(v, (int, float)) else "--"
        pct_s = f"{d['pct_passing']:.1f}%" if d["pct_passing"] is not None else "--"
        lines.append(f"  {gate:<16} {d['n_reached']:>9} {d['excluded']:>10} "
                      f"{_f(d['min']):>9} {_f(d['median']):>9} {_f(d['p90']):>9} "
                      f"{_f(d['max']):>9} {pct_s:>10}")
    lines.append("")
    lines.append("=" * 92)
    return "\n".join(lines)


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_fresh(config: dict) -> bool:
    if not os.path.exists(CACHE_FILE):
        return False
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ttl_hours = config.get("accounts", {}).get("cache_ttl_hours", 24)
        age_hours = (time.time() - data.get("updated_at", 0)) / 3600
        return age_hours < ttl_hours
    except Exception:
        return False


def _save_cache(winners: list[dict]) -> None:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated_at": time.time(), "winners": winners}, f, indent=2)


def _load_cache() -> list[dict]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("winners", [])
    except Exception:
        return []


def load_winners(config: dict) -> list[dict]:
    if _cache_fresh(config):
        winners = _load_cache()
        print(f"  [accounts] Loaded {len(winners)} cached winners")
        return winners
    winners = discover_winners(config)
    _save_cache(winners)
    print(f"  [accounts] Discovered {len(winners)} winning wallets — cache updated")
    return winners


# ── Smart money scan ──────────────────────────────────────────────────────────

def scan_market(condition_id: str, winners: list[dict], config: dict) -> list[dict]:
    if not condition_id or not winners:
        return []

    max_check = config.get("accounts", {}).get("max_wallets_per_scan", 20)
    signals   = []

    for winner in winners[:max_check]:
        trades        = fetch_user_trades(winner["address"], limit=50)
        market_trades = [t for t in trades if t.get("conditionId") == condition_id]
        if not market_trades:
            continue

        latest    = max(market_trades, key=lambda t: t.get("timestamp", 0))
        outcome   = (latest.get("outcome") or "").strip().lower()
        side      = (latest.get("side") or "BUY").upper()
        direction = None

        if outcome in ("yes", "1", "true"):
            direction = "YES" if side == "BUY" else "NO"
        elif outcome in ("no", "0", "false", "down"):
            direction = "NO" if side == "BUY" else "YES"

        if direction:
            signals.append({
                "address":       winner["address"],
                "display_name":  winner.get("display_name", ""),
                "name":          winner.get("name", ""),
                "pseudonym":     winner.get("pseudonym", ""),
                "profile_url":   winner.get("profile_url", f"{POLY_URL}/{winner['address']}"),
                "direction":     direction,
                "trade_count":   len(market_trades),
                "resolved_avg_pct_pnl": winner.get("resolved_avg_pct_pnl"),
                "resolved_cash_pnl":    winner.get("resolved_cash_pnl"),
                "win_rate":      winner.get("win_rate"),
                "active_markets": winner.get("active_markets", []),
                "last_trade_ts": latest.get("timestamp"),
            })

    return signals


# ── Main entry point ──────────────────────────────────────────────────────────

def enrich_with_smart_money(flagged_markets: list[dict], poly_data: dict, config: dict) -> dict[str, list[dict]]:
    if not config.get("accounts", {}).get("enabled", True):
        return {}

    winners = load_winners(config)
    if not winners:
        print("  [accounts] No winning wallets found — skipping smart money scan")
        return {}

    results = {}
    for m in flagged_markets:
        ticker       = m.get("ticker", "")
        poly         = poly_data.get(ticker)
        if not poly:
            continue
        condition_id = poly.get("condition_id") or poly.get("poly_slug", "")
        if not condition_id:
            continue
        signals = scan_market(condition_id, winners, config)
        if signals:
            results[ticker] = signals

    return results
