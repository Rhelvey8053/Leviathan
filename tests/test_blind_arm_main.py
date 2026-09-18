"""
tests/test_blind_arm_main.py — Offline tests for main._sample_for_blind_arm()
and main._build_blind_score_row() (backlog: price-blind-arm).

Pure functions, no network/DB/LLM. main.py's own orchestration of the
blind-arm block (the try/except wiring these two together, plus the
logger.log_blind_score() call) has no test harness, consistent with this
session's other main.py additions (e.g. _validate_market_shape) -- this
covers the testable, standalone pieces.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import main


def test_zero_or_negative_n_returns_empty():
    markets = [{"ticker": "A"}, {"ticker": "B"}]
    scored = {"A": {}, "B": {}}
    assert main._sample_for_blind_arm(markets, scored, 0) == []
    assert main._sample_for_blind_arm(markets, scored, -1) == []


def test_only_returns_markets_the_anchored_scorer_actually_scored():
    """A market Claude didn't produce a score for (e.g. dropped by the
    max_markets_per_run batch cap) must never be sent to the blind scorer --
    there'd be no market_price_at_score to compare it against later."""
    markets = [{"ticker": "A"}, {"ticker": "B"}, {"ticker": "C"}]
    scored  = {"A": {"market_price": 0.3}, "C": {"market_price": 0.6}}
    result  = main._sample_for_blind_arm(markets, scored, 5)
    assert [m["ticker"] for m in result] == ["A", "C"]


def test_truncates_to_n():
    markets = [{"ticker": f"T{i}"} for i in range(10)]
    scored  = {f"T{i}": {} for i in range(10)}
    result  = main._sample_for_blind_arm(markets, scored, 3, run_id="run1")
    assert len(result) == 3
    assert set(m["ticker"] for m in result) <= {f"T{i}" for i in range(10)}


def test_same_run_id_is_reproducible():
    """Re-running the same run (e.g. after a crash/retry) samples the same
    markets, rather than a fresh random draw each time."""
    markets = [{"ticker": f"T{i}"} for i in range(20)]
    scored  = {f"T{i}": {} for i in range(20)}
    result1 = main._sample_for_blind_arm(markets, scored, 4, run_id="run-abc")
    result2 = main._sample_for_blind_arm(markets, scored, 4, run_id="run-abc")
    assert [m["ticker"] for m in result1] == [m["ticker"] for m in result2]


def test_does_not_systematically_pick_only_the_head_of_the_list():
    """Regression guard: flagged_markets is pre-sorted by pre-signal
    strength before scoring, so always taking the first n would sample
    only the highest-conviction markets -- exactly the slice where the
    anchored scorer's use of price is most likely already justified. Over
    many different run_ids, the sample should sometimes include markets
    from later in the list, not just the head every time."""
    markets = [{"ticker": f"T{i}"} for i in range(20)]
    scored  = {f"T{i}": {} for i in range(20)}
    ever_included_tail = False
    for i in range(30):
        result = main._sample_for_blind_arm(markets, scored, 3, run_id=f"run-{i}")
        if any(m["ticker"] in ("T15", "T16", "T17", "T18", "T19") for m in result):
            ever_included_tail = True
            break
    assert ever_included_tail


def test_fewer_eligible_markets_than_n_returns_all_of_them():
    markets = [{"ticker": "A"}, {"ticker": "B"}]
    scored  = {"A": {}, "B": {}}
    result  = main._sample_for_blind_arm(markets, scored, 10)
    assert len(result) == 2


# ─── _build_blind_score_row (2026-09-17 title bug fix) ─────────────────────
# Every blind_scores row ever logged had title="" -- RECORD_SCORES_TOOL's
# schema (core/llm.py) has no "title" field at all, so scored_by_ticker's raw
# Claude-score dicts never carry one. title must come from sampled_by_ticker
# (the raw flagged-market dicts _sample_for_blind_arm selected from) instead.

def test_title_comes_from_sampled_market_not_scored_by_ticker():
    br = {"ticker": "KXFOO-1", "estimate": 0.4, "confidence": "MED",
          "reasoning": "x", "sources_checked": []}
    sampled_by_ticker = {"KXFOO-1": {"ticker": "KXFOO-1", "title": "Will foo happen?"}}
    # scored_by_ticker's own dict has NO title key at all -- matches the real
    # shape RECORD_SCORES_TOOL produces, not a hypothetical one.
    scored_by_ticker = {"KXFOO-1": {"ticker": "KXFOO-1", "market_price": 0.55,
                                     "our_estimate": 0.6, "edge": 0.05}}
    row = main._build_blind_score_row(br, sampled_by_ticker, scored_by_ticker, "run1", 0.02)
    assert row["title"] == "Will foo happen?"


def test_market_price_at_score_still_comes_from_scored_by_ticker():
    """Regression guard: only title was broken. market_price_at_score must
    stay sourced from the anchored scorer's own self-reported price, not
    silently switch to the raw market dict alongside the title fix."""
    br = {"ticker": "KXFOO-1", "estimate": 0.4, "confidence": "MED",
          "reasoning": "x", "sources_checked": []}
    sampled_by_ticker = {"KXFOO-1": {"ticker": "KXFOO-1", "title": "Will foo happen?",
                                      "market_price": 0.99}}  # deliberately different
    scored_by_ticker = {"KXFOO-1": {"ticker": "KXFOO-1", "market_price": 0.55}}
    row = main._build_blind_score_row(br, sampled_by_ticker, scored_by_ticker, "run1", 0.02)
    assert row["market_price_at_score"] == 0.55


def test_missing_ticker_in_lookups_yields_blank_title_not_a_crash():
    br = {"ticker": "KXUNKNOWN", "estimate": 0.4, "confidence": "MED",
          "reasoning": "x", "sources_checked": []}
    row = main._build_blind_score_row(br, {}, {}, "run1", None)
    assert row["title"] == ""
    assert row["market_price_at_score"] is None


def test_carries_through_run_id_and_cost_and_blind_fields():
    br = {"ticker": "KXFOO-1", "estimate": 0.42, "confidence": "HIGH",
          "reasoning": "reasoning text", "sources_checked": ["headline"]}
    sampled_by_ticker = {"KXFOO-1": {"ticker": "KXFOO-1", "title": "t"}}
    row = main._build_blind_score_row(br, sampled_by_ticker, {}, "run-abc", 0.031)
    assert row["run_id"] == "run-abc"
    assert row["ticker"] == "KXFOO-1"
    assert row["estimate"] == 0.42
    assert row["confidence"] == "HIGH"
    assert row["reasoning"] == "reasoning text"
    assert row["sources_checked"] == ["headline"]
    assert row["cost_usd"] == 0.031
