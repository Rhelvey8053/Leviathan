"""
Tests for polymarket_us.py — index building, matching, and cross-market
promotion against Polymarket US's marketSides[] schema (distinct from
international Polymarket's outcomes/outcomePrices arrays).
All tests are offline: no network calls.
"""
import math

import pytest
from unittest.mock import patch

from core import fees
from sources import polymarket_us


# ── Helpers ───────────────────────────────────────────────────────────────────

def _raw(question="Will X definitely happen sometime soon?", sides=None, market_id="1", volume=1000, title=""):
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


def _kalshi(ticker="KXTEST-1", title="Will X definitely happen sometime soon?", mid_price=0.55):
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
    idx = polymarket_us.build_index([_raw("Will X definitely happen sometime soon?", title="Will X definitely happen sometime soon?")])
    assert idx[0]["question"].count("Will X definitely happen sometime soon?") == 1


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
    idx = _idx("Will X definitely happen sometime soon?")
    m = polymarket_us.find_match("Will X definitely happen sometime soon?", idx)
    assert m is not None
    assert m["question"] == "Will X definitely happen sometime soon?"
    assert m["match_score"] >= 0.50


def test_find_match_no_match_below_threshold():
    idx = _idx("Will the stock go up?")
    m = polymarket_us.find_match("Will it rain tomorrow?", idx, threshold=0.90)
    assert m is None


def test_find_match_empty_index():
    assert polymarket_us.find_match("anything", []) is None


def test_find_match_picks_best():
    idx = _idx("Will it rain tomorrow?", "Will X definitely happen sometime soon?")
    m = polymarket_us.find_match("Will X definitely happen sometime soon?", idx)
    assert m is not None
    assert "X definitely happen" in m["question"]


# ── _match_score: MIN_TITLE_WORDS gate ──────────────────────────────────────
# backlog: polymarket-us-tuning-for-real-value, direction (2)'s other half.
# Regression guard against the exact documented failure class: a short,
# generic title (e.g. "Both Teams To Score") can clear min_match_score by
# coincidental word/character overlap alone, not because it's the same
# question -- the 2026-09-14 review's max_fetch 300->1500 experiment
# surfaced 3 such spurious matches before category restriction removed
# that specific source. Gated on word count so this applies regardless of
# which side (Kalshi or Polymarket US) has the short title.

def test_match_score_zero_when_kalshi_title_too_short():
    """An identical short title on both sides would otherwise score 1.0 --
    the gate must still zero it out below MIN_TITLE_WORDS."""
    assert polymarket_us._normalize("Score Win") .__len__() < polymarket_us.MIN_TITLE_WORDS
    assert polymarket_us._match_score("Score Win", "Score Win") == 0.0


def test_match_score_zero_when_poly_title_too_short():
    assert polymarket_us._match_score("Will X definitely happen tomorrow?", "Score Win") == 0.0


def test_match_score_nonzero_at_min_title_words_boundary():
    """Exactly MIN_TITLE_WORDS normalized words on both sides must NOT be
    gated -- the floor is a strict less-than, not less-than-or-equal."""
    title = "Rain snow hail tomorrow"  # normalizes to 4 content words -- above floor
    assert len(polymarket_us._normalize(title)) >= polymarket_us.MIN_TITLE_WORDS
    assert polymarket_us._match_score(title, title) > 0.0


def test_find_match_never_matches_short_generic_titles():
    """End-to-end regression for the documented real failure: a short,
    generic Kalshi title against an unrelated-but-lexically-similar short
    Polymarket US title must not match, even though it would clear a
    lenient threshold on raw score alone."""
    idx = _idx("Both Teams To Score")
    m = polymarket_us.find_match("Both Teams To Score", idx, threshold=0.10)
    assert m is None


def test_find_match_longer_titles_still_match_unaffected():
    """Non-regression: the gate must not break ordinary longer-title
    matching, which is most of what this module actually does."""
    idx = _idx("Will the Federal Reserve cut interest rates in September?")
    m = polymarket_us.find_match(
        "Will the Federal Reserve cut interest rates in September?", idx,
    )
    assert m is not None


