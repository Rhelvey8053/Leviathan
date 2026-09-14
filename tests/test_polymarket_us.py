"""
Tests for polymarket_us.py — index building, matching, and cross-market
promotion against Polymarket US's marketSides[] schema (distinct from
international Polymarket's outcomes/outcomePrices arrays).
All tests are offline: no network calls.
"""
import pytest
from unittest.mock import patch

from core import fees
from sources import polymarket_us


# ── Helpers ───────────────────────────────────────────────────────────────────

def _raw(question="Will X happen?", sides=None, market_id="1", volume=1000, title=""):
    if sides is None:
        sides = [
            {"description": "Yes", "price": "0.70"},
            {"description": "No", "price": "0.30"},
        ]
    return {
        "question":    question,
        "title":       title,
        "marketSides": sides,
        "slug":        "will-x-happen",
        "id":          market_id,
        "volume":      volume,
    }


def _kalshi(ticker="KXTEST-1", title="Will X happen?", mid_price=0.55):
    return {"ticker": ticker, "title": title, "mid_price": mid_price}


_CFG = {
    "polymarket_us": {
        "max_fetch": 100,
        "min_match_score": 0.50,
        "min_price_gap": 0.0,
        "cross_market_min_gap": 0.15,
        "cross_market_max_candidates": 10,
    }
}


# ── _yes_price ────────────────────────────────────────────────────────────────

def test_yes_price_yes_side():
    raw = _raw(sides=[{"description": "Yes", "price": "0.65"},
                       {"description": "No", "price": "0.35"}])
    assert polymarket_us._yes_price(raw) == pytest.approx(0.65)


def test_yes_price_true_side():
    raw = _raw(sides=[{"description": "True", "price": "0.80"},
                       {"description": "False", "price": "0.20"}])
    assert polymarket_us._yes_price(raw) == pytest.approx(0.80)


def test_yes_price_fallback_first_side():
    """Sports moneylines use team names, not Yes/No -- fall back to first side."""
    raw = _raw(sides=[{"description": "Los Angeles Chargers", "price": "0.60"},
                       {"description": "Tennessee Titans", "price": "0.40"}])
    assert polymarket_us._yes_price(raw) == pytest.approx(0.60)


def test_yes_price_missing_returns_none():
    assert polymarket_us._yes_price({}) is None


def test_yes_price_empty_sides_returns_none():
    assert polymarket_us._yes_price({"marketSides": []}) is None


def test_yes_price_falls_back_to_no_side_when_yes_price_missing():
    """Confirmed live 2026-09-14: a side's own price can be entirely
    absent while the opposite side has a real value."""
    raw = _raw(sides=[{"description": "Yes"},  # no "price" key at all
                       {"description": "No", "price": "0.02"}])
    assert polymarket_us._yes_price(raw) == pytest.approx(0.98)


def test_yes_price_null_yes_price_falls_back_to_no():
    raw = _raw(sides=[{"description": "Yes", "price": None},
                       {"description": "No", "price": "0.35"}])
    assert polymarket_us._yes_price(raw) == pytest.approx(0.65)


def test_yes_price_both_sides_missing_price_returns_none():
    raw = _raw(sides=[{"description": "Yes"}, {"description": "No"}])
    assert polymarket_us._yes_price(raw) is None


# ── build_index ───────────────────────────────────────────────────────────────

def test_build_index_includes_valid():
    idx = polymarket_us.build_index([_raw("Will it rain?")])
    assert len(idx) == 1
    assert idx[0]["question"] == "Will it rain?"
    assert idx[0]["yes_price"] == pytest.approx(0.70)
    assert idx[0]["slug"] == "will-x-happen"
    assert idx[0]["market_id"] == "1"


def test_build_index_drops_missing_price():
    raw = _raw()
    raw["marketSides"] = None
    idx = polymarket_us.build_index([raw])
    assert idx == []


def test_build_index_drops_missing_question():
    raw = _raw(question="")
    idx = polymarket_us.build_index([raw])
    assert idx == []


def test_build_index_multiple():
    idx = polymarket_us.build_index([_raw("Q1"), _raw("Q2")])
    assert len(idx) == 2


# ── build_index: question+title disambiguation (the real review finding) ──────

