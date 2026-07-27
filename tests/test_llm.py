"""
Offline tests for core/llm.py — score_via_api and probe_via_api.

All tests mock the anthropic client. No live API calls made.
Run: python -m pytest tests/test_llm.py -q
"""

import pytest
from unittest.mock import MagicMock, patch, call


@pytest.fixture(autouse=True)
def _isolate_daily_cost_state(tmp_path, monkeypatch):
    """
    Every test in this file gets a throwaway daily-cost state file --
    never touches data/llm_daily_cost.json on disk. Applies to all tests
    here, not just the cost-ceiling ones, since score_via_api/probe_via_api
    now always accumulate cost as a side effect of returning.
    """
    import core.llm as llm
    monkeypatch.setattr(llm, "DAILY_COST_STATE_PATH", str(tmp_path / "llm_daily_cost.json"))


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

    def test_force_tool_call_prices_both_calls_not_just_the_forced_one(self):
        """
        Regression guard: when the first call doesn't call the tool, BOTH
        billed calls' tokens must be counted in the returned cost_usd --
        a prior version priced only the forced call, silently dropping the
        first call's tokens (typically the larger of the two, since it
        carries the full system prompt + any web search) from both the
        return value and the persisted daily ceiling total.
        """
        score = _valid_score()
        text_block      = MagicMock()
        text_block.type = "text"
        text_block.name = None
        first_resp = _make_response([text_block], stop_reason="end_turn")
        first_resp.usage = MagicMock(
            input_tokens=50_000, output_tokens=2_000,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )

        forced_block = _make_tool_use_block("record_scores", {"scores": [score]})
        forced_resp  = _make_response([forced_block])
        forced_resp.usage = MagicMock(
            input_tokens=200, output_tokens=100,
            cache_creation_input_tokens=0, cache_read_input_tokens=0,
        )

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.side_effect = [first_resp, forced_resp]

            from core.llm import score_via_api
            _, token_info = score_via_api("sys", "user", _make_config())

        assert token_info["input_tokens"] == 50_000 + 200
        assert token_info["output_tokens"] == 2_000 + 100
        # Sonnet 4.6 pricing: $3/M input, $15/M output.
        expected_cost = (50_200 * 3.00 + 2_100 * 15.00) / 1_000_000
        assert token_info["cost_usd"] == pytest.approx(expected_cost, abs=1e-6)

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


# ── llm-cost-ceiling ──────────────────────────────────────────────────────────

class TestDailyCostState:
    """Low-level state helpers: get_daily_cost_usd / _accumulate_daily_cost."""

    def test_get_daily_cost_usd_zero_when_no_state_file(self):
        from core.llm import get_daily_cost_usd
        assert get_daily_cost_usd() == 0.0

    def test_accumulate_adds_and_persists(self):
        from core.llm import _accumulate_daily_cost, get_daily_cost_usd
        total1 = _accumulate_daily_cost(1.50)
        total2 = _accumulate_daily_cost(0.25)
        assert total1 == pytest.approx(1.50)
        assert total2 == pytest.approx(1.75)
        assert get_daily_cost_usd() == pytest.approx(1.75)

    def test_get_daily_cost_usd_resets_when_state_is_stale(self):
        """A persisted total from a prior day must not leak into today's figure."""
        import core.llm as llm
        with open(llm.DAILY_COST_STATE_PATH, "w", encoding="utf-8") as f:
            import json
            json.dump({"date": "2020-01-01", "total_cost_usd": 999.0}, f)
        assert llm.get_daily_cost_usd() == 0.0

    def test_accumulate_discards_stale_total_on_new_day(self):
        """A new day's accumulation starts from 0, not from yesterday's total."""
        import core.llm as llm
        import json
        with open(llm.DAILY_COST_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"date": "2020-01-01", "total_cost_usd": 999.0}, f)
        new_total = llm._accumulate_daily_cost(2.0)
        assert new_total == pytest.approx(2.0)


