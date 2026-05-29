"""
The Odds API integration — sharp bookmaker consensus prices.

Fetches live h2h (moneyline) odds from 40+ bookmakers across major sports,
converts to vig-free implied probabilities, and exposes them as a normalized
market index for cross-reference against Kalshi sports markets.

Free tier: 500 requests/month. Results are cached for 6 hours so a daily
run consumes at most one set of requests per sport.

Setup: add ODDS_API_KEY to .env  (get a free key at the-odds-api.com)
"""

import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL   = "https://api.the-odds-api.com/v4"
CACHE_FILE = os.path.join(os.path.dirname(__file__), "odds_cache.json")
CACHE_TTL  = 6 * 3600  # 6 hours — odds change slowly, protect free quota

DEFAULT_SPORTS = [
    "americanfootball_nfl",
    "americanfootball_ncaaf",
    "basketball_nba",
    "basketball_ncaab",
    "baseball_mlb",
    "icehockey_nhl",
    "soccer_epl",
    "soccer_usa_mls",
]


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_valid() -> bool:
    if not os.path.exists(CACHE_FILE):
        return False
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return (time.time() - data.get("ts", 0)) < CACHE_TTL
    except Exception:
        return False


def _load_cache() -> list[dict]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("events", [])
    except Exception:
        return []


def _save_cache(events: list[dict]) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "events": events}, f)
    except Exception:
        pass


# ── Odds math ─────────────────────────────────────────────────────────────────

def _best_decimal_odds(bookmakers: list[dict], outcome_name: str) -> float | None:
    """Returns the highest decimal odds available for a named outcome across all bookmakers."""
    best = None
    for bm in bookmakers:
        for market in bm.get("markets", []):
            if market.get("key") != "h2h":
                continue
            for o in market.get("outcomes", []):
                if o.get("name") == outcome_name:
                    try:
                        price = float(o["price"])
                        if price > 1 and (best is None or price > best):
                            best = price
                    except (KeyError, ValueError, TypeError):
                        pass
    return best


def _vig_free(p_home_raw: float, p_away_raw: float) -> tuple[float, float]:
    """Remove vig from a two-outcome market and return fair implied probabilities."""
    total = p_home_raw + p_away_raw
    if total <= 0:
        return 0.5, 0.5
    return p_home_raw / total, p_away_raw / total


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_events(config: dict) -> list[dict]:
    """
    Fetches current game odds for all configured sports.
    Uses a 6-hour cache to protect the free-tier quota.
    Returns raw event objects from The Odds API.
    """
    if _cache_valid():
        return _load_cache()

    api_key = os.getenv("ODDS_API_KEY", "")
    if not api_key:
        return []

    sports     = config.get("odds_api", {}).get("sports", DEFAULT_SPORTS)
    all_events = []

    for sport in sports:
        try:
            resp = requests.get(
                f"{BASE_URL}/sports/{sport}/odds/",
                params={
                    "apiKey":     api_key,
                    "regions":    "us",
                    "markets":    "h2h",
                    "oddsFormat": "decimal",
                },
                timeout=12,
            )
            if resp.status_code in (404, 422):
                continue  # sport has no current events
            resp.raise_for_status()
            all_events.extend(resp.json())
        except Exception:
            pass

    _save_cache(all_events)
    return all_events


def normalize_events(events: list[dict]) -> list[dict]:
    """
    Converts raw Odds API events into normalized market dicts.
    Produces two entries per game (home win + away win) with vig-free probabilities.
    """
    results = []
    for event in events:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        if not home or not away:
            continue

        bookmakers  = event.get("bookmakers", [])
        home_odds   = _best_decimal_odds(bookmakers, home)
        away_odds   = _best_decimal_odds(bookmakers, away)

        if not home_odds or not away_odds:
            continue

        p_home_raw = 1 / home_odds
        p_away_raw = 1 / away_odds
        p_home, p_away = _vig_free(p_home_raw, p_away_raw)

        sport_title = event.get("sport_title", "")
        tag = f" [{sport_title}]" if sport_title else ""

        results.append({
            "title":       f"Will {home} beat {away}?",
            "probability": round(p_home, 4),
            "source":      "OddsAPI",
            "url":         "",
            "volume":      0,
            "sport":       sport_title,
        })
        results.append({
            "title":       f"Will {away} beat {home}?",
            "probability": round(p_away, 4),
            "source":      "OddsAPI",
            "url":         "",
            "volume":      0,
            "sport":       sport_title,
        })

    return results
