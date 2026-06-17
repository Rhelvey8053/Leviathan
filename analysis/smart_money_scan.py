"""
Smart money watchlist scanner.

Tracks open positions of the top Polymarket traders (seeded from the monthly
leaderboard) and surfaces what they're betting on right now. Cross-references
with the latest Kalshi snapshot to flag markets where smart money is active
on a related Polymarket market.

Run:
    python analysis/smart_money_scan.py
"""

import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import accounts
import polymarket

CONFIG_PATH  = os.path.join(ROOT, "config.json")
SNAPSHOT_DIR   = os.path.join(ROOT, "data", "snapshots")
CACHE_PATH     = os.path.join(ROOT, "data", "watchlist_cache.json")
REPORT_DIR     = os.path.join(ROOT, "data", "smart_money")


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _cache_fresh(ttl_hours: float) -> bool:
    try:
        data = json.load(open(CACHE_PATH, encoding="utf-8"))
        age  = (datetime.now(timezone.utc).timestamp() - data.get("ts", 0)) / 3600
        return age < ttl_hours
    except Exception:
        return False


def _save_cache(positions_by_trader: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"ts": datetime.now(timezone.utc).timestamp(),
                   "data": positions_by_trader}, f, indent=2)


def _load_cache() -> dict:
    return json.load(open(CACHE_PATH, encoding="utf-8")).get("data", {})


_BINARY_OUTCOMES = frozenset({"yes", "no"})

_SPORTS_OUTCOME_PATTERNS = frozenset({
    "over", "under", "draw", "spread", "o/u", "push", "tie",
})

# Substrings that identify a sports-game title (soccer win bets, match results, competitions)
_SPORTS_TITLE_PATTERNS = (
    " vs. ", " vs ",
    "end in a draw",
    "win on 202",      # dated soccer win bet e.g. "Will Germany win on 2026-06-25?"
    "1st half", "2nd half",
    " o/u ",
    "spread:",
    "over/under",
    # Major sports competitions — world cup winner bets don't cross-reference to Kalshi political markets
    "world cup", "fifa", "champions league",
    "super bowl", "stanley cup", "world series",
    " nfl ", " nba ", " mlb ", " nhl ", " mls ",
    "olympic", "wimbledon",
)


def _is_binary_position(p: dict) -> bool:
    """
    Return True only for YES/NO binary outcome positions.
    Excludes sports spreads, over/unders, team-name outcomes, etc.
    These non-binary positions create noise in Kalshi cross-referencing.
    """
    outcome = (p.get("outcome") or "").lower().strip()
    if outcome in _BINARY_OUTCOMES:
        return True
    # Reject if outcome is obviously a sports result
    if any(pat in outcome for pat in _SPORTS_OUTCOME_PATTERNS):
        return False
    # Reject multi-word team-name outcomes like "Washington Nationals"
    # or score lines like "Algeria (-1.5)" by checking for digits / parens
    if any(c.isdigit() for c in outcome):
        return False
    # Short single-word outcomes that aren't yes/no are usually team names — skip
    if len(outcome.split()) <= 2 and outcome not in _BINARY_OUTCOMES:
        return False
    return False  # default: exclude anything non-binary


def _is_sports_title(title: str) -> bool:
    """Return True if the market title looks like a sports game bet (not a political/macro event)."""
    t = title.lower()
    return any(pat in t for pat in _SPORTS_TITLE_PATTERNS)