class TestCostCeilingCheck:
    """_check_cost_ceiling: the pre-flight guard called by both API functions."""

    def test_passes_silently_when_under_ceiling(self):
        from core.llm import _check_cost_ceiling
        _check_cost_ceiling({"llm": {"daily_cost_ceiling_usd": 5.0}})  # no raise

    def test_raises_when_already_at_ceiling(self):
        from core.llm import _check_cost_ceiling, _accumulate_daily_cost, LLMCostCeilingExceeded
        _accumulate_daily_cost(5.0)
        with pytest.raises(LLMCostCeilingExceeded):
            _check_cost_ceiling({"llm": {"daily_cost_ceiling_usd": 5.0}})

    def test_raises_when_over_ceiling(self):
        from core.llm import _check_cost_ceiling, _accumulate_daily_cost, LLMCostCeilingExceeded
        _accumulate_daily_cost(7.5)
        with pytest.raises(LLMCostCeilingExceeded):
            _check_cost_ceiling({"llm": {"daily_cost_ceiling_usd": 5.0}})

    def test_uses_default_ceiling_when_not_configured(self):
        """A config with no daily_cost_ceiling_usd falls back to DEFAULT_DAILY_COST_CEILING_USD."""
        from core.llm import _check_cost_ceiling, _accumulate_daily_cost, DEFAULT_DAILY_COST_CEILING_USD
        _accumulate_daily_cost(0.01)
        assert DEFAULT_DAILY_COST_CEILING_USD > 0.01
        _check_cost_ceiling({"llm": {}})  # no raise -- well under the default


class TestCostCeilingIntegration:
    """score_via_api/probe_via_api actually enforce and accumulate the ceiling."""

    def test_score_via_api_blocked_when_ceiling_already_breached(self):
        """No API call is attempted once the ceiling has already been reached."""
        from core.llm import _accumulate_daily_cost, LLMCostCeilingExceeded, score_via_api
        _accumulate_daily_cost(10.0)
        config = _make_config()
        config["llm"]["daily_cost_ceiling_usd"] = 5.0

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            with pytest.raises(LLMCostCeilingExceeded):
                score_via_api("sys", "user", config)
            mock_client.messages.create.assert_not_called()

    def test_probe_via_api_blocked_when_ceiling_already_breached(self):
        from core.llm import _accumulate_daily_cost, LLMCostCeilingExceeded, probe_via_api
        _accumulate_daily_cost(10.0)
        config = _make_config()
        config["llm"]["daily_cost_ceiling_usd"] = 5.0

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            with pytest.raises(LLMCostCeilingExceeded):
                probe_via_api("sys", "user", config)
            mock_client.messages.create.assert_not_called()

    def test_score_via_api_accumulates_cost_after_success(self):
        """A successful call's cost is added to the running daily total."""
        from core.llm import get_daily_cost_usd, score_via_api
        score = _valid_score()
        block = _make_tool_use_block("record_scores", {"scores": [score]})
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            assert get_daily_cost_usd() == 0.0
            _, token_info = score_via_api("sys", "user", _make_config())

        assert get_daily_cost_usd() == pytest.approx(token_info["cost_usd"])
        assert get_daily_cost_usd() > 0.0

    def test_token_info_includes_daily_total_usd(self):
        """Returned token_info carries the running total, matching get_daily_cost_usd()."""
        from core.llm import get_daily_cost_usd, score_via_api
        score = _valid_score()
        block = _make_tool_use_block("record_scores", {"scores": [score]})
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            _, token_info = score_via_api("sys", "user", _make_config())

        assert "daily_total_usd" in token_info
        assert token_info["daily_total_usd"] == pytest.approx(get_daily_cost_usd())

    def test_second_call_accumulates_on_top_of_first(self):
        """Two successful calls in the same day sum their costs."""
        from core.llm import get_daily_cost_usd, score_via_api
        score = _valid_score()
        block = _make_tool_use_block("record_scores", {"scores": [score]})
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            _, info1 = score_via_api("sys", "user", _make_config())
            _, info2 = score_via_api("sys", "user", _make_config())

        assert info2["daily_total_usd"] == pytest.approx(info1["cost_usd"] + info2["cost_usd"])


# ── score_blind_via_api tests (price-blind-arm) ──────────────────────────────