def test_build_index_disambiguates_shared_question_via_title():
    """
    The bug this review caught: Polymarket US repeats the identical
    `question` across every market in a family (temperature bands,
    per-candidate elections) -- only `title` tells them apart. Before the
    fix, two distinct band markets sharing a question would be
    indistinguishable to find_match(); after it, their combined
    question+title strings must differ.
    """
    band1 = _raw(question="Highest temperature in Los Angeles on September 13?",
                 title="75 or below", market_id="1",
                 sides=[{"description": "Yes", "price": "0.01"}, {"description": "No", "price": "0.99"}])
    band2 = _raw(question="Highest temperature in Los Angeles on September 13?",
                 title="80 to 81", market_id="2",
                 sides=[{"description": "Yes", "price": "0.30"}, {"description": "No", "price": "0.70"}])
    idx = polymarket_us.build_index([band1, band2])
    assert len(idx) == 2
    questions = {e["question"] for e in idx}
    assert len(questions) == 2  # must NOT collide
    assert "75 or below" in "".join(questions)
    assert "80 to 81" in "".join(questions)


def test_build_index_election_candidates_disambiguated_by_title():
    d = _raw(question="Kansas Governor Election Winner", title="Cindy Holscher (D)", market_id="1")
    r = _raw(question="Kansas Governor Election Winner", title="Republican Nominee", market_id="2")
    idx = polymarket_us.build_index([d, r])
    questions = {e["question"] for e in idx}
    assert len(questions) == 2


def test_build_index_no_title_uses_question_alone():
    """A genuinely single, non-family binary market has no distinguishing title."""
    idx = polymarket_us.build_index([_raw("Will the Fed cut rates in December?", title="")])
    assert idx[0]["question"] == "Will the Fed cut rates in December?"


def test_build_index_title_matching_question_not_duplicated():
    """When title already restates the question (simple binary markets),
    don't glue it on again."""
    idx = polymarket_us.build_index([_raw("Will X happen?", title="Will X happen?")])
    assert idx[0]["question"].count("Will X happen?") == 1


def test_find_match_distinguishes_correct_band_after_fix():
    """End-to-end: a Kalshi title naming a specific band must match that
    band, not an arbitrary sibling sharing the same base question."""
    band_cold = _raw(question="Highest temperature in Los Angeles on September 13?",
                      title="75 or below", market_id="1",
                      sides=[{"description": "Yes", "price": "0.01"}, {"description": "No", "price": "0.99"}])
    band_hot = _raw(question="Highest temperature in Los Angeles on September 13?",
                     title="80 to 81", market_id="2",
                     sides=[{"description": "Yes", "price": "0.30"}, {"description": "No", "price": "0.70"}])
    idx = polymarket_us.build_index([band_cold, band_hot])
    m = polymarket_us.find_match("Will the highest temperature in Los Angeles be 80 to 81 on September 13?", idx)
    assert m is not None
    assert m["market_id"] == "2"
    assert m["yes_price"] == pytest.approx(0.30)


# ── find_match ────────────────────────────────────────────────────────────────

def _idx(*questions):
    return polymarket_us.build_index([_raw(q) for q in questions])


def test_find_match_exact_returns_match():
    idx = _idx("Will X happen?")
    m = polymarket_us.find_match("Will X happen?", idx)
    assert m is not None
    assert m["question"] == "Will X happen?"
    assert m["match_score"] >= 0.50


def test_find_match_no_match_below_threshold():
    idx = _idx("Will the stock go up?")
    m = polymarket_us.find_match("Will it rain tomorrow?", idx, threshold=0.90)
    assert m is None


def test_find_match_empty_index():
    assert polymarket_us.find_match("anything", []) is None


def test_find_match_picks_best():
    idx = _idx("Will it rain tomorrow?", "Will X happen?")
    m = polymarket_us.find_match("Will X happen?", idx)
    assert m is not None
    assert "X happen" in m["question"]


# ── match_markets ─────────────────────────────────────────────────────────────

def test_match_markets_returns_match():
    idx = _idx("Will X happen?")
    markets = [_kalshi("KXTEST", "Will X happen?", mid_price=0.55)]
    result = polymarket_us.match_markets(markets, idx, _CFG)
    assert "KXTEST" in result
    assert result["KXTEST"]["poly_us_price"] == pytest.approx(0.70)
    assert result["KXTEST"]["price_gap"]     == pytest.approx(0.70 - 0.55, abs=1e-3)


def test_match_markets_no_match():
    idx = _idx("Will it rain tomorrow?")
    markets = [_kalshi("KXTEST", "Unrelated market about cheese", mid_price=0.50)]
    result = polymarket_us.match_markets(markets, idx, _CFG)
    assert "KXTEST" not in result


def test_match_markets_min_gap_filter():
    idx = _idx("Will X happen?")
    markets = [_kalshi("KXTEST", "Will X happen?", mid_price=0.68)]
    result = polymarket_us.match_markets(markets, idx, _CFG, min_gap=0.15)
    assert "KXTEST" not in result


