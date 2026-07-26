"""
tests/test_backlog_checker.py - Offline tests for backlog/checker.py.

All DB tests use a tmp sqlite DB with controlled data. All backlog.json
mutation tests use a tmp copy (tmp_backlog fixture / an inline synthetic
file) -- the real backlog.json is never touched by this file.

run(email_mode=True) DOES persist newly-unlocked status transitions to
disk (fixed 2026-07-25 -- a prior version silently discarded them, so a
scheduled --email run re-reported the same gate as newly unlocked forever;
see test_email_mode_persists_newly_unlocked_status_to_disk).
"""

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
BACKLOG_JSON = ROOT / "backlog" / "backlog.json"
BACKLOG_CHECKER_PY = ROOT / "backlog" / "checker.py"

sys.path.insert(0, str(ROOT))
from backlog.engine import load_backlog
from backlog.checker import (
    compare_statuses,
    compute_metrics,
    evaluate_triggers,
    execute_action,
    format_email_block,
    generate_markdown,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    """Minimal DB with controlled signal data."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE signals (
            call_id TEXT, ticker TEXT, result TEXT, flag_path TEXT, source TEXT
        )
    """)
    # 4 resolved signals across two flag_paths
    conn.executemany(
        "INSERT INTO signals VALUES (?, ?, ?, ?, ?)",
        [
            ("a1", "TICKER1", "WIN",  "EDGE",      "paper"),
            ("a2", "TICKER2", "LOSS", "EDGE",      "paper"),
            ("a3", "TICKER3", "WIN",  "HEURISTIC", "paper"),
            ("a4", "TICKER4", "WIN",  "HEURISTIC", "paper"),
            ("a5", "TICKER5", "",     "EDGE",      "paper"),       # pending
            ("a6", "TICKER6", None,   "EDGE",      "paper"),       # pending
            ("f1", "TICKER7", "",     None,         "real_fill"),  # fill, no result
            ("f2", "TICKER8", "",     None,         "real_fill"),  # fill, no result
        ]
    )
    conn.commit()
    conn.close()
    return db


@pytest.fixture()
def tmp_db_no_smf(tmp_db):
    """Same DB but without SMART_MONEY_FILLS table (tests graceful fallback)."""
    return tmp_db