# ── match_markets ─────────────────────────────────────────────────────────────

def test_match_markets_returns_match():
    idx = _idx("Will X definitely happen sometime soon?")
    markets = [_kalshi("KXTEST", "Will X definitely happen sometime soon?", mid_price=0.55)]
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
    idx = _idx("Will X definitely happen sometime soon?")
    markets = [_kalshi("KXTEST", "Will X definitely happen sometime soon?", mid_price=0.68)]
    result = polymarket_us.match_markets(markets, idx, _CFG, min_gap=0.15)
    assert "KXTEST" not in result


def test_match_markets_min_gap_passes():
    idx = _idx("Will X definitely happen sometime soon?")
    markets = [_kalshi("KXTEST", "Will X definitely happen sometime soon?", mid_price=0.50)]
    result = polymarket_us.match_markets(markets, idx, _CFG, min_gap=0.15)
    assert "KXTEST" in result


def test_match_markets_no_mid_price_includes_match():
    idx = _idx("Will X definitely happen sometime soon?")
    markets = [{"ticker": "KXTEST", "title": "Will X definitely happen sometime soon?", "mid_price": None}]
    result = polymarket_us.match_markets(markets, idx, _CFG)
    assert "KXTEST" in result
    assert result["KXTEST"]["price_gap"] is None


def test_match_markets_skips_empty_title():
    idx = _idx("Will X definitely happen sometime soon?")
    markets = [{"ticker": "KXTEST", "title": "", "mid_price": 0.5}]
    result = polymarket_us.match_markets(markets, idx, _CFG)
    assert "KXTEST" not in result


# ─── _kalshi_match_title / event_title enrichment ────────────────────────────
# backlog: polymarket-us-tuning-for-real-value, 2026-09-18. A Kalshi ladder
# market's own title omits the city; only the event's own title has it.
# Confirmed live this was the actual reason no genuine positive match had
# ever surfaced in this item's prior testing rounds (2026-09-14/15).

def test_kalshi_match_title_combines_event_title_and_title():
    m = {"title": "82 to 83", "event_title": "Highest temperature in Los Angeles on Sep 18, 2026?"}
    combined = polymarket_us._kalshi_match_title(m)
    assert "Los Angeles" in combined
    assert "82 to 83" in combined


def test_kalshi_match_title_no_event_title_falls_back_to_bare_title():
    m = {"title": "Will X definitely happen sometime soon?"}
    assert polymarket_us._kalshi_match_title(m) == "Will X definitely happen sometime soon?"


def test_kalshi_match_title_does_not_duplicate_title_already_in_event_title():
    m = {"title": "Foo bar baz", "event_title": "Foo bar baz question text"}
    combined = polymarket_us._kalshi_match_title(m)
    assert combined == "Foo bar baz question text"


def test_kalshi_match_title_missing_both_returns_empty_string():
    assert polymarket_us._kalshi_match_title({}) == ""


