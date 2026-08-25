"""
tests/test_rescore_shortlist.py — Offline tests for
main._rescore_shortlist_for_clean_sources() (GOAL_phase2-6_decisions.md
Decision 1).

Pure function apart from its two collaborators (report.determine_top_
shortlist, scorer.rescore_single_market), both mocked here -- no network,
no DB, no LLM. main.py's own orchestration around this call (the try/except
+ print + run_meta accounting) has no test harness, consistent with this
codebase's other main.py additions (_sample_for_blind_arm) -- this covers
the one piece of new logic that's a testable, standalone function.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import main


def _sig(ticker, sources=None):
    return {"ticker": ticker, "sources": sources or []}


def _market(ticker):
    return {"ticker": ticker, "title": f"Will {ticker} happen?", "mid_price": 0.3}


def test_no_shortlist_returns_zeroed_result():
    with patch("core.report.determine_top_shortlist", return_value=[]):
        result = main._rescore_shortlist_for_clean_sources([], [], {})
    assert result == {"shortlist_size": 0, "rescored_count": 0, "tokens": 0, "cost_usd": 0.0}


def test_rescored_signal_gets_fresh_sources():
    sig = _sig("KXA", sources=[{"url": "https://batch-shared.com", "title": "shared"}])
    fresh_score = {"ticker": "KXA", "sources": [{"url": "https://clean.com", "title": "clean"}]}
    with patch("core.report.determine_top_shortlist", return_value=[sig]), \
         patch("core.scorer.rescore_single_market", return_value=(fresh_score, {"cost_usd": 0.02, "input_tokens": 500, "output_tokens": 100})):
        result = main._rescore_shortlist_for_clean_sources([sig], [_market("KXA")], {})

    assert sig["sources"] == [{"url": "https://clean.com", "title": "clean"}]
    assert result == {"shortlist_size": 1, "rescored_count": 1, "tokens": 600, "cost_usd": 0.02}


def test_market_not_found_skipped_without_crashing():
    """A shortlisted ticker missing from flagged_markets (shouldn't happen,
    but must not raise) is skipped -- its batch-scored sources stay as-is."""
    sig = _sig("KXMISSING", sources=[{"url": "https://original.com"}])
    with patch("core.report.determine_top_shortlist", return_value=[sig]):
        result = main._rescore_shortlist_for_clean_sources([sig], [], {})  # no matching market

    assert sig["sources"] == [{"url": "https://original.com"}]  # untouched
    assert result["rescored_count"] == 0
    assert result["shortlist_size"] == 1


def test_empty_rescore_result_keeps_batch_sources():
    """rescore_single_market returning (None, token_info) -- e.g. the API
    declined to score this ticker alone -- must not wipe out the pick's
    existing batch-scored sources."""
    sig = _sig("KXA", sources=[{"url": "https://batch-shared.com"}])
    with patch("core.report.determine_top_shortlist", return_value=[sig]), \
         patch("core.scorer.rescore_single_market", return_value=(None, {"cost_usd": 0.01})):
        result = main._rescore_shortlist_for_clean_sources([sig], [_market("KXA")], {})

    assert sig["sources"] == [{"url": "https://batch-shared.com"}]  # untouched
    assert result["rescored_count"] == 0
    assert result["cost_usd"] == 0.01  # still billed even though no fresh score came back


def test_cost_and_tokens_accumulate_across_multiple_picks():
    sig_a = _sig("KXA")
    sig_b = _sig("KXB")
    fresh_a = {"ticker": "KXA", "sources": [{"url": "https://a.com"}]}
    fresh_b = {"ticker": "KXB", "sources": [{"url": "https://b.com"}]}
    with patch("core.report.determine_top_shortlist", return_value=[sig_a, sig_b]), \
         patch("core.scorer.rescore_single_market", side_effect=[
             (fresh_a, {"cost_usd": 0.01, "input_tokens": 100, "output_tokens": 50}),
             (fresh_b, {"cost_usd": 0.02, "input_tokens": 200, "output_tokens": 60}),
         ]):
        result = main._rescore_shortlist_for_clean_sources(
            [sig_a, sig_b], [_market("KXA"), _market("KXB")], {},
        )

    assert result["rescored_count"] == 2
    assert result["tokens"] == 410
    assert result["cost_usd"] == pytest.approx(0.03)


def test_calibration_and_flag_cal_passed_through():
    sig = _sig("KXA")
    cal, flag_cal = {"HIGH": {}}, {"DRIFT": {}}
    with patch("core.report.determine_top_shortlist", return_value=[sig]), \
         patch("core.scorer.rescore_single_market", return_value=(None, {})) as mock_rescore:
        main._rescore_shortlist_for_clean_sources(
            [sig], [_market("KXA")], {}, calibration=cal, flag_cal=flag_cal,
        )
    _, kwargs = mock_rescore.call_args
    assert kwargs["calibration"] is cal
    assert kwargs["flag_cal"] is flag_cal
