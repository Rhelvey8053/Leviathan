"""
tests/test_blind_scorer.py — Offline tests for core/blind_scorer.py
(backlog: price-blind-arm).

No live API calls: score_blind() is tested against a patched
core.blind_scorer._score_blind_via_api. The real API path (core.llm.
score_blind_via_api) can't be exercised end-to-end without a working
Anthropic API key -- see docs/PROGRESS_ARCHIVE.md 2026-07-26.
"""

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import blind_scorer, scorer as live_scorer


def _market(**overrides):
    m = {
        "ticker": "KXFOO-26AUG01",
        "title": "Will foo happen by August?",
        "time_horizon": "MONTHLY",
        "close_time": "2026-08-01T00:00:00Z",
        "mid_price": 0.42,
        "flag_path": "HEURISTIC",
        "base_rate": 0.65,
        "heuristic_direction": "YES",
    }
    m.update(overrides)
    return m


def test_build_prompt_blind_omits_price_and_anchoring_content():
    m = _market(
        poly={"price_gap": 0.20, "poly_price": 0.62, "poly_question": "Will foo?", "match_score": 0.9},
        ext_consensus={"consensus_gap": 0.15, "consensus_dir": "YES", "sources_higher": 3, "sources_lower": 0},
        drift_flag=True, price_drift=0.05,
    )
    prompt = blind_scorer.build_prompt_blind([m])

    for leaked in (
        "Current market price", "42.0%", "FLAG REASON", "SIGNAL QUALITY",
        "POLYMARKET", "CROSS-MARKET", "DRIFT SIGNAL", "HEURISTIC vs POLYMARKET",
        "SIGNAL SUMMARY", "Base rate estimate",
    ):
        assert leaked not in prompt, f"blind prompt leaked anchored content: {leaked!r}"

    assert "KXFOO-26AUG01" in prompt
    assert "Will foo happen by August?" in prompt
    assert "MONTHLY" in prompt
    assert "Closes:" in prompt


def test_build_prompt_blind_keeps_whale_alert():
    m = _market(whale_data={"whale_detected": True, "whale_direction": "YES",
                             "max_trade_size": 500, "avg_trade_size": 120})
    prompt = blind_scorer.build_prompt_blind([m])
    assert "WHALE ALERT" in prompt
    assert "buying YES" in prompt


def test_build_prompt_blind_schema_has_no_price_or_edge_fields():
    assert '"estimate"' in blind_scorer.BLIND_RESPONSE_SCHEMA
    assert '"market_price"' not in blind_scorer.BLIND_RESPONSE_SCHEMA
    assert '"edge":' not in blind_scorer.BLIND_RESPONSE_SCHEMA
    assert '"direction"' not in blind_scorer.BLIND_RESPONSE_SCHEMA


def test_system_prompt_blind_contains_override_and_preserves_unrelated_rules():
    assert "BLIND MODE OVERRIDE" in blind_scorer.SYSTEM_PROMPT_BLIND
    assert "Rule 11" in blind_scorer.SYSTEM_PROMPT_BLIND
    assert "Rule 13" in blind_scorer.SYSTEM_PROMPT_BLIND
    # A rule with no price-anchoring content at all should survive verbatim
    # from the live prompt, not be duplicated/rewritten.
    assert "IPO ANNOUNCEMENT MARKETS" in blind_scorer.SYSTEM_PROMPT_BLIND
    assert "IPO ANNOUNCEMENT MARKETS" in live_scorer.SYSTEM_PROMPT
    # The override must come after (layered on top of), not instead of, the
    # live prompt -- confirms this isn't a hand-copied duplicate.
    assert blind_scorer.SYSTEM_PROMPT_BLIND.startswith(live_scorer.SYSTEM_PROMPT)


def test_score_blind_empty_markets_short_circuits():
    with patch.object(blind_scorer, "_score_blind_via_api") as mock_api:
        result, token_info = blind_scorer.score_blind([], config={})
    assert result == []
    assert token_info == {}
    mock_api.assert_not_called()


def test_score_blind_calls_metered_api_path():
    m = _market()
    fake_scores = [{"ticker": "KXFOO-26AUG01", "estimate": 0.55, "confidence": "MED",
                     "reasoning": "test", "sources_checked": []}]
    with patch.object(blind_scorer, "_score_blind_via_api", return_value=(fake_scores, {"cost_usd": 0.01})) as mock_api:
        result, token_info = blind_scorer.score_blind([m], config={"llm": {"backend": "cli"}})

    assert result == fake_scores
    assert token_info == {"cost_usd": 0.01}
    mock_api.assert_called_once()
    called_system_prompt, called_user_prompt, called_config = mock_api.call_args[0]
    assert called_system_prompt == blind_scorer.SYSTEM_PROMPT_BLIND
    assert "KXFOO-26AUG01" in called_user_prompt
    # Blind scoring must go through the metered API regardless of the
    # configured backend (config still says "cli" above) -- score_blind()
    # ignores config["llm"]["backend"] entirely and always calls the API path.