def _valid_blind_score(**overrides):
    base = {
        "ticker":          "KXTEST-01",
        "estimate":        0.55,
        "confidence":      "MED",
        "reasoning":       "Strong evidence.",
        "sources_checked": ["reuters.com"],
    }
    base.update(overrides)
    return base


class TestScoreBlindViaApi:

    def test_tool_schema_has_no_price_or_edge_fields(self):
        from core.llm import RECORD_BLIND_SCORES_TOOL
        props = RECORD_BLIND_SCORES_TOOL["input_schema"]["properties"]["scores"]["items"]["properties"]
        assert "estimate" in props
        assert "market_price" not in props
        assert "edge" not in props
        assert "direction" not in props

    def test_happy_path_returns_scores(self):
        score = _valid_blind_score()
        block = _make_tool_use_block("record_blind_scores", {"scores": [score]})
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            from core.llm import score_blind_via_api
            scores, token_info = score_blind_via_api("sys", "user", _make_config())

        assert len(scores) == 1
        assert scores[0]["ticker"] == "KXTEST-01"
        assert "cost_usd" in token_info

    def test_missing_required_field_raises(self):
        bad = _valid_blind_score()
        del bad["reasoning"]
        block = _make_tool_use_block("record_blind_scores", {"scores": [bad]})
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            from core.llm import score_blind_via_api
            with pytest.raises(ValueError, match="KXTEST-01"):
                score_blind_via_api("sys", "user", _make_config())

    def test_bad_confidence_enum_raises(self):
        bad   = _valid_blind_score(confidence="SUPER_HIGH")
        block = _make_tool_use_block("record_blind_scores", {"scores": [bad]})
        resp  = _make_response([block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.return_value = resp

            from core.llm import score_blind_via_api
            with pytest.raises(ValueError, match="KXTEST-01"):
                score_blind_via_api("sys", "user", _make_config())

    def test_force_tool_when_no_block_in_first_response(self):
        score = _valid_blind_score()
        text_block      = MagicMock()
        text_block.type = "text"
        text_block.name = None
        first_resp = _make_response([text_block], stop_reason="end_turn")

        forced_block = _make_tool_use_block("record_blind_scores", {"scores": [score]})
        forced_resp  = _make_response([forced_block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.side_effect = [first_resp, forced_resp]

            from core.llm import score_blind_via_api
            scores, _ = score_blind_via_api("sys", "user", _make_config())

        assert mock_client.messages.create.call_count == 2
        assert scores[0]["ticker"] == "KXTEST-01"

    def test_blocked_when_ceiling_already_breached(self):
        """Same cost-ceiling gate as score_via_api/probe_via_api -- blind-arm
        sampling must not be a way to bypass the daily spend cap."""
        from core.llm import _accumulate_daily_cost, LLMCostCeilingExceeded, score_blind_via_api
        _accumulate_daily_cost(10.0)
        config = _make_config()
        config["llm"]["daily_cost_ceiling_usd"] = 5.0

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            with pytest.raises(LLMCostCeilingExceeded):
                score_blind_via_api("sys", "user", config)
            mock_client.messages.create.assert_not_called()

    def test_accumulates_into_the_same_daily_total_as_score_via_api(self):
        """Blind-arm spend and main-scan-via-API spend share one ceiling, not two."""
        from core.llm import get_daily_cost_usd, score_via_api, score_blind_via_api
        anchored_block = _make_tool_use_block("record_scores", {"scores": [_valid_score()]})
        anchored_resp  = _make_response([anchored_block])
        blind_block = _make_tool_use_block("record_blind_scores", {"scores": [_valid_blind_score()]})
        blind_resp  = _make_response([blind_block])

        with patch("core.llm._make_client") as mock_client_fn:
            mock_client = MagicMock()
            mock_client_fn.return_value = mock_client
            mock_client.messages.create.side_effect = [anchored_resp, blind_resp]

            _, info1 = score_via_api("sys", "user", _make_config())
            _, info2 = score_blind_via_api("sys", "user", _make_config())

        assert get_daily_cost_usd() == pytest.approx(info1["cost_usd"] + info2["cost_usd"])


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