def test_match_markets_event_title_improves_score_but_does_not_alone_clear_production_threshold():
    """
    Honest documentation of where this fix actually lands, not an
    overclaim. Live-verified 2026-09-18 against real Kalshi/Polymarket US
    weather data (18 real LA/Chicago/SF band markets): the bare title
    scores ~0.45; combining event_title+title (this fix) raises it to
    ~0.55-0.57 -- a real, measured improvement, and enough to clear this
    fixture's 0.50 threshold -- but NOT the real production floor of 0.6.
    Root cause of the remaining gap: appending the band-specific title
    (needed to avoid every band in an event scoring identically -- see
    _kalshi_match_title's own docstring) reintroduces noise via duplicated
    date/temperature words, and Kalshi's own band-number formatting
    ("82-83°") doesn't textually align with Polymarket US's ("82 to 83")
    well enough for the SequenceMatcher/Jaccard combination to close the
    rest of the gap. This is a second, distinct problem from the one this
    fix addresses (missing city) -- band-ladder markets fundamentally need
    numeric-range-aware matching, not fuzzier text matching, to solve
    precisely. Flagged in the backlog as a follow-up, not solved here.
    """
    idx = _idx("Highest temperature in Los Angeles on September 18? — 82 to 83")
    bare = [{"ticker": "KXHIGHLAX-1", "title": "Will the maximum temperature be 82-83° on Sep 18, 2026?",
             "mid_price": 0.55}]
    enriched = [{**bare[0], "event_title": "Highest temperature in Los Angeles on Sep 18, 2026?"}]

    result_bare_fixture     = polymarket_us.match_markets(bare, idx, _CFG)                          # threshold 0.50
    result_enriched_fixture = polymarket_us.match_markets(enriched, idx, _CFG)                       # threshold 0.50
    result_enriched_prod    = polymarket_us.match_markets(enriched, idx, _CFG, min_match_score=0.6)  # real production floor

    assert "KXHIGHLAX-1" not in result_bare_fixture         # bare title: no match even at 0.50
    assert "KXHIGHLAX-1" in result_enriched_fixture          # event_title: clears 0.50 (real improvement)
    assert "KXHIGHLAX-1" not in result_enriched_prod          # but NOT the real 0.6 production floor


def test_match_markets_no_mid_price_excluded_when_gap_floor_set():
    idx = _idx("Will X definitely happen sometime soon?")
    markets = [{"ticker": "KXTEST", "title": "Will X definitely happen sometime soon?", "mid_price": None}]
    result = polymarket_us.match_markets(markets, idx, _CFG, min_gap=0.15)
    assert "KXTEST" not in result


def test_match_markets_net_price_gap_matches_manual_fee_calc():
    idx = _idx("Will X definitely happen sometime soon?")
    markets = [_kalshi("KXTEST", "Will X definitely happen sometime soon?", mid_price=0.55)]
    result = polymarket_us.match_markets(markets, idx, _CFG)
    k_fee = fees.kalshi_fee(0.55, 10)
    p_fee = fees.polymarket_fee(0.70, 10, None)
    expected = round(0.15 - (k_fee + p_fee) / 10, 4)
    assert result["KXTEST"]["net_price_gap"] == pytest.approx(expected, abs=1e-4)
    assert result["KXTEST"]["net_price_gap"] < result["KXTEST"]["price_gap"]


def test_match_markets_net_price_gap_never_flips_sign():
    idx = _idx("Will X definitely happen sometime soon?")
    markets = [_kalshi("KXTEST", "Will X definitely happen sometime soon?", mid_price=0.90)]
    result = polymarket_us.match_markets(markets, idx, _CFG)
    assert result["KXTEST"]["price_gap"] < 0
    assert result["KXTEST"]["net_price_gap"] <= 0


def test_match_markets_min_match_score_override():
    """Title pair deliberately >= MIN_TITLE_WORDS on both sides -- this
    test is about the min_match_score override plumbing, not the
    word-count gate, so it must not be silently zeroed out by that gate
    the way a real short-title pair should be."""
    idx = _idx("Will the United States economy grow significantly this year?")
    markets = [_kalshi("KXTEST", "Will United States GDP rise sometime during 2026?", mid_price=0.50)]
    result_strict = polymarket_us.match_markets(markets, idx, _CFG, min_match_score=0.80)
    result_loose  = polymarket_us.match_markets(markets, idx, _CFG, min_match_score=0.10)
    assert "KXTEST" not in result_strict
    assert "KXTEST" in result_loose


# ── parse_kalshi_band / parse_poly_us_band (backlog: polymarket-us-tuning-
# for-real-value, numeric-band follow-up 2026-09-18) ──────────────────────────

