"""
tests/test_cross_model.py — Tests for core/cross_model.py.

requests.post is mocked throughout -- no real network calls, no dependency
on OmniRoute actually running.
"""

from unittest.mock import patch, MagicMock

from core import cross_model


def _market(ticker="KXTEST-01", title="Will X happen?", mid_price=0.40):
    return {"ticker": ticker, "title": title, "mid_price": mid_price}


def _our_score(estimate=0.55, direction="YES", confidence="MED"):
    return {"our_estimate": estimate, "direction": direction, "confidence": confidence}


def _mock_response(content: str, model="big-pickle", status_ok=True):
    resp = MagicMock()
    resp.raise_for_status = MagicMock() if status_ok else MagicMock(side_effect=Exception("HTTP error"))
    resp.json.return_value = {
        "model": model,
        "choices": [{"message": {"content": content}}],
    }
    return resp


# ─── disabled by default ──────────────────────────────────────────────────────

def test_returns_none_when_not_enabled():
    """No config at all -- must not attempt any HTTP call."""
    with patch("core.cross_model.requests.post") as mock_post:
        result = cross_model.get_opinion(_market(), _our_score(), config=None)
    assert result is None
    mock_post.assert_not_called()


def test_returns_none_when_explicitly_disabled():
    with patch("core.cross_model.requests.post") as mock_post:
        result = cross_model.get_opinion(_market(), _our_score(), {"cross_model": {"enabled": False}})
    assert result is None
    mock_post.assert_not_called()


# ─── happy path ────────────────────────────────────────────────────────────────

def test_accepts_valid_response():
    good_reply = '{"direction": "YES", "estimate": 0.62, "reasoning": "test reasoning"}'
    with patch("core.cross_model.requests.post", return_value=_mock_response(good_reply)):
        result = cross_model.get_opinion(_market(), _our_score(), {"cross_model": {"enabled": True}})
    assert result == {
        "model": "big-pickle",
        "direction": "YES",
        "estimate": 0.62,
        "reasoning": "test reasoning",
    }


def test_extracts_json_from_markdown_fence():
    """An unknown-quality free model is more likely than Claude to wrap its reply."""
    fenced_reply = 'Sure, here you go:\n```json\n{"direction": "NO", "estimate": 0.2, "reasoning": "r"}\n```'
    with patch("core.cross_model.requests.post", return_value=_mock_response(fenced_reply)):
        result = cross_model.get_opinion(_market(), _our_score(), {"cross_model": {"enabled": True}})
    assert result["direction"] == "NO"
    assert result["estimate"] == 0.2


def test_normalizes_direction_case():
    good_reply = '{"direction": "yes", "estimate": 0.7, "reasoning": "r"}'
    with patch("core.cross_model.requests.post", return_value=_mock_response(good_reply)):
        result = cross_model.get_opinion(_market(), _our_score(), {"cross_model": {"enabled": True}})
    assert result["direction"] == "YES"


# ─── failure modes are all non-fatal ──────────────────────────────────────────

def test_returns_none_on_connection_error():
    with patch("core.cross_model.requests.post", side_effect=ConnectionError("refused")):
        result = cross_model.get_opinion(_market(), _our_score(), {"cross_model": {"enabled": True}})
    assert result is None


def test_returns_none_on_timeout():
    import requests as _requests
    with patch("core.cross_model.requests.post", side_effect=_requests.exceptions.ReadTimeout("timed out")):
        result = cross_model.get_opinion(_market(), _our_score(), {"cross_model": {"enabled": True}})
    assert result is None


def test_returns_none_on_http_error_status():
    with patch("core.cross_model.requests.post", return_value=_mock_response("{}", status_ok=False)):
        result = cross_model.get_opinion(_market(), _our_score(), {"cross_model": {"enabled": True}})
    assert result is None


def test_returns_none_on_non_json_reply():
    with patch("core.cross_model.requests.post", return_value=_mock_response("I refuse to answer that.")):
        result = cross_model.get_opinion(_market(), _our_score(), {"cross_model": {"enabled": True}})
    assert result is None


def test_returns_none_on_invalid_direction():
    bad_reply = '{"direction": "MAYBE", "estimate": 0.5, "reasoning": "r"}'
    with patch("core.cross_model.requests.post", return_value=_mock_response(bad_reply)):
        result = cross_model.get_opinion(_market(), _our_score(), {"cross_model": {"enabled": True}})
    assert result is None


def test_returns_none_when_response_json_is_a_list_not_object():
    with patch("core.cross_model.requests.post", return_value=_mock_response("[1, 2, 3]")):
        result = cross_model.get_opinion(_market(), _our_score(), {"cross_model": {"enabled": True}})
    assert result is None


# ─── config wiring ─────────────────────────────────────────────────────────────

def test_uses_default_base_url_model_timeout_when_unspecified():
    good_reply = '{"direction": "PASS", "estimate": 0.5, "reasoning": "r"}'
    with patch("core.cross_model.requests.post", return_value=_mock_response(good_reply)) as mock_post:
        cross_model.get_opinion(_market(), _our_score(), {"cross_model": {"enabled": True}})
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0] == f"{cross_model.DEFAULT_BASE_URL}/chat/completions"
    assert call_kwargs[1]["json"]["model"] == cross_model.DEFAULT_MODEL
    assert call_kwargs[1]["timeout"] == cross_model.DEFAULT_TIMEOUT_S


def test_uses_configured_base_url_model_timeout():
    good_reply = '{"direction": "PASS", "estimate": 0.5, "reasoning": "r"}'
    cfg = {"cross_model": {
        "enabled": True,
        "base_url": "http://192.168.1.5:20128/v1",
        "model": "kimi/kimi-k2.6",
        "timeout_s": 45,
    }}
    with patch("core.cross_model.requests.post", return_value=_mock_response(good_reply)) as mock_post:
        cross_model.get_opinion(_market(), _our_score(), cfg)
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0] == "http://192.168.1.5:20128/v1/chat/completions"
    assert call_kwargs[1]["json"]["model"] == "kimi/kimi-k2.6"
    assert call_kwargs[1]["timeout"] == 45


def test_prompt_includes_our_own_estimate_but_asks_for_independent_answer():
    """The other model must see our estimate (to agree/disagree with) but the
    prompt must not instruct it to simply repeat it back."""
    good_reply = '{"direction": "YES", "estimate": 0.6, "reasoning": "r"}'
    with patch("core.cross_model.requests.post", return_value=_mock_response(good_reply)) as mock_post:
        cross_model.get_opinion(_market(), _our_score(estimate=0.55), {"cross_model": {"enabled": True}})
    prompt = mock_post.call_args[1]["json"]["messages"][0]["content"]
    assert "0.55" in prompt
    assert "Independently" in prompt
    assert "KXTEST-01" in prompt
