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
CLOB_BASE  = "https://clob.polymarket.com"
POLY_URL   = "https://polymarket.com/profile"
_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_FILE = os.path.join(_ROOT, "data", "winning_accounts.json")
RESOLUTION_CACHE_FILE = os.path.join(_ROOT, "data", "market_resolutions.json")


# ── API helpers ───────────────────────────────────────────────────────────────

# Polymarket's Data API caps /positions at 150 req/10s (15/sec, IP-based --
# docs.polymarket.com/api-reference/rate-limits, confirmed live 2026-08-23),
# with over-limit requests throttled/queued rather than rejected outright.
# discover_winners() and analysis/smart_money_scan.py's watchlist scan both
# call fetch_user_positions() back-to-back for every wallet with no pacing
# at all -- easily exceeds 15/sec, and a request queued past this function's
# own timeout raises a timeout exception that used to be silently swallowed
# and reported as "no positions returned," indistinguishable from a wallet
# that genuinely has zero. 0.1s keeps sustained usage safely under 15/sec
# even across a long back-to-back loop.
_MIN_REQUEST_INTERVAL_S = 0.1


def _get(path: str, params: dict = None, timeout: int = 12) -> list | dict | None:
    time.sleep(_MIN_REQUEST_INTERVAL_S)
    try:
        resp = requests.get(f"{DATA_API}/{path}", params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        # A real failure (timeout, connection error, non-2xx) now prints,
        # so it's visibly distinct in logs from a genuinely empty 200
        # response -- callers still get None/[] either way (unchanged
        # contract), this only adds the visibility that was missing.
        print(f"  [accounts] Data API request to {path!r} failed: {e}")
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


# ── True resolution (ground truth for win/loss) ────────────────────────────────
#
# BUG (found + confirmed 2026-09-08): the Data API's /positions currentValue,
# curPrice, percentPnl, and cashPnl fields all read ~0 / ~-100% for a resolved
# (redeemable=True) position REGARDLESS of whether it actually won or lost --
# confirmed live across 1,479 resolved positions spanning 20 watchlist
# wallets, 99.8% showed percentPnl ~= -99.9999% independent of position size
# or realizedPnl sign. These fields only reflect true state once a position
# is manually redeemed on-chain, which practically never happens for large
# active traders. realizedPnl alone isn't a fix either -- it only captures
# cash already banked from voluntary sells, not the terminal payout of a
# position held to resolution. The CLOB API's /markets/{condition_id} is
# independent ground truth: it reports each outcome token's true settlement
# (winner: true/false), verified live against a real position + cross-checked
# against Gamma's outcomePrices for the same market.

def fetch_market_resolution(condition_id: str) -> dict | None:
    """
    Returns {outcome_name: is_winner_bool} for a resolved market via CLOB's
    /markets/{condition_id} -- ground truth, independent of the Data API's
    broken post-resolution position fields (see module note above).
    Returns None if the market isn't closed yet or the request fails.
    """
    time.sleep(_MIN_REQUEST_INTERVAL_S)
    try:
        resp = requests.get(f"{CLOB_BASE}/markets/{condition_id}", timeout=12)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [accounts] CLOB resolution fetch for {condition_id!r} failed: {e}")
        return None
    if not data or not data.get("closed"):
        return None
    return {
        t["outcome"]: bool(t.get("winner"))
        for t in data.get("tokens", []) if t.get("outcome")
    }


def _load_resolution_cache() -> dict:
    try:
        with open(RESOLUTION_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_resolution_cache(cache: dict) -> None:
    os.makedirs(os.path.dirname(RESOLUTION_CACHE_FILE), exist_ok=True)
    with open(RESOLUTION_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def fetch_resolutions_for_positions(positions: list[dict], cache: dict = None,
                                     fetch_new: bool = True) -> dict:
    """
    Fetches (and permanently disk-caches) the true resolution for every
    resolved position's conditionId not already cached. A market's
    resolution is immutable once set, so entries never expire -- repeat
    calls across wallets/runs cost nothing for already-seen markets.

    fetch_new=False skips live CLOB calls entirely and returns only what's
    already cached (on disk or in the passed-in `cache`) -- for callers on
    an interactive/live-refreshed path where a wallet can have hundreds of
    distinct unresolved-in-cache markets (e.g. a fresh 100-wallet discovery
    sample), where fetching every one live would turn a few-second panel
    into a many-minute one. discover_winners() (the once-a-day pipeline),
    _verify_watchlist_trader(), and get_wallet_profile() (a single wallet,
    on demand) all keep the default fetch_new=True for full accuracy --
    only diagnose_discovery()'s live exploratory sample opts out.
    """
    if cache is None:
        cache = _load_resolution_cache()

    if not fetch_new:
        return cache

    needed = {
        p["conditionId"] for p in positions
        if p.get("redeemable") and p.get("conditionId") and p["conditionId"] not in cache
    }
    fetched_any = False
    for cid in needed:
        resolution = fetch_market_resolution(cid)
        if resolution is not None:
            cache[cid] = resolution
            fetched_any = True

    if fetched_any:
        _save_resolution_cache(cache)

    return cache


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


def _score_wallet(positions: list[dict], resolutions: dict | None = None) -> dict | None:
    """
    Scores a wallet on their RESOLVED position performance only.

    Coin-flip markets (sub-daily crypto tick bets) and sports-game bets are
    excluded from all scoring — they test luck, not forecasting skill.
    Ranking metrics (win_rate, resolved_cash_pnl) come from resolved positions
    only (redeemable=True), so a wallet with 0 resolved positions cannot qualify.

    `resolutions` is a {conditionId: {outcome_name: is_winner_bool}} map from
    fetch_resolutions_for_positions() -- the source of truth for win/loss on a
    resolved position. The Data API's own percentPnl/cashPnl fields are NOT
    used for resolved positions: they read ~0/~-100% unconditionally post-
    resolution regardless of actual outcome (see fetch_market_resolution's
    module note). A resolved position whose conditionId/outcome isn't in
    `resolutions` (fetch failed, or resolutions=None) is excluded from
    scoring rather than misclassified as a loss. Open (non-redeemable)
    positions are unaffected by this bug — their percentPnl still reflects
    a live mark-to-market price — so active_markets keeps using it.
    """
    if not positions:
        return None

    # Lazy import to avoid circular dependency; _is_sports_title is a pure predicate
    try:
        from analysis.smart_money_scan import _is_sports_title as _sports
    except ImportError:
        _sports = lambda t: False  # noqa: E731

    resolutions = resolutions or {}

    resolved_pct_pnls = []  # true pct PnL per resolved position (realized + payout - cost)
    resolved_true_pnls = []  # true cash PnL per resolved position
    resolved_wins       = []  # bool win/loss per resolved position, from true resolution
    active_mkts          = []

    for p in positions:
        try:
            title = (p.get("title") or "").strip()

            # Exclude coin-flip and sports-game markets — P&L here is noise, not skill
            if _is_coinflip(title) or _sports(title):
                continue

            slug    = (p.get("eventSlug") or p.get("slug") or "").strip()
            outcome = (p.get("outcome") or "").strip()

            if title and not p.get("redeemable"):
                pct_open = float(p.get("percentPnl") or 0)
                active_mkts.append({
                    "title":   title,
                    "slug":    slug,
                    "outcome": outcome,
                    "pct_pnl": pct_open,
                    "url":     f"https://polymarket.com/event/{slug}" if slug else "",
                })

            if p.get("redeemable"):
                resolution = resolutions.get(p.get("conditionId"))
                if resolution is None or outcome not in resolution:
                    continue  # unknown true outcome — exclude, never guess

                won      = resolution[outcome]
                realized = float(p.get("realizedPnl") or 0)
                initial  = float(p.get("initialValue") or 0)
                size     = float(p.get("size") or 0)
                payout   = size if won else 0.0
                true_pnl = realized + payout - initial

                resolved_wins.append(won)
                resolved_true_pnls.append(true_pnl)
                resolved_pct_pnls.append(round(true_pnl / initial * 100, 2) if initial else 0.0)
        except (TypeError, ValueError):
            pass

    wins     = sum(1 for w in resolved_wins if w)
    n_res    = len(resolved_wins)
    win_rate = round(wins / n_res * 100, 1) if n_res else None

    active_mkts.sort(key=lambda m: abs(m["pct_pnl"]), reverse=True)

    return {
        "position_count":      len(positions),
        "resolved_count":      n_res,
        "resolved_avg_pct_pnl": round(sum(resolved_pct_pnls) / n_res, 2) if n_res else None,
        "resolved_cash_pnl":   round(sum(resolved_true_pnls), 2),
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


def gate_checklist(stats: dict, config: dict) -> list[dict]:
    """
    Plain-language pass/fail breakdown of every _classify_wallet gate, in
    GATE_ORDER, for the Trader Profile dashboard page -- spells out exactly
    why a wallet is (or isn't) being tracked, one threshold at a time,
    instead of a single opaque PASS/FAIL.
    """
    cfg = config.get("accounts", {})
    min_resolved  = cfg.get("min_resolved_count", 10)
    min_win_rate  = cfg.get("min_win_rate", 55.0)
    min_positions = cfg.get("min_positions", 5)
    min_pct_pnl   = cfg.get("min_pct_pnl", 10.0)
    min_cash_pnl  = cfg.get("min_cash_pnl", 100.0)

    resolved  = stats.get("resolved_count", 0)
    win_rate  = stats.get("win_rate")
    positions = stats.get("position_count", 0)
    pct_pnl   = stats.get("resolved_avg_pct_pnl")
    cash_pnl  = stats.get("resolved_cash_pnl", 0.0) or 0.0

    return [
        {
            "label": "Enough resolved bets to judge",
            "passed": resolved >= min_resolved,
            "detail": f"{resolved} resolved bets (need at least {min_resolved})",
        },
        {
            "label": "Wins often enough",
            "passed": win_rate is not None and win_rate >= min_win_rate,
            "detail": (f"{win_rate:.1f}% win rate (need at least {min_win_rate:.0f}%)"
                       if win_rate is not None else f"no win rate yet (need at least {min_win_rate:.0f}%)"),
        },
        {
            "label": "Trades often enough to matter",
            "passed": positions >= min_positions,
            "detail": f"{positions} total positions (need at least {min_positions})",
        },
        {
            "label": "Profitable per bet, on average",
            "passed": pct_pnl is not None and pct_pnl >= min_pct_pnl,
            "detail": (f"{pct_pnl:+.1f}% average return per resolved bet (need at least {min_pct_pnl:.0f}%)"
                       if pct_pnl is not None else f"no data yet (need at least {min_pct_pnl:.0f}%)"),
        },
        {
            "label": "Real money made, not just a good percentage",
            "passed": cash_pnl >= min_cash_pnl,
            "detail": f"${cash_pnl:,.2f} total realized profit on resolved bets (need at least ${min_cash_pnl:.0f})",
        },
    ]


def get_wallet_profile(address: str, config: dict) -> dict | None:
    """
    Assembles everything the Trader Profile dashboard page needs for one
    wallet from a single fetch_user_positions() call: summary stats, a
    plain-language gate checklist, current (open) bets, and resolved bet
    history with true win/loss -- computed the same way _score_wallet does
    (true resolution via fetch_resolutions_for_positions, NOT the Data
    API's broken post-resolution percentPnl/cashPnl).

    Returns None only if the API returns literally nothing for this
    address. Unlike _is_winner, this never gates the result -- it's a
    diagnostic/transparency view for any wallet, tracked or not.
    """
    positions = fetch_user_positions(address)
    if not positions:
        return None

    resolutions = fetch_resolutions_for_positions(positions)
    stats = _score_wallet(positions, resolutions)
    classification = _classify_wallet(stats, config) if stats else None
    checklist = gate_checklist(stats, config) if stats else []

    try:
        from analysis.smart_money_scan import _is_sports_title as _sports
    except ImportError:
        _sports = lambda t: False  # noqa: E731

    current_bets = []
    bet_history  = []

    for p in positions:
        try:
            title = (p.get("title") or "").strip()
            if not title or _is_coinflip(title) or _sports(title):
                continue

            slug    = (p.get("eventSlug") or p.get("slug") or "").strip()
            outcome = (p.get("outcome") or "").strip()
            url     = f"https://polymarket.com/event/{slug}" if slug else ""

            if not p.get("redeemable"):
                current_bets.append({
                    "title":          title,
                    "side":           outcome,
                    "value":          float(p.get("initialValue") or 0),
                    "conviction_pct": float(p.get("percentPnl") or 0),
                    "url":            url,
                })
                continue

            resolution = resolutions.get(p.get("conditionId"))
            if resolution is None or outcome not in resolution:
                continue  # unknown true outcome — omit rather than guess

            won      = resolution[outcome]
            realized = float(p.get("realizedPnl") or 0)
            initial  = float(p.get("initialValue") or 0)
            size     = float(p.get("size") or 0)
            true_pnl = realized + (size if won else 0.0) - initial

            bet_history.append({
                "title":   title,
                "side":    outcome,
                "won":     won,
                "pnl":     round(true_pnl, 2),
                "pct_pnl": round(true_pnl / initial * 100, 1) if initial else 0.0,
                "url":     url,
            })
        except (TypeError, ValueError):
            pass

    current_bets.sort(key=lambda b: abs(b["conviction_pct"]), reverse=True)
    bet_history.sort(key=lambda b: abs(b["pnl"]), reverse=True)

    return {
        "address":        address,
        "stats":          stats,
        "classification": classification,
        "checklist":      checklist,
        "current_bets":   current_bets,
        "bet_history":    bet_history,
    }


# ── Discovery ─────────────────────────────────────────────────────────────────

def discover_winners(config: dict) -> list[dict]:
    """
    Live-samples recent Polymarket trades, scores each unique wallet on
    verified resolved-position win rate (see fetch_market_resolution's
    module note), and returns the ones that pass every _is_winner() gate.

    Called synchronously from main.py's daily pipeline (via
    enrich_with_smart_money -> load_winners) whenever the 24h winner cache
    goes stale -- and main.py's own scheduled task has only a 10-minute
    ExecutionTimeLimit (scripts/setup_scheduler.ps1). Per-wallet cost is
    highly variable (a single large wallet's resolution fetches can take
    many minutes; confirmed live 2026-09-09 -- one wallet with 397 resolved
    positions on a cold cache dominated an entire discovery pass), so a
    naive full sample_size loop has no bound on total wall-clock time and
    can silently blow through main.py's own time limit, killing the whole
    daily run, not just this step. discovery_time_budget_s enforces a hard
    ceiling: once exceeded, stop checking new wallets and return whatever
    winners were found among the wallets actually reached -- a smaller
    result today, never a hung/killed pipeline tomorrow. The permanent
    on-disk resolution cache means later runs (same day or the next) cover
    more ground for the same time budget as it warms up.
    """
    cfg         = config.get("accounts", {})
    sample_size = cfg.get("discovery_sample_size", 300)
    max_wallets = cfg.get("max_wallets_to_track", 50)
    time_budget_s = cfg.get("discovery_time_budget_s", 120)

    print(f"  [accounts] Sampling {sample_size} recent trades for wallet discovery...")
    trades  = fetch_recent_trades(sample_size)
    wallets = list({t["proxyWallet"] for t in trades if t.get("proxyWallet")})
    print(f"  [accounts] {len(wallets)} unique wallets found, scoring "
          f"(time budget {time_budget_s}s)...")

    resolution_cache = _load_resolution_cache()

    t0 = time.time()
    winners = []
    for i, address in enumerate(wallets):
        if time.time() - t0 > time_budget_s:
            print(f"  [accounts] Time budget reached after {i}/{len(wallets)} wallets "
                  f"-- returning {len(winners)} winners found so far.")
            break

        positions   = fetch_user_positions(address)
        resolutions = fetch_resolutions_for_positions(positions, cache=resolution_cache)
        stats       = _score_wallet(positions, resolutions)
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
    resolution_cache = _load_resolution_cache()

    for address in wallets:
        positions = fetch_user_positions(address)  # single fetch pass — never re-fetched
        if positions:
            n_positions_returned += 1
        # fetch_new=False: this is a live, exploratory sample of up to
        # discovery_sample_size fresh wallets, refreshed on a 5-min dashboard
        # cache -- fetching every not-yet-cached market's resolution live
        # would turn this into a many-minute call. Uses whatever's already
        # cached (grows over time from discover_winners()/the watchlist/
        # Trader Profile page) and excludes the rest, same as an unknown
        # resolution always does — an honest undercount, not a wrong one.
        resolutions = fetch_resolutions_for_positions(positions, cache=resolution_cache, fetch_new=False)
        stats = _score_wallet(positions, resolutions)
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


def read_cached_winners() -> tuple[list[dict], float | None]:
    """
    Read-only peek at data/winning_accounts.json -- NEVER triggers a live
    discover_winners() fetch, unlike load_winners() (which falls back to a
    live multi-minute Polymarket crawl when the cache is stale). Built for
    the Smart Money dashboard's Winning Whales panel, where opening a tab
    must never block on a live crawl -- the live daily pipeline (main.py)
    is what actually keeps this cache warm. Returns (winners, updated_at
    unix timestamp or None if the cache file doesn't exist/is unreadable).
    """
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("winners", []), data.get("updated_at")
    except Exception:
        return [], None


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