def test_parse_kalshi_band_bounded_range():
    assert polymarket_us.parse_kalshi_band("Will the maximum temperature be 82-83° on Sep 18, 2026?") == (82.0, 83.0)


def test_parse_kalshi_band_above():
    assert polymarket_us.parse_kalshi_band("Will the maximum temperature be >90° on Sep 18, 2026?") == (90.0, math.inf)


def test_parse_kalshi_band_below():
    assert polymarket_us.parse_kalshi_band("Will the maximum temperature be <60° on Sep 18, 2026?") == (-math.inf, 60.0)


def test_parse_kalshi_band_unparseable_returns_none():
    assert polymarket_us.parse_kalshi_band("Will the Fed cut rates in December?") is None


def test_parse_kalshi_band_handles_reversed_range():
    """Defensive: a range written high-to-low still normalizes to (lo, hi)."""
    assert polymarket_us.parse_kalshi_band("Will it be 83-82 on Sep 18?") == (82.0, 83.0)


def test_parse_poly_us_band_bounded_range():
    assert polymarket_us.parse_poly_us_band("82 to 83") == (82.0, 83.0)


def test_parse_poly_us_band_above_variants():
    assert polymarket_us.parse_poly_us_band("90 or above") == (90.0, math.inf)
    assert polymarket_us.parse_poly_us_band("90 or more") == (90.0, math.inf)
    assert polymarket_us.parse_poly_us_band("90 or higher") == (90.0, math.inf)


def test_parse_poly_us_band_below_variants():
    assert polymarket_us.parse_poly_us_band("60 or below") == (-math.inf, 60.0)
    assert polymarket_us.parse_poly_us_band("60 or less") == (-math.inf, 60.0)
    assert polymarket_us.parse_poly_us_band("60 or lower") == (-math.inf, 60.0)


def test_parse_poly_us_band_unparseable_returns_none():
    assert polymarket_us.parse_poly_us_band("Cindy Holscher (D)") is None


# ── match_ladder_markets: exact-band family matching (backlog:
# polymarket-us-tuning-for-real-value, numeric-band follow-up 2026-09-18) ─────

def _ladder_kalshi(ticker, title, event_ticker="KXHIGHLAX-26SEP18", event_title="Highest temperature in Los Angeles on Sep 18, 2026?", mid_price=0.50, category="Weather"):
    return {"ticker": ticker, "title": title, "event_ticker": event_ticker,
            "event_title": event_title, "mid_price": mid_price, "category": category}


def _ladder_poly_raw(base_question, band_title, market_id, yes_price):
    return _raw(question=base_question, title=band_title, market_id=market_id,
                sides=[{"description": "Yes", "price": str(yes_price)},
                       {"description": "No", "price": str(round(1 - yes_price, 2))}])


def test_match_ladder_markets_exact_aligned_city_matches():
    """Chicago-pattern: boundaries align exactly between platforms, so the
    matcher must find a real, price-comparable pair."""
    idx = polymarket_us.build_index([
        _ladder_poly_raw("Highest temperature in Chicago on September 18?", "69 to 71", "1", 0.40),
        _ladder_poly_raw("Highest temperature in Chicago on September 18?", "71 to 73", "2", 0.55),
    ])
    kalshi_markets = [
        _ladder_kalshi("KXHIGHTCHI-1", "Will the maximum temperature be 69-71° on Sep 18, 2026?",
                       event_ticker="KXHIGHTCHI-26SEP18",
                       event_title="Highest temperature in Chicago on Sep 18, 2026?", mid_price=0.35),
    ]
    result = polymarket_us.match_ladder_markets(kalshi_markets, idx, _CFG)
    assert "KXHIGHTCHI-1" in result
    assert result["KXHIGHTCHI-1"]["market_id"] == "1"
    assert result["KXHIGHTCHI-1"]["poly_us_price"] == pytest.approx(0.40)
    assert result["KXHIGHTCHI-1"]["price_gap"] == pytest.approx(0.05)


