"""
Tests for polymarket.py — index building, matching, and cross-market promotion.
All tests are offline: no network calls.
"""
import pytest
from unittest.mock import patch

from sources import polymarket


# ── Helpers ───────────────────────────────────────────────────────────────────

def _raw(question="Will X happen?", outcomes='["Yes","No"]', prices='["0.70","0.30"]', volume=1000):
    return {
        "question":      question,
        "outcomes":      outcomes,
        "outcomePrices": prices,
        "slug":          "will-x-happen",
        "conditionId":   "abc123",
        "volume":        volume,
    }


def _kalshi(ticker="KXTEST-1", title="Will X happen?", mid_price=0.55):
    return {"ticker": ticker, "title": title, "mid_price": mid_price}


_CFG = {
    "polymarket": {
        "max_fetch": 100,
        "min_match_score": 0.50,
        "min_price_gap": 0.0,
        "cross_market_min_gap": 0.15,
        "cross_market_max_candidates": 10,
    }
}


# ── _yes_price ────────────────────────────────────────────────────────────────

def test_yes_price_yes_outcome():
    raw = _raw(outcomes='["Yes","No"]', prices='["0.65","0.35"]')
    assert polymarket._yes_price(raw) == pytest.approx(0.65)


def test_yes_price_true_outcome():
    raw = _raw(outcomes='["True","False"]', prices='["0.80","0.20"]')
    assert polymarket._yes_price(raw) == pytest.approx(0.80)


def test_yes_price_fallback_first():
    raw = _raw(outcomes='["Candidate A","Candidate B"]', prices='["0.60","0.40"]')
    assert polymarket._yes_price(raw) == pytest.approx(0.60)


def test_yes_price_missing_returns_none():
    assert polymarket._yes_price({}) is None


def test_yes_price_list_already_parsed():
    raw = {"outcomes": ["Yes", "No"], "outcomePrices": [0.72, 0.28]}
    assert polymarket._yes_price(raw) == pytest.approx(0.72)


# ── build_index ───────────────────────────────────────────────────────────────

def test_build_index_includes_valid():
    idx = polymarket.build_index([_raw("Will it rain?")])
    assert len(idx) == 1
    assert idx[0]["question"] == "Will it rain?"
    assert idx[0]["yes_price"] == pytest.approx(0.70)
    assert idx[0]["slug"] == "will-x-happen"


def test_build_index_drops_missing_price():
    raw = _raw()
    raw["outcomePrices"] = None
    idx = polymarket.build_index([raw])
    assert idx == []


def test_build_index_drops_missing_question():
    raw = _raw(question="")
    idx = polymarket.build_index([raw])
    assert idx == []


def test_build_index_multiple():
    idx = polymarket.build_index([_raw("Q1"), _raw("Q2")])
    assert len(idx) == 2


# ── find_match ────────────────────────────────────────────────────────────────

def _idx(*questions):
    return polymarket.build_index([_raw(q) for q in questions])


def test_find_match_exact_returns_match():
    idx = _idx("Will X happen?")
    m = polymarket.find_match("Will X happen?", idx)
    assert m is not None
    assert m["question"] == "Will X happen?"
    assert m["match_score"] >= 0.50


def test_find_match_no_match_below_threshold():
    idx = _idx("Will the stock go up?")
    m = polymarket.find_match("Will it rain tomorrow?", idx, threshold=0.90)
    assert m is None


def test_find_match_empty_index():
    assert polymarket.find_match("anything", []) is None


def test_find_match_picks_best():
    idx = _idx("Will it rain tomorrow?", "Will X happen?")
    m = polymarket.find_match("Will X happen?", idx)
    assert m is not None
    assert "X happen" in m["question"]


# ── match_markets ─────────────────────────────────────────────────────────────

def test_match_markets_returns_match():
    idx = _idx("Will X happen?")
    markets = [_kalshi("KXTEST", "Will X happen?", mid_price=0.55)]
    result = polymarket.match_markets(markets, idx, _CFG)
    assert "KXTEST" in result
    assert result["KXTEST"]["poly_price"] == pytest.approx(0.70)
    assert result["KXTEST"]["price_gap"]  == pytest.approx(0.70 - 0.55, abs=1e-3)


def test_match_markets_no_match():
    idx = _idx("Will it rain tomorrow?")
    markets = [_kalshi("KXTEST", "Unrelated market about cheese", mid_price=0.50)]
    result = polymarket.match_markets(markets, idx, _CFG)
    assert "KXTEST" not in result


def test_match_markets_min_gap_filter():
    idx = _idx("Will X happen?")
    # poly=0.70, kalshi=0.68 → gap=0.02 < min_gap=0.15
    markets = [_kalshi("KXTEST", "Will X happen?", mid_price=0.68)]
    result = polymarket.match_markets(markets, idx, _CFG, min_gap=0.15)
    assert "KXTEST" not in result


def test_match_markets_min_gap_passes():
    idx = _idx("Will X happen?")
    # poly=0.70, kalshi=0.50 → gap=0.20 > min_gap=0.15
    markets = [_kalshi("KXTEST", "Will X happen?", mid_price=0.50)]
    result = polymarket.match_markets(markets, idx, _CFG, min_gap=0.15)
    assert "KXTEST" in result


