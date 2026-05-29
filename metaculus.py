"""
Metaculus cross-reference — calibrated community forecasts.

Metaculus hosts thousands of open questions with community probability estimates
from a vetted pool of superforecasters.

Requires a free API token: https://www.metaculus.com/api/
Add METACULUS_API_TOKEN to your .env file.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://www.metaculus.com/api2"

_STOP = {
    "will", "the", "a", "an", "be", "in", "on", "by", "to", "of", "or",
    "and", "is", "for", "at", "it", "its", "who", "what", "when", "which",
    "that", "this", "have", "has", "do", "does", "not", "than", "more",
}


def _headers() -> dict:
    token = os.getenv("METACULUS_API_TOKEN", "")
    h = {"User-Agent": "Leviathan/1.0", "Accept": "application/json"}
    if token:
        h["Authorization"] = f"Token {token}"
    return h


def _search(term: str, limit: int = 6) -> list[dict]:
    token = os.getenv("METACULUS_API_TOKEN", "")
    if not token:
        return []
    try:
        resp = requests.get(
            f"{BASE_URL}/questions/",
            params={
                "search": term,
                "format": "json",
                "limit":  limit,
                "type":   "forecast",
                "status": "open",
            },
            timeout=10,
            headers=_headers(),
        )
        if resp.status_code in (401, 403):
            return []
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception:
        return []


def _extract_probability(q: dict) -> float | None:
    """
    Extracts community median probability from a Metaculus question.
    Handles multiple response shapes defensively.
    """
    cp = q.get("community_prediction")
    if cp is None:
        return None

    # Shape 1: simple float
    if isinstance(cp, (int, float)):
        p = float(cp)
        return p if 0 < p < 1 else None

    # Shape 2: nested stats object
    if isinstance(cp, dict):
        full = cp.get("full") or {}
        p    = full.get("q2") or full.get("median") or cp.get("q2")
        if p is not None:
            p = float(p)
            return p if 0 < p < 1 else None

    return None


def fetch_for_titles(titles: list[str], results_per_query: int = 6) -> list[dict]:
    """
    Searches Metaculus for open binary questions matching each Kalshi title.
    Returns deduplicated normalized results: {title, probability, source, url, volume}.
    """
    seen: set[int] = set()
    results: list[dict] = []

    for title in titles:
        words     = [w.strip(".,?!:()") for w in title.split()]
        key_words = [w for w in words if w.lower() not in _STOP and len(w) > 2]
        term      = " ".join(key_words[:5])[:60].strip()
        if not term:
            continue

        for q in _search(term, results_per_query):
            qid  = q.get("id")
            prob = _extract_probability(q)
            if qid and qid not in seen and prob is not None:
                seen.add(qid)
                page = q.get("page_url") or f"/questions/{qid}/"
                results.append({
                    "title":       q.get("title", ""),
                    "probability": round(prob, 4),
                    "source":      "Metaculus",
                    "url":         f"https://www.metaculus.com{page}",
                    "volume":      0,
                })

    return results
