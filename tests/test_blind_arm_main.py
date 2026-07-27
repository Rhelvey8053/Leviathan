"""
tests/test_blind_arm_main.py — Offline tests for main._sample_for_blind_arm()
(backlog: price-blind-arm).

Pure function, no network/DB/LLM. main.py's own orchestration of the
blind-arm block has no test harness, consistent with this session's other
main.py additions (e.g. _validate_market_shape) -- this covers the one
piece of new logic that's a testable, standalone function.
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