def test_match_ladder_markets_offset_city_correctly_finds_no_match():
    """LA/SFO-pattern: Kalshi uses even-number boundaries, Polymarket US
    odd -- a [82,84) Kalshi band and an [83,85) Polymarket US band describe
    different real-world outcomes. Must NOT pair them on overlap; must
    return no match at all rather than fabricate a price comparison."""
    idx = polymarket_us.build_index([
        _ladder_poly_raw("Highest temperature in Los Angeles on September 18?", "83 to 85", "1", 0.45),
    ])
    kalshi_markets = [
        _ladder_kalshi("KXHIGHLAX-1", "Will the maximum temperature be 82-84° on Sep 18, 2026?", mid_price=0.50),
    ]
    result = polymarket_us.match_ladder_markets(kalshi_markets, idx, _CFG)
    assert "KXHIGHLAX-1" not in result


def test_match_ladder_markets_open_ended_bands_match():
    idx = polymarket_us.build_index([
        _ladder_poly_raw("Highest temperature in Chicago on September 18?", "77 or above", "1", 0.10),
    ])
    kalshi_markets = [
        _ladder_kalshi("KXHIGHTCHI-1", "Will the maximum temperature be >77° on Sep 18, 2026?",
                       event_ticker="KXHIGHTCHI-26SEP18",
                       event_title="Highest temperature in Chicago on Sep 18, 2026?", mid_price=0.08),
    ]
    result = polymarket_us.match_ladder_markets(kalshi_markets, idx, _CFG)
    assert "KXHIGHTCHI-1" in result
    assert result["KXHIGHTCHI-1"]["market_id"] == "1"


def test_match_ladder_markets_wrong_family_no_cross_city_match():
    """A Chicago Kalshi market must never match an LA Polymarket US family
    even if a band happens to line up numerically -- family match on
    event_title vs base_question gates this before band comparison runs."""
    idx = polymarket_us.build_index([
        _ladder_poly_raw("Highest temperature in Los Angeles on September 18?", "69 to 71", "1", 0.40),
    ])
    kalshi_markets = [
        _ladder_kalshi("KXHIGHTCHI-1", "Will the maximum temperature be 69-71° on Sep 18, 2026?",
                       event_ticker="KXHIGHTCHI-26SEP18",
                       event_title="Highest temperature in Chicago on Sep 18, 2026?", mid_price=0.35),
    ]
    result = polymarket_us.match_ladder_markets(kalshi_markets, idx, _CFG)
    assert "KXHIGHTCHI-1" not in result


def test_match_ladder_markets_missing_event_title_no_match():
    idx = polymarket_us.build_index([
        _ladder_poly_raw("Highest temperature in Chicago on September 18?", "69 to 71", "1", 0.40),
    ])
    kalshi_markets = [{"ticker": "KXTEST-1", "title": "Will the maximum temperature be 69-71° on Sep 18, 2026?",
                        "event_ticker": "KXTEST-26SEP18", "event_title": "", "mid_price": 0.35}]
    result = polymarket_us.match_ladder_markets(kalshi_markets, idx, _CFG)
    assert "KXTEST-1" not in result


def test_match_ladder_markets_unparseable_kalshi_band_no_match():
    """A non-ladder market inside an otherwise-ladder event_ticker (shouldn't
    happen in practice, but the parser gate must hold regardless) is simply
    absent from the result, never guessed."""
    idx = polymarket_us.build_index([
        _ladder_poly_raw("Highest temperature in Chicago on September 18?", "69 to 71", "1", 0.40),
    ])
    kalshi_markets = [_ladder_kalshi("KXHIGHTCHI-1", "Will it be unusually warm on Sep 18, 2026?",
                                      event_ticker="KXHIGHTCHI-26SEP18",
                                      event_title="Highest temperature in Chicago on Sep 18, 2026?")]
    result = polymarket_us.match_ladder_markets(kalshi_markets, idx, _CFG)
    assert "KXHIGHTCHI-1" not in result