def test_match_markets_min_gap_passes():
    idx = _idx("Will X happen?")
    markets = [_kalshi("KXTEST", "Will X happen?", mid_price=0.50)]
    result = polymarket_us.match_markets(markets, idx, _CFG, min_gap=0.15)
    assert "KXTEST" in result


def test_match_markets_no_mid_price_includes_match():
    idx = _idx("Will X happen?")
    markets = [{"ticker": "KXTEST", "title": "Will X happen?", "mid_price": None}]
    result = polymarket_us.match_markets(markets, idx, _CFG)
    assert "KXTEST" in result
    assert result["KXTEST"]["price_gap"] is None


def test_match_markets_skips_empty_title():
    idx = _idx("Will X happen?")
    markets = [{"ticker": "KXTEST", "title": "", "mid_price": 0.5}]
    result = polymarket_us.match_markets(markets, idx, _CFG)
    assert "KXTEST" not in result


def test_match_markets_no_mid_price_excluded_when_gap_floor_set():
    idx = _idx("Will X happen?")
    markets = [{"ticker": "KXTEST", "title": "Will X happen?", "mid_price": None}]
    result = polymarket_us.match_markets(markets, idx, _CFG, min_gap=0.15)
    assert "KXTEST" not in result


def test_match_markets_net_price_gap_matches_manual_fee_calc():
    idx = _idx("Will X happen?")
    markets = [_kalshi("KXTEST", "Will X happen?", mid_price=0.55)]
    result = polymarket_us.match_markets(markets, idx, _CFG)
    k_fee = fees.kalshi_fee(0.55, 10)
    p_fee = fees.polymarket_fee(0.70, 10, None)
    expected = round(0.15 - (k_fee + p_fee) / 10, 4)
    assert result["KXTEST"]["net_price_gap"] == pytest.approx(expected, abs=1e-4)
    assert result["KXTEST"]["net_price_gap"] < result["KXTEST"]["price_gap"]


def test_match_markets_net_price_gap_never_flips_sign():
    idx = _idx("Will X happen?")
    markets = [_kalshi("KXTEST", "Will X happen?", mid_price=0.90)]
    result = polymarket_us.match_markets(markets, idx, _CFG)
    assert result["KXTEST"]["price_gap"] < 0
    assert result["KXTEST"]["net_price_gap"] <= 0


def test_match_markets_min_match_score_override():
    idx = _idx("Will the economy grow this year?")
    markets = [_kalshi("KXTEST", "Will GDP rise in 2026?", mid_price=0.50)]
    result_strict = polymarket_us.match_markets(markets, idx, _CFG, min_match_score=0.80)
    result_loose  = polymarket_us.match_markets(markets, idx, _CFG, min_match_score=0.10)
    assert "KXTEST" not in result_strict
    assert "KXTEST" in result_loose


# ── fetch_and_build_index ─────────────────────────────────────────────────────

def test_fetch_and_build_index_calls_fetch_and_build():
    fake_raw = [_raw("Will Q happen?")]
    with patch.object(polymarket_us, "fetch_markets", return_value=fake_raw) as mock_fetch:
        idx = polymarket_us.fetch_and_build_index(_CFG)
    mock_fetch.assert_called_once_with(100)  # max_fetch from _CFG
    assert len(idx) == 1
    assert idx[0]["question"] == "Will Q happen?"


# ── enrich_flagged ────────────────────────────────────────────────────────────

def test_enrich_flagged():
    fake_raw = [_raw("Will X happen?")]
    with patch.object(polymarket_us, "fetch_markets", return_value=fake_raw):
        result = polymarket_us.enrich_flagged(
            [_kalshi("KXTEST", "Will X happen?", mid_price=0.50)],
            _CFG,
        )
    assert "KXTEST" in result
    assert result["KXTEST"]["poly_us_price"] == pytest.approx(0.70)


# ── fetch_markets (network-mocked) ────────────────────────────────────────────

def test_fetch_markets_unwraps_markets_key():
    """Polymarket US's response shape is {"markets": [...]}, unlike
    international Polymarket's bare list -- fetch_markets() must unwrap it."""
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"markets": [_raw("A"), _raw("B")]}

    with patch.object(polymarket_us.requests, "get", return_value=_Resp()):
        markets = polymarket_us.fetch_markets(limit=10)
    assert len(markets) == 2


def test_fetch_markets_handles_request_failure():
    with patch.object(polymarket_us.requests, "get", side_effect=Exception("boom")):
        markets = polymarket_us.fetch_markets(limit=10)
    assert markets == []
