"""
tests/test_eval_dataset.py — Tests for analysis/eval_dataset.py.

Uses a throwaway SQLite DB (never leviathan.db) and a tmp_path data dir
(never analysis/eval_data/) — same monkeypatch pattern as test_logger.py
and test_mcp_server.py. Freezes go through the MCP server's
get_resolved_track_record tool, same as production use.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import logger
from analysis import eval_dataset


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=False)
def tmp_env(tmp_path, monkeypatch):
    """Fresh throwaway DB + isolated data dir for each test."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(logger, "DB_PATH", db_file)
    logger._init_db()
    monkeypatch.setattr(eval_dataset, "DATA_DIR", tmp_path / "eval_data")
    return tmp_path


def _insert(call_id, ticker, direction, market_price,
            outcome="", result="", pnl=None, edge=0.10, our_estimate=0.40):
    with logger._db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO signals
            (call_id, timestamp, ticker, title, market_price, our_estimate,
             edge, direction, confidence, whale_detected, whale_direction,
             outcome, result, pnl_if_traded, run_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id,
            datetime.now(timezone.utc).isoformat(),
            ticker, "Test Market",
            market_price, our_estimate,
            edge,
            direction, "MED", 0, "",
            outcome, result, pnl,
            "run-test",
        ))


# ─── freeze_dataset ───────────────────────────────────────────────────────────

def test_freeze_dataset_row_count_matches_resolved(tmp_env):
    _insert("e1", "KXEVAL1", "YES", 0.40, outcome="no", result="LOSS")
    _insert("e2", "KXEVAL2", "YES", 0.40, outcome="yes", result="WIN")
    _insert("e3", "KXEVALOPEN", "YES", 0.40)  # unresolved, must be excluded

    payload = eval_dataset.freeze_dataset(version="test-version")
    assert payload["n"] == 2
    assert len(payload["rows"]) == 2


def test_freeze_dataset_row_fields(tmp_env):
    _insert("e4", "KXEVALFIELDS", "YES", 0.30, outcome="yes", result="WIN",
            our_estimate=0.55)
    payload = eval_dataset.freeze_dataset(version="test-version")
    row = payload["rows"][0]
    assert row["market_id"] == "KXEVALFIELDS"
    assert row["our_estimate"] == 0.55
    assert row["market_price"] == 0.30
    assert row["actual_outcome"] == "YES"
    assert row["actual_outcome_binary"] == 1


def test_freeze_dataset_outcome_no_maps_to_binary_zero(tmp_env):
    _insert("e5", "KXEVALNO", "NO", 0.30, outcome="no", result="WIN")
    payload = eval_dataset.freeze_dataset(version="test-version")
    row = payload["rows"][0]
    assert row["actual_outcome"] == "NO"
    assert row["actual_outcome_binary"] == 0


def test_freeze_dataset_writes_dated_and_latest_files(tmp_env):
    _insert("e6", "KXEVALWRITE", "YES", 0.40, outcome="yes", result="WIN")
    eval_dataset.freeze_dataset(version="2099-01-01")
    dated = eval_dataset.DATA_DIR / "resolved_2099-01-01.json"
    latest = eval_dataset.DATA_DIR / "resolved_latest.json"
    assert dated.exists()
    assert latest.exists()
    with open(dated) as f:
        assert json.load(f)["version"] == "2099-01-01"


def test_freeze_dataset_uses_mcp_tool_surface(tmp_env, monkeypatch):
    """Freezing must go through the MCP server's tool, not a second query path."""
    called = {}
    original = eval_dataset._fetch_resolved_rows

    def _spy():
        called["hit"] = True
        return original()

    monkeypatch.setattr(eval_dataset, "_fetch_resolved_rows", _spy)
    _insert("e7", "KXEVALSPY", "YES", 0.40, outcome="yes", result="WIN")
    eval_dataset.freeze_dataset(version="test-version")
    assert called.get("hit") is True


# ─── load_latest ──────────────────────────────────────────────────────────────

def test_load_latest_freezes_if_missing(tmp_env):
    _insert("e8", "KXEVALLATEST", "YES", 0.40, outcome="yes", result="WIN")
    assert not (eval_dataset.DATA_DIR / "resolved_latest.json").exists()
    payload = eval_dataset.load_latest()
    assert payload["n"] == 1


def test_load_latest_reads_existing_file(tmp_env):
    _insert("e9", "KXEVALEXIST", "YES", 0.40, outcome="yes", result="WIN")
    eval_dataset.freeze_dataset(version="first")
    _insert("e10", "KXEVALEXIST2", "YES", 0.40, outcome="yes", result="WIN")
    # load_latest should read the already-frozen file, not re-freeze with e10 included
    payload = eval_dataset.load_latest()
    assert payload["version"] == "first"
    assert payload["n"] == 1