def fetch_watchlist_positions(config: dict, force: bool = False) -> dict[str, list[dict]]:
    """
    Returns {trader_name: {address, monthly_pnl, positions}} for each watchlist entry.
    Uses a short TTL cache (default 4h) to avoid hammering the API.
    """
    cfg     = config.get("accounts", {})
    ttl     = cfg.get("watchlist_cache_ttl_hours", 4)
    min_val = cfg.get("watchlist_min_position_value", 1000)
    watchlist = cfg.get("watchlist", [])

    if not force and _cache_fresh(ttl):
        print("  [watchlist] Loaded from cache")
        return _load_cache()

    result = {}
    for entry in watchlist:
        name    = entry["name"]
        addr    = entry["address"]
        monthly = entry.get("monthly_pnl", 0)
        positions = accounts.fetch_user_positions(addr)
        open_pos  = [
            p for p in positions
            if not p.get("redeemable")
            and float(p.get("currentValue") or 0) >= min_val
        ]
        open_pos.sort(key=lambda p: float(p.get("currentValue") or 0), reverse=True)
        result[name] = {
            "address":     addr,
            "monthly_pnl": monthly,
            "positions":   open_pos,
        }
        print(f"  {name:<18}  ${monthly/1e6:.1f}M/mo  {len(open_pos)} open positions >= ${min_val:,}")

    _save_cache(result)
    return result


def _load_kalshi_titles() -> dict[str, str]:
    try:
        files = sorted(f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json"))
        if not files:
            return {}
        data = json.load(open(os.path.join(SNAPSHOT_DIR, files[-1]), encoding="utf-8"))
        return {m["ticker"]: m.get("title", "") for m in data.get("markets", [])}
    except Exception:
        return {}


_STOP = frozenset({
    "will", "the", "a", "an", "in", "on", "of", "to", "by", "vs", "vs.",
    "at", "for", "and", "or", "is", "be", "win", "does", "that", "this",
    "before", "after", "from", "with", "its", "their", "have", "has",
    "any", "all", "are", "were", "been", "not", "no", "yes", "2026",
    "2027", "2028", "2029", "2030",
})

# US state names that appear in prediction market titles — used for entity contradiction check.
# Compound state names handled as single words where the distinguishing word is unique
# (e.g. "texas", "utah", "florida"); "carolina"/"dakota" are omitted to avoid treating
# North Carolina vs South Carolina as contradictory.
_US_STATE_WORDS = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california",
    "colorado", "connecticut", "delaware", "florida", "georgia",
    "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas",
    "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana",
    "nebraska", "nevada", "hampshire", "jersey", "ohio", "oklahoma",
    "oregon", "pennsylvania", "rhode", "tennessee", "texas", "utah",
    "vermont", "virginia", "wisconsin", "wyoming",
})

# Major world cities used in prediction markets — checked pairwise to reject city mismatches.
_CITY_MARKERS = frozenset({
    "london", "angeles", "chicago", "houston", "philadelphia",
    "phoenix", "seattle", "denver", "boston", "miami", "atlanta",
    "dallas", "detroit", "toronto", "paris", "berlin", "tokyo",
    "beijing", "moscow", "tehran", "jerusalem", "kabul",
    "istanbul", "cairo", "dubai", "riyadh", "taipei",
})

# International organization groups — titles belonging to different groups are incompatible.
# Each tuple is one group; if one title draws from group 0 and the other from group 1, reject.
_ORG_EXCLUSION_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"opec"}),
    frozenset({" eu ", "european union", "brexit"}),
    frozenset({"nato"}),
    frozenset({"imf", "international monetary fund"}),
    frozenset({"wto", "world trade organization"}),
    frozenset({"asean"}),
)


def _entity_contradiction(poly_title: str, kalshi_title: str) -> bool:
    """
    Returns True if the two titles clearly refer to incompatible named entities.

    Handles three categories of false-positive:
      - US state mismatch: "Texas Senate" vs "Utah Senate"
      - City mismatch: "Los Angeles mayor" vs "London mayor"
      - Organization mismatch: "leave OPEC" vs "leave the EU"

    Does NOT reject titles that share a country (e.g. both mention Israel)
    even if they differ on the second party (Syria vs Lebanon) — that overlap
    is semantically close enough to be a useful cross-reference.
    """
    p = f" {poly_title.lower()} "
    k = f" {kalshi_title.lower()} "

    # US state mismatch: both titles name a US state but different ones
    p_states = {s for s in _US_STATE_WORDS if f" {s} " in p or f" {s}," in p or f" {s}." in p}
    k_states = {s for s in _US_STATE_WORDS if f" {s} " in k or f" {s}," in k or f" {s}." in k}
    if p_states and k_states and not (p_states & k_states):
        return True

    # City mismatch: both titles name a major city but different ones
    p_cities = {c for c in _CITY_MARKERS if c in poly_title.lower()}
    k_cities = {c for c in _CITY_MARKERS if c in kalshi_title.lower()}
    if p_cities and k_cities and not (p_cities & k_cities):
        return True

    # Organization mismatch: titles reference organizations from different exclusive groups
    def _org_groups(text: str) -> set[int]:
        return {i for i, grp in enumerate(_ORG_EXCLUSION_GROUPS) if any(t in text for t in grp)}

    p_orgs = _org_groups(p)
    k_orgs = _org_groups(k)
    if p_orgs and k_orgs and not (p_orgs & k_orgs):
        return True

    return False