@pytest.fixture()
def tmp_db_with_smf(tmp_path):
    """DB that includes SMART_MONEY_FILLS."""
    db = tmp_path / "test_smf.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE signals (
            call_id TEXT, ticker TEXT, result TEXT, flag_path TEXT, source TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE smart_money_fills (wallet TEXT, resolved INTEGER)
    """)
    conn.executemany(
        "INSERT INTO signals VALUES (?, ?, ?, ?, ?)",
        [("s1", "T1", "WIN", "EDGE", "paper")] * 30
    )
    conn.executemany(
        "INSERT INTO smart_money_fills VALUES (?, ?)",
        [("walletA", 1)] * 12 + [("walletB", 1)] * 5
    )
    conn.commit()
    conn.close()
    return db


@pytest.fixture()
def backlog_data():
    return load_backlog(BACKLOG_JSON)


@pytest.fixture()
def tmp_backlog(tmp_path):
    dest = tmp_path / "backlog.json"
    shutil.copy(BACKLOG_JSON, dest)
    return dest


# ---------------------------------------------------------------------------
# compute_metrics
# ---------------------------------------------------------------------------

def test_compute_metrics_correct_counts(tmp_db):
    m = compute_metrics(tmp_db)
    assert m["resolved_count"] == 4              # WIN/LOSS only
    assert m["resolved_count_per_category_max"] == 2  # HEURISTIC has 2
    assert m["fills_count"] == 2


def test_compute_metrics_missing_smf_returns_zero(tmp_db_no_smf):
    m = compute_metrics(tmp_db_no_smf)
    assert m["resolved_count_per_wallet_max"] == 0   # no table, no error


def test_compute_metrics_with_smf(tmp_db_with_smf):
    m = compute_metrics(tmp_db_with_smf)
    assert m["resolved_count_per_wallet_max"] == 12  # walletA


# ---------------------------------------------------------------------------
# evaluate_triggers
# ---------------------------------------------------------------------------

def test_evaluate_triggers_unlocks_at_threshold(backlog_data):
    metrics = {"resolved_count": 25, "resolved_count_per_category_max": 0,
               "resolved_count_per_wallet_max": 0, "fills_count": 0}
    results = evaluate_triggers(backlog_data, metrics)
    assert results["brier-tracking"] is True
    assert results["confluence-detection"] is True


def test_evaluate_triggers_stays_locked_below_threshold(backlog_data):
    metrics = {"resolved_count": 24, "resolved_count_per_category_max": 0,
               "resolved_count_per_wallet_max": 0, "fills_count": 0}
    results = evaluate_triggers(backlog_data, metrics)
    assert results["brier-tracking"] is False


def test_evaluate_triggers_two_conditions_both_required(backlog_data):
    # auto-calibration-loop needs resolved_count>=30 AND resolved_count_per_category_max>=15
    metrics_partial = {"resolved_count": 30, "resolved_count_per_category_max": 14,
                       "resolved_count_per_wallet_max": 0, "fills_count": 0}
    results = evaluate_triggers(backlog_data, metrics_partial)
    assert results["auto-calibration-loop"] is False

    metrics_full = {"resolved_count": 30, "resolved_count_per_category_max": 15,
                    "resolved_count_per_wallet_max": 0, "fills_count": 0}
    results = evaluate_triggers(backlog_data, metrics_full)
    # Still False because depends_on [sample-size-gates, brier-tracking] are not "done"
    assert results["auto-calibration-loop"] is False


def test_evaluate_triggers_blocked_stays_locked_even_if_trigger_passes(backlog_data):
    # calibration-curve-dashboard depends on calibration-curve (locked, not done)
    metrics = {"resolved_count": 999, "resolved_count_per_category_max": 999,
               "resolved_count_per_wallet_max": 999, "fills_count": 999}
    results = evaluate_triggers(backlog_data, metrics)
    assert results["calibration-curve-dashboard"] is False


# ---------------------------------------------------------------------------
# compare_statuses
# ---------------------------------------------------------------------------

def test_compare_statuses_returns_newly_unlocked():
    import copy
    backlog = {
        "items": [
            {"id": "item-a", "status": "locked",  "trigger": {"all": []}, "depends_on": []},
            {"id": "item-b", "status": "locked",  "trigger": {"all": []}, "depends_on": []},
            {"id": "item-c", "status": "ready",   "trigger": {"all": []}, "depends_on": []},
            {"id": "item-d", "status": "blocked", "trigger": {"all": []}, "depends_on": ["item-a"]},
        ]
    }
    trigger_results = {"item-a": True, "item-b": False, "item-c": True, "item-d": False}
    newly = compare_statuses(backlog, trigger_results)
    assert newly == ["item-a"]
    assert backlog["items"][0]["status"] == "ready"
    assert backlog["items"][1]["status"] == "locked"   # unchanged
    assert backlog["items"][2]["status"] == "ready"    # was already ready, unchanged


# ---------------------------------------------------------------------------
# generate_markdown
# ---------------------------------------------------------------------------

def test_generate_markdown_contains_sections(backlog_data):
    metrics = {"resolved_count": 4, "resolved_count_per_category_max": 2,
               "resolved_count_per_wallet_max": 0, "fills_count": 2}
    md = generate_markdown(backlog_data, metrics)
    assert "## Ready" in md
    assert "## Locked" in md
    assert "## Blocked" in md
    assert "## Done" in md
    assert "resolved=4" in md
    assert "fills=2" in md


# ---------------------------------------------------------------------------
# format_email_block
# ---------------------------------------------------------------------------

def test_email_block_required_fields(backlog_data):
    metrics = {"resolved_count": 4, "resolved_count_per_category_max": 2,
               "resolved_count_per_wallet_max": 0, "fills_count": 2}
    block = format_email_block(backlog_data, metrics, [])
    assert "=== LEVIATHAN BACKLOG UPDATE ===" in block
    assert "Date:" in block
    assert "Newly Unlocked:" in block
    assert "Live Metrics:" in block
    assert "resolved_count:" in block
    assert "fills_count:" in block
    assert "Full backlog:" in block
    assert "===" in block


def test_email_block_includes_unlocked_item(backlog_data):
    metrics = {"resolved_count": 25, "resolved_count_per_category_max": 0,
               "resolved_count_per_wallet_max": 0, "fills_count": 0}
    block = format_email_block(backlog_data, metrics, ["brier-tracking"])
    assert "brier-tracking" in block
    assert "CONTINUE:brier-tracking" in block
    assert "REVIEW:brier-tracking" in block


# ---------------------------------------------------------------------------
# execute_action stubs
# ---------------------------------------------------------------------------

def test_execute_action_stubs_return_true_and_print(backlog_data, capsys):
    for item in backlog_data["items"]:
        result = execute_action(item)
        assert result is True
    captured = capsys.readouterr()
    assert "[STUB] Execute:" in captured.out


# ---------------------------------------------------------------------------
# --email integration against real DB
# ---------------------------------------------------------------------------

def test_email_mode_exits_zero(tmp_backlog, tmp_db):
    result = subprocess.run(
        [sys.executable, str(BACKLOG_CHECKER_PY), "--email",
         "--file", str(tmp_backlog), "--db", str(tmp_db)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "=== LEVIATHAN BACKLOG UPDATE ===" in result.stdout
    assert "Live Metrics:" in result.stdout


def test_email_mode_persists_newly_unlocked_status_to_disk(tmp_path, tmp_db):
    """
    Regression guard: a prior version of run(email_mode=True) mutated the
    in-memory backlog dict (via compare_statuses) and rendered BACKLOG.md
    from it, but never called save_backlog() in the --email branch --
    only the interactive C/M path did. So backlog.json on disk never
    advanced past "locked", and every subsequent scheduled --email run
    re-evaluated the same already-met trigger and re-reported the same
    item as "Newly Unlocked" forever. This test uses a synthetic backlog
    with one locked item whose trigger is already satisfied by tmp_db's
    4 resolved signals, and asserts the on-disk file actually flips to
    "ready" after a single --email run -- and stays "ready" (not
    re-reported) on a second run.
    """
    synthetic = {
        "version": "1.0",
        "updated": "2026-01-01",
        "metrics_glossary": {"resolved_count": "total resolved signals in DB"},
        "items": [{
            "id": "test-item", "title": "Test", "area": "validation",
            "priority": 1, "status": "locked",
            "trigger": {"all": [{"metric": "resolved_count", "op": ">=", "value": 1}]},
            "depends_on": [], "action": "Do the thing.", "notes": "",
        }],
    }
    backlog_path = tmp_path / "backlog.json"
    backlog_path.write_text(json.dumps(synthetic), encoding="utf-8")

    run1 = subprocess.run(
        [sys.executable, str(BACKLOG_CHECKER_PY), "--email",
         "--file", str(backlog_path), "--db", str(tmp_db)],
        capture_output=True, text=True,
    )
    assert run1.returncode == 0, run1.stderr
    assert "test-item" in run1.stdout

    on_disk = json.loads(backlog_path.read_text(encoding="utf-8"))
    assert on_disk["items"][0]["status"] == "ready"

    run2 = subprocess.run(
        [sys.executable, str(BACKLOG_CHECKER_PY), "--email",
         "--file", str(backlog_path), "--db", str(tmp_db)],
        capture_output=True, text=True,
    )
    assert run2.returncode == 0, run2.stderr
    assert "Newly Unlocked: 0" in run2.stdout