def test_match_ladder_markets_net_price_gap_applies_fees():
    idx = polymarket_us.build_index([
        _ladder_poly_raw("Highest temperature in Chicago on September 18?", "69 to 71", "1", 0.40),
    ])
    kalshi_markets = [
        _ladder_kalshi("KXHIGHTCHI-1", "Will the maximum temperature be 69-71° on Sep 18, 2026?",
                       event_ticker="KXHIGHTCHI-26SEP18",
                       event_title="Highest temperature in Chicago on Sep 18, 2026?", mid_price=0.35)]
    cfg = {**_CFG, "betting": {"unit_size": 10}}
    result = polymarket_us.match_ladder_markets(kalshi_markets, idx, cfg)
    row = result["KXHIGHTCHI-1"]
    k_fee = fees.kalshi_fee(0.35, 10)
    p_fee = fees.polymarket_fee(0.40, 10, "Weather")
    expected_net = round(max(abs(row["price_gap"]) - (k_fee + p_fee) / 10, 0.0), 4)
    assert row["net_price_gap"] == pytest.approx(expected_net)


def test_match_ladder_markets_lowest_never_matches_highest_family():
    """Live-verified near-miss (2026-09-18): 'Lowest temperature in San
    Francisco on Sep 18, 2026?' scores 0.737 against Polymarket US's
    'Highest temperature in San Francisco on September 18?' -- only 0.013
    below family_threshold's 0.75, i.e. NOT safely separated by score
    alone. The explicit metric-word guard (_temp_metric) must reject this
    regardless of how close the fuzzy score comes to the threshold."""
    idx = polymarket_us.build_index([
        _ladder_poly_raw("Highest temperature in San Francisco on September 18?", "76 or above", "1", 0.60),
    ])
    kalshi_markets = [
        _ladder_kalshi("KXLOWTSFO-1", "Will the minimum temperature be >76° on Sep 18, 2026?",
                       event_ticker="KXLOWTSFO-26SEP18",
                       event_title="Lowest temperature in San Francisco on Sep 18, 2026?", mid_price=0.55),
    ]
    result = polymarket_us.match_ladder_markets(kalshi_markets, idx, _CFG)
    assert "KXLOWTSFO-1" not in result


def test_temp_metric_recognizes_both_pairs_and_non_temp_returns_none():
    assert polymarket_us._temp_metric("Highest temperature in Miami on Sep 18?") == "high"
    assert polymarket_us._temp_metric("Will the maximum temperature be >76°?") == "high"
    assert polymarket_us._temp_metric("Lowest temperature in Miami on Sep 18?") == "low"
    assert polymarket_us._temp_metric("Will the minimum temperature be <53°?") == "low"
    assert polymarket_us._temp_metric("Kansas Governor Election Winner") is None


def test_match_markets_uses_ladder_path_for_exact_band_and_falls_back_for_others():
    """End-to-end through the public match_markets() entry point: a real
    ladder pair resolves via the exact-band path even though its combined
    event_title+title text would not clear the real 0.6 production
    threshold on its own (see the event_title docstring above), while an
    ordinary non-ladder market in the same call still resolves via the
    original fuzzy-text path."""
    idx = polymarket_us.build_index([
        _ladder_poly_raw("Highest temperature in Chicago on September 18?", "69 to 71", "1", 0.40),
        _raw("Will the Fed cut rates in December this year?", title="", market_id="2",
             sides=[{"description": "Yes", "price": "0.62"}, {"description": "No", "price": "0.38"}]),
    ])
    markets = [
        _ladder_kalshi("KXHIGHTCHI-1", "Will the maximum temperature be 69-71° on Sep 18, 2026?",
                       event_ticker="KXHIGHTCHI-26SEP18",
                       event_title="Highest temperature in Chicago on Sep 18, 2026?", mid_price=0.35),
        _kalshi("KXFED-1", "Will the Fed cut interest rates in December this year?", mid_price=0.58),
    ]
    result = polymarket_us.match_markets(markets, idx, _CFG, min_match_score=0.6)
    assert result["KXHIGHTCHI-1"]["market_id"] == "1"
    assert result["KXFED-1"]["market_id"] == "2"