def _normalize(title: str) -> set[str]:
    import re
    words = re.sub(r"[^a-z0-9\s]", "", title.lower()).split()
    return {w for w in words if w not in _STOP and len(w) > 2}


def _match_to_kalshi(pos_title: str, kalshi_titles: dict[str, str],
                     min_score: float = 0.30) -> list[tuple[str, float]]:
    """
    Combined Jaccard word-overlap + SequenceMatcher score.
    Matches Polymarket position title against all Kalshi market titles.
    Applies entity-contradiction check to reject geographic/org mismatches.
    Returns up to 3 matches above min_score, sorted by score desc.
    """
    from difflib import SequenceMatcher

    poly_words = _normalize(pos_title)
    if not poly_words:
        return []

    matches = []
    for ticker, title in kalshi_titles.items():
        kalshi_words = _normalize(title)
        if not kalshi_words:
            continue
        common = poly_words & kalshi_words
        # Require at least 2 shared keywords — prevents single-word or character-similarity false positives
        if len(common) < 2:
            continue
        # Reject geographic/organizational entity contradictions before scoring
        if _entity_contradiction(pos_title, title):
            continue
        union   = poly_words | kalshi_words
        jaccard = len(common) / len(union) if union else 0.0
        seq     = SequenceMatcher(None, pos_title.lower(), title.lower()).ratio()
        score   = max(jaccard, seq * 0.85)
        if score >= min_score:
            matches.append((ticker, round(score, 3)))

    matches.sort(key=lambda x: -x[1])
    return matches[:3]


def _group_signals_by_ticker(signals: list[dict]) -> list[dict]:
    """
    Aggregate individual per-trader signals by Kalshi ticker.
    Multiple traders on the same Kalshi market produce one grouped entry with
    combined position value, trader count, and direction vote tallies.
    """
    groups: dict[str, dict] = {}
    for s in signals:
        ticker = s["kalshi_ticker"]
        if ticker not in groups:
            groups[ticker] = {
                "kalshi_ticker":      ticker,
                "kalshi_title":       s.get("kalshi_title", ""),
                "total_position_val": 0.0,
                "traders":            set(),
                "directions":         {},
                "signals":            [],
            }
        g = groups[ticker]
        g["total_position_val"] += s["position_val"]
        g["traders"].add(s["trader"])
        direction = s.get("kalshi_direction", "UNKNOWN")
        g["directions"][direction] = g["directions"].get(direction, 0) + 1
        g["signals"].append(s)

    result = []
    for g in groups.values():
        directions = g["directions"]
        yes_count  = directions.get("YES", 0)
        no_count   = directions.get("NO", 0)
        if yes_count > 0 and no_count > 0:
            consensus = "MIXED"
        elif yes_count > 0:
            consensus = "YES"
        elif no_count > 0:
            consensus = "NO"
        else:
            consensus = "UNKNOWN"
        result.append({
            "kalshi_ticker":       g["kalshi_ticker"],
            "kalshi_title":        g["kalshi_title"],
            "total_position_val":  g["total_position_val"],
            "trader_count":        len(g["traders"]),
            "directions":          directions,
            "consensus_direction": consensus,
            "signals":             g["signals"],
        })
    return result