def test_match_markets_no_mid_price_includes_match():
    idx = _idx("Will X happen?")
    markets = [{"ticker": "KXTEST", "title": "Will X happen?", "mid_price": None}]
    result = polymarket.match_markets(markets, idx, _CFG)
    assert "KXTEST" in result
    assert result["KXTEST"]["price_gap"] is None


def test_match_markets_skips_empty_title():
    idx = _idx("Will X happen?")
    markets = [{"ticker": "KXTEST", "title": "", "mid_price": 0.5}]
    result = polymarket.match_markets(markets, idx, _CFG)
    assert "KXTEST" not in result


def test_match_markets_no_mid_price_excluded_when_gap_floor_set():
    """Markets with None mid_price must be excluded when a gap floor is configured."""
    idx = _idx("Will X happen?")
    markets = [{"ticker": "KXTEST", "title": "Will X happen?", "mid_price": None}]
    result = polymarket.match_markets(markets, idx, _CFG, min_gap=0.15)
    assert "KXTEST" not in result


def test_match_markets_no_mid_price_included_when_no_gap_floor():
    """Markets with None mid_price are included when gap floor is 0 (enrich_flagged use case)."""
    idx = _idx("Will X happen?")
    markets = [{"ticker": "KXTEST", "title": "Will X happen?", "mid_price": None}]
    result = polymarket.match_markets(markets, idx, _CFG, min_gap=0.0)
    assert "KXTEST" in result
    assert result["KXTEST"]["price_gap"] is None


def test_match_markets_min_match_score_override():
    idx = _idx("Will the economy grow this year?")
    # Low similarity title — fails at 0.80 threshold
    markets = [_kalshi("KXTEST", "Will GDP rise in 2026?", mid_price=0.50)]
    result_strict = polymarket.match_markets(markets, idx, _CFG, min_match_score=0.80)
    result_loose  = polymarket.match_markets(markets, idx, _CFG, min_match_score=0.10)
    # At 0.10 threshold it should match; at 0.80 it should not
    assert "KXTEST" not in result_strict
    assert "KXTEST" in result_loose


# ── fetch_and_build_index ─────────────────────────────────────────────────────

def test_fetch_and_build_index_calls_fetch_and_build():
    fake_raw = [_raw("Will Q happen?")]
    with patch.object(polymarket, "fetch_markets", return_value=fake_raw) as mock_fetch:
        idx = polymarket.fetch_and_build_index(_CFG)
    mock_fetch.assert_called_once_with(100)  # max_fetch from _CFG
    assert len(idx) == 1
    assert idx[0]["question"] == "Will Q happen?"


# ── enrich_flagged (backward compat) ─────────────────────────────────────────

def test_enrich_flagged_compat():
    fake_raw = [_raw("Will X happen?")]
    with patch.object(polymarket, "fetch_markets", return_value=fake_raw):
        result = polymarket.enrich_flagged(
            [_kalshi("KXTEST", "Will X happen?", mid_price=0.50)],
            _CFG,
        )
    assert "KXTEST" in result
    assert result["KXTEST"]["poly_price"] == pytest.approx(0.70)


# ── cross-market promotion logic ──────────────────────────────────────────────

def test_cross_market_promote_sets_flag_path():
    """
    Simulate the promotion loop from main.py:
    unflagged market with large gap gets flag_path='CROSS_MARKET'.
    """
    idx = polymarket.build_index([_raw("Will X happen?", prices='["0.80","0.20"]')])
    # Kalshi mid=0.50, poly=0.80 → gap=0.30 > 0.15 threshold
    m = {"ticker": "KXTEST", "title": "Will X happen?", "mid_price": 0.50, "flag": False}
    matches = polymarket.match_markets([m], idx, _CFG, min_gap=0.15)
    assert "KXTEST" in matches

    # Apply the promotion
    m["flag"]      = True
    m["flag_path"] = "CROSS_MARKET"
    m["poly"]      = matches["KXTEST"]

    assert m["flag_path"] == "CROSS_MARKET"
    assert m["poly"]["price_gap"] == pytest.approx(0.30, abs=1e-3)


def test_cross_market_promote_small_gap_excluded():
    idx = polymarket.build_index([_raw("Will X happen?", prices='["0.55","0.45"]')])
    # Kalshi mid=0.50, poly=0.55 → gap=0.05 < 0.15
    m = {"ticker": "KXTEST", "title": "Will X happen?", "mid_price": 0.50}
    matches = polymarket.match_markets([m], idx, _CFG, min_gap=0.15)
    assert "KXTEST" not in matches


def test_cross_market_promote_combined_gap_and_score():
    """Simulate main.py: both min_gap=0.15 AND min_match_score=0.65 must be satisfied."""
    idx = polymarket.build_index([_raw("Will X happen?", prices='["0.80","0.20"]')])
    # Large gap (0.30) but title similarity enforced via min_match_score
    m = {"ticker": "KXTEST", "title": "Will X happen?", "mid_price": 0.50}
    # Should match — identical title, big gap
    good = polymarket.match_markets([m], idx, _CFG, min_gap=0.15, min_match_score=0.65)
    assert "KXTEST" in good
    # With a high score threshold this exact title should still match (it's identical)
    also_good = polymarket.match_markets([m], idx, _CFG, min_gap=0.15, min_match_score=0.90)
    assert "KXTEST" in also_good  # identical title scores 1.0, beats any threshold