# ── fetch_and_build_index ─────────────────────────────────────────────────────

def test_fetch_and_build_index_calls_fetch_and_build():
    fake_raw = [_raw("Will Q happen?")]
    with patch.object(polymarket_us, "fetch_markets", return_value=fake_raw) as mock_fetch:
        idx = polymarket_us.fetch_and_build_index(_CFG)
    mock_fetch.assert_called_once_with(100, categories=None)  # max_fetch from _CFG, no categories configured
    assert len(idx) == 1
    assert idx[0]["question"] == "Will Q happen?"


def test_fetch_and_build_index_passes_configured_categories():
    cfg = {"polymarket_us": {**_CFG["polymarket_us"], "categories": ["climate", "politics"]}}
    with patch.object(polymarket_us, "fetch_markets", return_value=[]) as mock_fetch:
        polymarket_us.fetch_and_build_index(cfg)
    mock_fetch.assert_called_once_with(100, categories=["climate", "politics"])


# ── enrich_flagged ────────────────────────────────────────────────────────────

def test_enrich_flagged():
    fake_raw = [_raw("Will X definitely happen sometime soon?")]
    with patch.object(polymarket_us, "fetch_markets", return_value=fake_raw):
        result = polymarket_us.enrich_flagged(
            [_kalshi("KXTEST", "Will X definitely happen sometime soon?", mid_price=0.50)],
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


# ── fetch_markets: categories (backlog: polymarket-us-tuning-for-real-value) ──

def test_fetch_markets_no_categories_makes_one_uncategorized_call():
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"markets": [_raw("A", market_id="1")]}

    with patch.object(polymarket_us.requests, "get", return_value=_Resp()) as mock_get:
        markets = polymarket_us.fetch_markets(limit=10, categories=None)
    assert len(markets) == 1
    mock_get.assert_called_once()
    assert "categories" not in mock_get.call_args.kwargs["params"]


def test_fetch_markets_multiple_categories_uses_one_request_per_category():
    """The gateway API doesn't accept a comma-joined category list (confirmed
    live -- it's matched as one literal string, not an OR filter), so this
    must issue one request per category."""
    calls = []

    class _Resp:
        def __init__(self, cat):
            self._cat = cat
        def raise_for_status(self): pass
        def json(self):
            return {"markets": [_raw(f"Q-{self._cat}", market_id=self._cat)]}

    def fake_get(url, params, timeout):
        calls.append(params.get("categories"))
        return _Resp(params.get("categories"))

    with patch.object(polymarket_us.requests, "get", side_effect=fake_get):
        markets = polymarket_us.fetch_markets(limit=10, categories=["climate", "politics"])
    assert sorted(calls) == ["climate", "politics"]
    assert len(markets) == 2


def test_fetch_markets_categories_dedup_by_market_id():
    """The same market_id returned by two different category calls (a
    market can plausibly carry multiple tags) must not be double-counted."""
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"markets": [_raw("Shared", market_id="dup1")]}

    with patch.object(polymarket_us.requests, "get", return_value=_Resp()):
        markets = polymarket_us.fetch_markets(limit=10, categories=["climate", "politics"])
    assert len(markets) == 1


def test_fetch_markets_categories_splits_limit_across_categories():
    """A limit of 100 across 2 categories should request ~50 per category,
    not 100 each (which would silently multiply the effective fetch size)."""
    seen_limits = []

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"markets": []}

    def fake_get(url, params, timeout):
        seen_limits.append(params["limit"])
        return _Resp()

    with patch.object(polymarket_us.requests, "get", side_effect=fake_get):
        polymarket_us.fetch_markets(limit=100, categories=["climate", "politics"])
    assert all(lim == 50 for lim in seen_limits)
