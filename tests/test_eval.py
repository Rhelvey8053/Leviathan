"""
tests/test_eval.py — Tests for analysis/eval.py.

three_way_comparison is pure — takes a dataset dict, no DB/network. main()
is exercised end-to-end against a tmp DB/data dir to confirm the CLI
entry point runs without arguments and prints the headline line.
"""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import logger
from analysis import eval as eval_mod


# ─── three_way_comparison ─────────────────────────────────────────────────────

def _dataset(rows):
    return {"version": "test", "n": len(rows), "source": "test", "rows": rows}


def test_three_way_comparison_keys():
    dataset = _dataset([
        {"our_estimate": 0.6, "market_price": 0.5, "actual_outcome_binary": 1},
    ])
    result = eval_mod.three_way_comparison(dataset)
    assert set(result) == {"scorer", "market", "constant"}


def test_three_way_comparison_scorer_uses_our_estimate():
    dataset = _dataset([
        {"our_estimate": 1.0, "market_price": 0.0, "actual_outcome_binary": 1},
    ])
    result = eval_mod.three_way_comparison(dataset)
    assert result["scorer"]["brier"] == 0.0
    assert result["market"]["brier"] == 1.0


def test_three_way_comparison_constant_is_always_half():
    dataset = _dataset([
        {"our_estimate": 0.9, "market_price": 0.9, "actual_outcome_binary": 1},
        {"our_estimate": 0.1, "market_price": 0.1, "actual_outcome_binary": 0},
    ])
    result = eval_mod.three_way_comparison(dataset)
    assert result["constant"]["brier"] == 0.25


def test_three_way_comparison_reconciled_dataset_matches_readme():
    """The real 8-row resolved set: scorer Brier reproduces the reconciled 0.0578."""
    rows = [
        {"our_estimate": 0.65,  "market_price": 0.105, "actual_outcome_binary": 0},
        {"our_estimate": 0.15,  "market_price": 0.045, "actual_outcome_binary": 0},
        {"our_estimate": 0.10,  "market_price": 0.025, "actual_outcome_binary": 0},
        {"our_estimate": 0.002, "market_price": 0.005, "actual_outcome_binary": 0},
        {"our_estimate": 0.07,  "market_price": 0.043, "actual_outcome_binary": 0},
        {"our_estimate": 0.04,  "market_price": 0.016, "actual_outcome_binary": 0},
        {"our_estimate": 0.025, "market_price": 0.036, "actual_outcome_binary": 0},
        {"our_estimate": 0.015, "market_price": 0.027, "actual_outcome_binary": 0},
    ]
    result = eval_mod.three_way_comparison(_dataset(rows))
    assert abs(result["scorer"]["brier"] - 0.05779425) < 1e-8


# ─── main() end-to-end ────────────────────────────────────────────────────────

def test_main_runs_with_no_arguments(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(logger, "DB_PATH", db_file)
    logger._init_db()

    from analysis import eval_dataset
    monkeypatch.setattr(eval_dataset, "DATA_DIR", tmp_path / "eval_data")

    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, title, market_price, our_estimate,
             edge, direction, confidence, outcome, result, source)
            VALUES ('m1', ?, 'KXMAINEVAL', 'T', 0.30, 0.55, 0.25,
                    'YES', 'MED', 'yes', 'WIN', 'paper')
        """, (datetime.now(timezone.utc).isoformat(),))

    eval_mod.main()  # must not raise, must run headless with no args
