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
    gate_progress_str,
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
            call_id TEXT, ticker TEXT, result TEXT, flag_path TEXT, source TEXT,
            direction TEXT
        )
    """)
    # 4 resolved (non-PASS, source=paper) signals across two flag_paths,
    # plus rows resolved-count-metric-desync must exclude: a resolved PASS
    # row (PASS resolves LOSS by construction, not a real call), a resolved
    # real_fill row (real trade fills are tracked separately from paper
    # signals by design), and a resolved research_probe row (a different
    # experiment population, not paper signals either).
    conn.executemany(
        "INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("a1", "TICKER1",  "WIN",  "EDGE",      "paper",          "YES"),
            ("a2", "TICKER2",  "LOSS", "EDGE",      "paper",          "NO"),
            ("a3", "TICKER3",  "WIN",  "HEURISTIC", "paper",          "YES"),
            ("a4", "TICKER4",  "WIN",  "HEURISTIC", "paper",          "NO"),
            ("a5", "TICKER5",  "",     "EDGE",      "paper",          "YES"),  # pending
            ("a6", "TICKER6",  None,   "EDGE",      "paper",          "NO"),   # pending
            ("a7", "TICKER9",  "LOSS", "DRIFT",     "paper",          "PASS"), # resolved PASS -- excluded
            ("f1", "TICKER7",  "",     None,        "real_fill",      ""),     # fill, no result
            ("f2", "TICKER8",  "",     None,        "real_fill",      ""),     # fill, no result
            ("f3", "TICKER10", "WIN",  None,        "real_fill",      "YES"),  # resolved real_fill -- excluded
            ("p1", "TICKER11", "WIN",  None,        "research_probe", "YES"),  # resolved research_probe -- excluded
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
            call_id TEXT, ticker TEXT, result TEXT, flag_path TEXT, source TEXT,
            direction TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE smart_money_fills (wallet TEXT, resolved INTEGER)
    """)
    conn.executemany(
        "INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?)",
        [("s1", "T1", "WIN", "EDGE", "paper", "YES")] * 30
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
    assert m["resolved_count"] == 4              # paper, WIN/LOSS, non-PASS only
    assert m["resolved_count_per_category_max"] == 2  # NULL/EDGE/HEURISTIC tied at 2
    assert m["fills_count"] == 3


def test_compute_metrics_resolved_count_excludes_pass_and_non_paper_sources(tmp_db):
    """
    resolved-count-metric-desync (2026-08-16): resolved_count's SQL had two
    gaps vs. core.logger.get_stats()['resolved'] (the number
    scripts/gate_notifier.py's actual gate-unlock emails use) -- no
    direction filter (a7, a resolved PASS row, would have counted; PASS
    resolves LOSS by construction, not a real call) and no source filter
    (f3/p1, resolved real_fill/research_probe rows, would have counted;
    both are tracked as separate populations from paper signals by
    design). Before this fix tmp_db's resolved_count would have been 7,
    not 4.
    """
    m = compute_metrics(tmp_db)
    assert m["resolved_count"] == 4


def test_compute_metrics_missing_smf_returns_zero(tmp_db_no_smf):
    m = compute_metrics(tmp_db_no_smf)
    assert m["resolved_count_per_wallet_max"] == 0   # no table, no error


def test_compute_metrics_missing_smf_flags_data_gap(tmp_db_no_smf):
    """
    2026-08-26: a missing smart_money_fills table used to be indistinguishable
    from a genuine "0 resolved fills across every wallet" -- backlog:
    smart-money-fills-table-missing. _data_gaps makes the difference visible.
    """
    m = compute_metrics(tmp_db_no_smf)
    assert "resolved_count_per_wallet_max" in m["_data_gaps"]


def test_compute_metrics_with_smf(tmp_db_with_smf):
    m = compute_metrics(tmp_db_with_smf)
    assert m["resolved_count_per_wallet_max"] == 12  # walletA


def test_compute_metrics_with_smf_no_data_gap(tmp_db_with_smf):
    """A real, present smart_money_fills table must never be flagged as a data gap."""
    m = compute_metrics(tmp_db_with_smf)
    assert "resolved_count_per_wallet_max" not in m["_data_gaps"]


def test_gate_progress_str_annotates_data_gap():
    """
    gate_progress_str() must distinguish a metric that's genuinely 0 from
    one whose backing table doesn't exist yet -- a bare "not met" would
    wrongly read as "this data was checked and there just isn't any yet."
    """
    item = {"trigger": {"all": [{"metric": "resolved_count_per_wallet_max", "op": ">=", "value": 10}]}}
    metrics = {"resolved_count_per_wallet_max": 0, "_data_gaps": ["resolved_count_per_wallet_max"]}
    result = gate_progress_str(item, metrics)
    assert "not met" in result
    assert "smart-money-fills-table-missing" in result


def test_gate_progress_str_no_annotation_without_gap():
    """A metric with real backing data (even if 0) gets no data-gap annotation."""
    item = {"trigger": {"all": [{"metric": "resolved_count_per_wallet_max", "op": ">=", "value": 10}]}}
    metrics = {"resolved_count_per_wallet_max": 0, "_data_gaps": []}
    result = gate_progress_str(item, metrics)
    assert "not met" in result
    assert "data not tracked" not in result


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


def test_evaluate_triggers_stays_locked_with_undone_dependency():
    """
    Both trigger conditions can be met and the item must still stay locked
    if any depends_on id isn't "done" -- a synthetic backlog, not the
    backlog_data fixture (real backlog.json), since real items' done/not-done
    status legitimately changes over time (kalshi-sdk-migration-implementation,
    2026-08-04: reconciling backlog.json's stale done-status for
    brier-tracking/confluence-detection/multi-sample-scoring broke this
    test's prior version, which had relied on auto-calibration-loop's real
    dependency brier-tracking staying perpetually not-done as fixture data).
    """
    backlog = {
        "items": [
            {"id": "dep-a", "status": "done", "trigger": {"all": []}, "depends_on": []},
            {"id": "dep-b", "status": "locked",
             "trigger": {"all": [{"metric": "resolved_count", "op": ">=", "value": 999}]},
             "depends_on": []},
            {"id": "gated-item", "status": "blocked",
             "trigger": {"all": [{"metric": "resolved_count", "op": ">=", "value": 30},
                                  {"metric": "resolved_count_per_category_max", "op": ">=", "value": 15}]},
             "depends_on": ["dep-a", "dep-b"]},
        ]
    }
    metrics_full = {"resolved_count": 30, "resolved_count_per_category_max": 15,
                    "resolved_count_per_wallet_max": 0, "fills_count": 0}
    results = evaluate_triggers(backlog, metrics_full)
    # Own trigger conditions are both met, but dep-b is still locked (not done).
    assert results["gated-item"] is False


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


def test_manually_set_blocked_with_empty_trigger_does_not_hold():
    """
    Documents a real footgun hit 2026-08-27 (empirical-base-rates-poly):
    manually setting status="blocked" with an empty trigger and empty
    depends_on is NOT a stable state. evaluate_triggers() treats an empty
    trigger.all as vacuously satisfied (no conditions to fail), so the
    very next `python -m backlog.checker` run flips it straight back to
    "ready" via compare_statuses() and reports it as newly unlocked --
    silently, since nothing about this looks wrong from the trigger
    evaluator's point of view. A real "wait for more data" gate MUST use
    a sentinel trigger metric that compute_metrics() deliberately never
    populates (see api_spend_authorized, graphify_corpus_shape_changed,
    sufficient_per_heuristic_label_resolved_data in metrics_glossary) --
    see test_sentinel_trigger_metric_never_auto_unlocks below for the
    stable alternative.
    """
    backlog = {"items": [
        {"id": "fake-blocked", "status": "blocked", "trigger": {"all": []}, "depends_on": []},
    ]}
    trigger_results = evaluate_triggers(backlog, metrics={})
    assert trigger_results["fake-blocked"] is True
    newly = compare_statuses(backlog, trigger_results)
    assert newly == ["fake-blocked"]
    assert backlog["items"][0]["status"] == "ready"


def test_sentinel_trigger_metric_never_auto_unlocks():
    """A trigger metric absent from METRICS_KEYS (and thus never in the
    real metrics dict) reads as 0 via metrics.get(metric, 0) and can never
    satisfy a `== 1` condition on its own -- the stable way to gate an
    item on a condition compute_metrics() doesn't (or shouldn't yet)
    compute, as opposed to a bare empty trigger (see the test above)."""
    backlog = {"items": [
        {"id": "sentinel-gated", "status": "locked",
         "trigger": {"all": [{"metric": "sufficient_per_heuristic_label_resolved_data", "op": "==", "value": 1}]},
         "depends_on": []},
    ]}
    # Even a metrics dict with lots of real signal never contains the sentinel key.
    trigger_results = evaluate_triggers(backlog, metrics={"resolved_count": 999999})
    assert trigger_results["sentinel-gated"] is False
    newly = compare_statuses(backlog, trigger_results)
    assert newly == []
    assert backlog["items"][0]["status"] == "locked"


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

def test_email_mode_exits_zero(tmp_backlog, tmp_db, tmp_path):
    tmp_markdown = tmp_path / "BACKLOG.md"
    result = subprocess.run(
        [sys.executable, str(BACKLOG_CHECKER_PY), "--email",
         "--file", str(tmp_backlog), "--db", str(tmp_db), "--markdown", str(tmp_markdown)],
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

    --markdown is pinned to a tmp path -- omitting it here is exactly what
    let an earlier version of this test silently overwrite the REAL repo
    BACKLOG.md with this synthetic one-item backlog on every test-suite
    run (write_markdown() used to have no destination parameter at all,
    always targeting the hardcoded real path regardless of --file). That
    corrupted file was committed and pushed before it was caught by a user
    noticing the real backlog looked wrong on GitHub.
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
    tmp_markdown = tmp_path / "BACKLOG.md"

    run1 = subprocess.run(
        [sys.executable, str(BACKLOG_CHECKER_PY), "--email",
         "--file", str(backlog_path), "--db", str(tmp_db), "--markdown", str(tmp_markdown)],
        capture_output=True, text=True,
    )
    assert run1.returncode == 0, run1.stderr
    assert "test-item" in run1.stdout
    assert tmp_markdown.exists()  # rendered to the isolated path, not the real repo file

    on_disk = json.loads(backlog_path.read_text(encoding="utf-8"))
    assert on_disk["items"][0]["status"] == "ready"

    run2 = subprocess.run(
        [sys.executable, str(BACKLOG_CHECKER_PY), "--email",
         "--file", str(backlog_path), "--db", str(tmp_db), "--markdown", str(tmp_markdown)],
        capture_output=True, text=True,
    )
    assert run2.returncode == 0, run2.stderr
    assert "Newly Unlocked: 0" in run2.stdout


def test_email_mode_never_writes_real_repo_backlog_md(tmp_path, tmp_db):
    """
    Regression guard, direct: running the real checker.py --email against
    a synthetic backlog must never touch the actual repo's BACKLOG.md,
    regardless of --file. Captures the real file's content before the run
    and asserts it is byte-for-byte unchanged after.
    """
    real_backlog_md = ROOT / "BACKLOG.md"
    before = real_backlog_md.read_text(encoding="utf-8")

    synthetic = {
        "version": "1.0", "updated": "2026-01-01",
        "metrics_glossary": {"resolved_count": "total resolved signals in DB"},
        "items": [{
            "id": "canary-item", "title": "Canary", "area": "validation",
            "priority": 1, "status": "locked",
            "trigger": {"all": [{"metric": "resolved_count", "op": ">=", "value": 1}]},
            "depends_on": [], "action": "Canary action.", "notes": "",
        }],
    }
    backlog_path = tmp_path / "backlog.json"
    backlog_path.write_text(json.dumps(synthetic), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(BACKLOG_CHECKER_PY), "--email",
         "--file", str(backlog_path), "--db", str(tmp_db),
         "--markdown", str(tmp_path / "BACKLOG.md")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    after = real_backlog_md.read_text(encoding="utf-8")
    assert after == before
    assert "canary-item" not in after