def run_smart_money_scan(config: dict | None = None, force_refresh: bool = False) -> dict:
    if config is None:
        config = load_config()

    print(f"\n=== Smart Money Watchlist Scan | {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} ===\n")
    print("Fetching open positions for watchlist traders...")

    trader_data   = fetch_watchlist_positions(config, force=force_refresh)
    kalshi_titles = _load_kalshi_titles()

    print(f"\n  Kalshi snapshot: {len(kalshi_titles)} markets loaded for cross-reference\n")
    print("=" * 100)

    all_signals   = []
    total_traders = 0
    total_pos     = 0

    for name, data in trader_data.items():
        positions = data.get("positions", [])
        if not positions:
            continue
        total_traders += 1
        total_pos     += len(positions)

        monthly = data.get("monthly_pnl", 0)
        print(f"\n  {name}  (${monthly/1e6:.1f}M/mo)  —  {len(positions)} significant open positions:")

        for p in positions:
            val     = float(p.get("currentValue") or 0)
            price   = float(p.get("curPrice") or p.get("avgPrice") or 0)
            outcome = p.get("outcome", "?")
            title   = p.get("title", "")
            slug    = p.get("eventSlug") or p.get("slug", "")
            pct_pnl = float(p.get("percentPnl") or 0)
            pnl_str = f"{pct_pnl:+.1f}%"

            kalshi_matches = (
                _match_to_kalshi(title, kalshi_titles, min_score=0.50)
                if _is_binary_position(p) and not _is_sports_title(title) else []
            )
            match_str = ""
            if kalshi_matches:
                match_str = f"  -> Kalshi: {kalshi_matches[0][0]} ({kalshi_matches[0][1]:.0%} match)"

            print(f"    {outcome:<4}  ${val:>9,.0f}  {price:.2f}  {pnl_str:>7}  {title[:55]}{match_str}")

            if kalshi_matches:
                best_ticker = kalshi_matches[0][0]
                # Derive implied Kalshi direction: Poly YES/NO on a semantically
                # matching question maps directly to Kalshi YES/NO.
                kalshi_dir = outcome.upper() if outcome.upper() in ("YES", "NO") else "UNKNOWN"
                all_signals.append({
                    "trader":           name,
                    "monthly_pnl":      monthly,
                    "poly_title":       title,
                    "poly_outcome":     outcome,
                    "poly_price":       price,
                    "position_val":     val,
                    "pct_pnl":          pct_pnl,
                    "poly_url":         f"https://polymarket.com/event/{slug}",
                    "kalshi_ticker":    best_ticker,
                    "kalshi_title":     kalshi_titles.get(best_ticker, ""),
                    "match_score":      kalshi_matches[0][1],
                    "kalshi_direction": kalshi_dir,
                })

    grouped = _group_signals_by_ticker(all_signals)

    print(f"\n{'='*100}")
    print(f"\n  Traders with open positions: {total_traders}")
    print(f"  Total significant positions: {total_pos}")

    if all_signals:
        print(f"\n  Kalshi cross-references found ({len(all_signals)} raw, {len(grouped)} unique tickers):\n")
        all_signals.sort(key=lambda s: -s["position_val"])
        for s in all_signals:
            print(f"    [{s['trader']}]  {s['poly_outcome']} on {s['poly_title'][:45]}")
            print(f"      -> Kalshi: {s['kalshi_ticker']}  (match {s['match_score']:.0%})  "
                  f"Poly price: {s['poly_price']:.2f}  Position: ${s['position_val']:,.0f}  "
                  f"=> Kalshi {s['kalshi_direction']}")

        print(f"\n  Grouped by Kalshi ticker ({len(grouped)} markets):\n")
        for g in sorted(grouped, key=lambda x: -x["total_position_val"]):
            dirs = g["directions"]
            dir_str = f"YES×{dirs.get('YES',0)} NO×{dirs.get('NO',0)}" if (dirs.get('YES',0) + dirs.get('NO',0)) > 1 else g["consensus_direction"]
            print(f"    {g['kalshi_ticker']:<36}  ${g['total_position_val']:>10,.0f}  "
                  f"{g['trader_count']} trader(s)  => {dir_str}")
            print(f"      {g['kalshi_title'][:70]}")
    else:
        print("\n  No Kalshi cross-references found in this snapshot.")

    print()

    result = {
        "traders_active":    total_traders,
        "positions_total":   total_pos,
        "kalshi_signals":    all_signals,
        "grouped_signals":   grouped,
        "trader_data":       trader_data,
        "run_at":            datetime.now(timezone.utc).isoformat(),
    }
    return result


