"""
Offline tests for core/llm.py — score_via_api and probe_via_api.

All tests mock the anthropic client. No live API calls made.
Run: python -m pytest tests/test_llm.py -q
"""

import pytest
from unittest.mock import MagicMock, patch, call


# ── Helpers ───────────────────────────────────────────────────────────────────

def _valid_score(**overrides):
    base = {
        "ticker":          "KXTEST-01",
        "market_price":    0.30,
        "our_estimate":    0.55,
        "edge":            0.25,
        "direction":       "YES",
        "confidence":      "MED",
        "reasoning":       "Strong evidence.",
        "sources_checked": ["reuters.com"],
    }
    base.update(overrides)
    return base


def _make_tool_use_block(name: str, input_data: dict):
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = input_data
    return block


def _make_response(blocks, stop_reason="tool_use"):
    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = stop_reason
    resp.usage = MagicMock(
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    return resp


def _make_config(backend="api"):
    return {
        "llm": {
            "backend": backend,
            "model": "claude-sonnet-4-6",
            "max_web_searches": 4,
        }
    }


# ── score_via_api tests ───────────────────────────────────────────────────────

class TestScoreViaApi:

    def test_happy_path_returns_scores(self):
        """record_scores block in first response — scores returned unchanged."""
        score = _valid_score()
        block = _make_tool_use_block("record_scores", {"scores": [score]})
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            from core.llm import score_via_api
            scores, token_info = score_via_api("sys", "user", _make_config())

        assert len(scores) == 1
        assert scores[0]["ticker"] == "KXTEST-01"
        assert "cost_usd" in token_info

    def test_missing_required_field_raises(self):
        """Score missing 'reasoning' field raises ValueError naming the ticker."""
        bad = _valid_score()
        del bad["reasoning"]
        block = _make_tool_use_block("record_scores", {"scores": [bad]})
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            from core.llm import score_via_api
            with pytest.raises(ValueError, match="KXTEST-01"):
                score_via_api("sys", "user", _make_config())

    def test_bad_direction_enum_raises(self):
        """Score with invalid direction enum raises ValueError naming the ticker."""
        bad   = _valid_score(direction="MAYBE")
        block = _make_tool_use_block("record_scores", {"scores": [bad]})
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            from core.llm import score_via_api
            with pytest.raises(ValueError, match="KXTEST-01"):
                score_via_api("sys", "user", _make_config())

    def test_bad_confidence_enum_raises(self):
        """Score with invalid confidence enum raises ValueError naming the ticker."""
        bad   = _valid_score(confidence="SUPER_HIGH")
        block = _make_tool_use_block("record_scores", {"scores": [bad]})
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            from core.llm import score_via_api
            with pytest.raises(ValueError, match="KXTEST-01"):
                score_via_api("sys", "user", _make_config())

    def test_force_tool_when_no_block_in_first_response(self):
        """If first response has no record_scores block, forced second call is made."""
        score = _valid_score()
        # First response: only a text block, no record_scores
        text_block      = MagicMock()
        text_block.type = "text"
        text_block.name = None
        first_resp = _make_response([text_block], stop_reason="end_turn")

        # Forced response: record_scores block present
        forced_block = _make_tool_use_block("record_scores", {"scores": [score]})
        forced_resp  = _make_response([forced_block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.side_effect = [first_resp, forced_resp]

            from core.llm import score_via_api
            scores, _ = score_via_api("sys", "user", _make_config())

        assert mock_client.messages.create.call_count == 2
        assert scores[0]["ticker"] == "KXTEST-01"

    def test_api_error_twice_then_success(self):
        """Retries twice on APIError; third attempt succeeds."""
        import anthropic as _ant
        score = _valid_score()
        block = _make_tool_use_block("record_scores", {"scores": [score]})
        good_resp = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn, \
             patch("core.llm.time.sleep"):
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.side_effect = [
                _ant.APIStatusError("err", response=MagicMock(status_code=500), body={}),
                _ant.APIStatusError("err", response=MagicMock(status_code=500), body={}),
                good_resp,
            ]

            from core.llm import score_via_api
            scores, _ = score_via_api("sys", "user", _make_config())

        assert scores[0]["ticker"] == "KXTEST-01"
        assert mock_client.messages.create.call_count == 3

    def test_api_error_three_times_raises_runtime_error(self):
        """Three consecutive APIErrors raise RuntimeError."""
        import anthropic as _ant

        with patch("core.llm._make_client") as mock_client_fn, \
             patch("core.llm.time.sleep"):
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.side_effect = _ant.APIStatusError(
                "err", response=MagicMock(status_code=500), body={}
            )

            from core.llm import score_via_api
            with pytest.raises(RuntimeError, match="score_via_api"):
                score_via_api("sys", "user", _make_config())

        assert mock_client.messages.create.call_count == 3

    def test_multiple_scores_all_validated(self):
        """Multiple valid scores are returned; all fields present."""
        scores_input = [
            _valid_score(ticker="KXTEST-01"),
            _valid_score(ticker="KXTEST-02", direction="NO", confidence="HIGH"),
            _valid_score(ticker="KXTEST-03", direction="PASS", confidence="LOW"),
        ]
        block = _make_tool_use_block("record_scores", {"scores": scores_input})
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            from core.llm import score_via_api
            scores, _ = score_via_api("sys", "user", _make_config())

        assert len(scores) == 3
        tickers = [s["ticker"] for s in scores]
        assert "KXTEST-01" in tickers
        assert "KXTEST-03" in tickers


# ── probe_via_api tests ───────────────────────────────────────────────────────

class TestProbeViaApi:

    def _valid_probe_input(self, **overrides):
        base = {
            "ticker":              "KXTEST-01",
            "claude_estimate":     0.55,
            "predicted_direction": "YES",
            "confidence":          "MED",
            "rationale":           "Evidence found.",
        }
        base.update(overrides)
        return base

    def test_happy_path_returns_probe(self):
        probe_input = self._valid_probe_input()
        block = _make_tool_use_block("record_probe", probe_input)
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            from core.llm import probe_via_api
            result, token_info = probe_via_api("sys", "user", _make_config())

        assert result["ticker"] == "KXTEST-01"
        assert result["claude_estimate"] == 0.55
        assert "cost_usd" in token_info

    def test_force_tool_when_no_probe_block(self):
        """Forced second call when first response has no record_probe block."""
        probe_input = self._valid_probe_input()
        text_block      = MagicMock()
        text_block.type = "text"
        text_block.name = None
        first_resp = _make_response([text_block], stop_reason="end_turn")

        forced_block = _make_tool_use_block("record_probe", probe_input)
        forced_resp  = _make_response([forced_block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.side_effect = [first_resp, forced_resp]

            from core.llm import probe_via_api
            result, _ = probe_via_api("sys", "user", _make_config())

        assert mock_client.messages.create.call_count == 2
        assert result["ticker"] == "KXTEST-01"

    def test_api_error_three_times_raises(self):
        """Three consecutive APIErrors raise RuntimeError."""
        import anthropic as _ant

        with patch("core.llm._make_client") as mock_client_fn, \
             patch("core.llm.time.sleep"):
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.side_effect = _ant.APIStatusError(
                "err", response=MagicMock(status_code=500), body={}
            )

            from core.llm import probe_via_api
            with pytest.raises(RuntimeError, match="probe_via_api"):
                probe_via_api("sys", "user", _make_config())


# ── backend="cli" regression pin ─────────────────────────────────────────────

class TestBackendCliRegression:

    def test_score_markets_cli_does_not_call_api_client(self):
        """When backend=cli, score_markets routes to _score_via_cli, not API."""
        config = {"llm": {"backend": "cli"}, "scoring": {"min_pre_claude_lv": 0, "max_markets_per_run": 1}}
        market = {
            "ticker": "KXTEST-CLI",
            "title": "Test",
            "mid_price": 0.4,
            "yes_bid": 0.38,
            "yes_ask": 0.42,
            "volume": 1000,
            "open_interest": 50,
        }

        with patch("core.scorer._score_via_cli", return_value=[]) as mock_cli, \
             patch("core.llm._make_client") as mock_client_fn:
            from core.scorer import score_markets
            score_markets([market], config)

        mock_cli.assert_called_once()
        mock_client_fn.assert_not_called()
