"""
tests/test_eval_rescore.py — Tests for analysis/eval_rescore.py.

Mocks core.llm.score_via_api — no live API calls, no cost. Verifies the
harness correctly reconstructs market dicts, calls the scoring pipeline
with temperature pinned, and detects identical vs. differing re-score
runs. The actual live-API determinism proof is run manually/separately
(see the eval-harness build notes) — this suite only proves the
harness's own wiring is correct.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import logger
from analysis import eval_rescore


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=False)
def tmp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(logger, "DB_PATH", db_file)
    logger._init_db()
    return db_file


def _insert(call_id, ticker, direction, market_price, our_estimate=0.40):
    with logger._db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO signals
            (call_id, timestamp, ticker, title, market_price, our_estimate,
             edge, direction, confidence, whale_detected, whale_direction,
             outcome, result, pnl_if_traded, run_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id, datetime.now(timezone.utc).isoformat(), ticker,
            "Test Market", market_price, our_estimate, 0.10,
            direction, "MED", 0, "", "yes", "WIN", 0.5, "run-test",
        ))


_FAKE_CONFIG = {"llm": {"backend": "api", "model": "claude-sonnet-4-6", "max_web_searches": 8}}


# ─── _market_dict_for_ticker ──────────────────────────────────────────────────

def test_market_dict_for_ticker_maps_mid_price(tmp_db):
    _insert("r1", "KXRESCORE1", "YES", 0.42)
    m = eval_rescore._market_dict_for_ticker("KXRESCORE1")
    assert m["mid_price"] == 0.42
    assert m["ticker"] == "KXRESCORE1"


def test_market_dict_for_ticker_missing_returns_none(tmp_db):
    assert eval_rescore._market_dict_for_ticker("KXNOPE") is None


# ─── rescore_dataset ──────────────────────────────────────────────────────────

def test_rescore_dataset_maps_by_ticker(tmp_db):
    _insert("r2", "KXRESCORE2", "YES", 0.30)
    dataset = {"rows": [{"market_id": "KXRESCORE2"}]}

    fake_scores = [{"ticker": "KXRESCORE2", "our_estimate": 0.55, "direction": "YES"}]
    with patch.object(eval_rescore.llm, "score_via_api", return_value=(fake_scores, {})) as mock_call:
        result = eval_rescore.rescore_dataset(dataset, temperature=0.0, config=_FAKE_CONFIG)

    assert result == {"KXRESCORE2": fake_scores[0]}
    # temperature must be threaded through to the scoring call
    _, kwargs = mock_call.call_args
    assert mock_call.call_args[0][2] == _FAKE_CONFIG or mock_call.call_args.kwargs.get("config") == _FAKE_CONFIG
    assert mock_call.call_args.kwargs.get("temperature") == 0.0


def test_rescore_dataset_dispatches_to_cli_backend(tmp_db):
    """config.json's default backend='cli' must call _score_via_cli, not the API."""
    _insert("r5", "KXRESCORECLI", "YES", 0.30)
    dataset = {"rows": [{"market_id": "KXRESCORECLI"}]}
    cli_config = {"llm": {"backend": "cli"}}
    fake_scores = [{"ticker": "KXRESCORECLI", "our_estimate": 0.55, "direction": "YES"}]

    with patch.object(eval_rescore, "_score_via_cli", return_value=fake_scores) as mock_cli, \
         patch.object(eval_rescore.llm, "score_via_api") as mock_api:
        result = eval_rescore.rescore_dataset(dataset, config=cli_config)

    assert result == {"KXRESCORECLI": fake_scores[0]}
    mock_cli.assert_called_once()
    mock_api.assert_not_called()


def test_rescore_dataset_skips_unknown_tickers(tmp_db):
    dataset = {"rows": [{"market_id": "KXUNKNOWN"}]}
    with patch.object(eval_rescore.llm, "score_via_api") as mock_call:
        result = eval_rescore.rescore_dataset(dataset, config=_FAKE_CONFIG)
    assert result == {}
    mock_call.assert_not_called()


# ─── check_determinism ────────────────────────────────────────────────────────

def test_check_determinism_identical_runs(tmp_db):
    _insert("r3", "KXDET1", "YES", 0.30)
    dataset = {"rows": [{"market_id": "KXDET1"}]}
    fake_scores = [{"ticker": "KXDET1", "our_estimate": 0.60, "direction": "YES"}]

    with patch.object(eval_rescore.llm, "score_via_api", return_value=(fake_scores, {})):
        result = eval_rescore.check_determinism(dataset, config=_FAKE_CONFIG)

    assert result["identical"] is True
    assert result["diffs"] == []
    assert result["n_scored"] == 1


def test_check_determinism_detects_diff(tmp_db):
    _insert("r4", "KXDET2", "YES", 0.30)
    dataset = {"rows": [{"market_id": "KXDET2"}]}
    responses = [
        ([{"ticker": "KXDET2", "our_estimate": 0.60, "direction": "YES"}], {}),
        ([{"ticker": "KXDET2", "our_estimate": 0.70, "direction": "YES"}], {}),
    ]

    with patch.object(eval_rescore.llm, "score_via_api", side_effect=responses):
        result = eval_rescore.check_determinism(dataset, config=_FAKE_CONFIG)

    assert result["identical"] is False
    assert len(result["diffs"]) == 1
    assert result["diffs"][0]["ticker"] == "KXDET2"
    assert result["diffs"][0]["run1"] == 0.60
    assert result["diffs"][0]["run2"] == 0.70