def save_signals_cache(result: dict) -> None:
    """
    Write a lightweight signals cache to data/smart_money/latest_signals.json.
    Contains only the Kalshi cross-reference tickers so main.py can boost them
    in the scoring queue without parsing markdown.
    """
    cache_path = os.path.join(REPORT_DIR, "latest_signals.json")
    os.makedirs(REPORT_DIR, exist_ok=True)
    payload = {
        "run_at":         result.get("run_at", ""),
        "kalshi_tickers": list({s["kalshi_ticker"] for s in result.get("kalshi_signals", [])}),
        "signal_count":   len(result.get("kalshi_signals", [])),
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def save_report(result: dict) -> str:
    """Write a dated markdown report to data/smart_money/YYYY-MM-DD.md. Returns the path."""
    save_signals_cache(result)
    os.makedirs(REPORT_DIR, exist_ok=True)
    date_str  = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_at    = result.get("run_at", datetime.now(timezone.utc).isoformat())
    out_path  = os.path.join(REPORT_DIR, f"{date_str}.md")

    lines = [
        f"# Smart Money Watchlist — {date_str}",
        f"",
        f"**Run at:** {run_at}",
        f"**Traders active:** {result['traders_active']}  "
        f"**Positions tracked:** {result['positions_total']}",
        f"",
    ]

    signals = result.get("kalshi_signals", [])
    if signals:
        lines += [
            f"## Kalshi Cross-References ({len(signals)})",
            f"",
            f"| Trader | Out | Poly Market | Kalshi Market | Ticker | Price | Position | Match |",
            f"|--------|-----|-------------|---------------|--------|-------|----------|-------|",
        ]
        ranked = sorted(signals, key=lambda x: -(x["match_score"] * x["position_val"]))
        for s in ranked:
            kalshi_t = s.get("kalshi_title", s["kalshi_ticker"])[:50]
            lines.append(
                f"| {s['trader']} | {s['poly_outcome']} | {s['poly_title'][:45]} "
                f"| {kalshi_t} | {s['kalshi_ticker']} "
                f"| {s['poly_price']:.2f} | ${s['position_val']:,.0f} "
                f"| {s['match_score']:.0%} |"
            )
        lines.append("")
    else:
        lines += ["## Kalshi Cross-References", "", "None found in this snapshot.", ""]

    lines += ["## All Open Positions", ""]
    for name, data in result.get("trader_data", {}).items():
        positions = data.get("positions", [])
        if not positions:
            continue
        monthly = data.get("monthly_pnl", 0)
        lines.append(f"### {name}  (${monthly/1e6:.1f}M/mo)")
        lines.append("")
        lines.append("| Outcome | Value | Price | PnL | Market |")
        lines.append("|---------|-------|-------|-----|--------|")
        for p in positions:
            val     = float(p.get("currentValue") or 0)
            price   = float(p.get("curPrice") or p.get("avgPrice") or 0)
            outcome = p.get("outcome", "?")
            title   = p.get("title", "")[:55]
            pct_pnl = float(p.get("percentPnl") or 0)
            lines.append(f"| {outcome} | ${val:,.0f} | {price:.2f} | {pct_pnl:+.1f}% | {title} |")
        lines.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return out_path


if __name__ == "__main__":
    force = "--refresh" in sys.argv
    cfg   = load_config()
    result = run_smart_money_scan(cfg, force_refresh=force)
    path   = save_report(result)
    print(f"Report saved: {path}")
