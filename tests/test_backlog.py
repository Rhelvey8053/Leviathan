"""
tests/test_backlog.py - Offline tests for backlog/backlog.json and backlog/engine.py.

All tests operate on a temp copy of backlog.json; the real file is never mutated.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
BACKLOG_JSON = ROOT / "backlog" / "backlog.json"
BACKLOG_PY = ROOT / "backlog" / "engine.py"

sys.path.insert(0, str(ROOT))
from backlog.engine import (
    determine_status,
    load_backlog,
    parse_trigger,
    save_backlog,
    validate_item,
)


@pytest.fixture()
def backlog_data():
    return load_backlog(BACKLOG_JSON)


@pytest.fixture()
def tmp_backlog(tmp_path):
    dest = tmp_path / "backlog.json"
    shutil.copy(BACKLOG_JSON, dest)
    return dest


# ---------------------------------------------------------------------------
# backlog.json structure
# ---------------------------------------------------------------------------

def test_parses_and_75_items(backlog_data):
    """
    75, not 55: the monday.com sync Phase 0 discovery (2026-08-17,
    docs/monday_sync_discovery.md) found the monday board -- seeded early
    on from a version of BACKLOG.md that still carried rich hand-written
    Done-entry prose directly -- had 20 items with no backlog.json
    counterpart at all (ci-kalshi-auth-env-2026-08, db-audit-2026-08,
    down-ballot-election-recalibration, export-validation-pass-exclusion,
    ext-signal-activation, graphify-skill-evaluation, heuristic-backtest-tool,
    heuristic_label-vs-base_rate-desync, hurricane-recalibration,
    log-pass-schema-parity, near-dated-markets-supplement,
    near-dated-window-chunking, price-threshold-recalibration,
    production-delivery-milestone-recalibration, prop-market-skill-filter,
    show-renewal-recalibration, sports-award-recalibration,
    subscriber-report-rework-2026-08, subscriber-report-wiring,
    win-catchall-recalibration) -- the same gap the 43->55 fix (below)
    closed for 2 items, just 20 more of them, orphaned once
    backlog/checker.py's generate_markdown() took over rendering BACKLOG.md
    purely from backlog.json and stopped preserving any hand-written entry
    that never had a matching json item. Backfilled from git history (each
    item's original BACKLOG.md-addition commit, not the monday board's own
    Detail column, which turned out to already be truncated to ~450 chars
    at seed time) rather than fabricated -- every action/area/priority
    value traces to a real commit, cross-checked against what the board
    had independently captured at seed time (matched on all 20). Per Reed's
    explicit decision on the monday-sync handoff (backfill now, not leave
    unmanaged), gated per that doc's own Phase 0 sign-off requirement.

    55, not the original 43: kalshi-sdk-migration-implementation and
    kalshi-sdk-evaluation-2026-08 (2026-08-04) were both real, completed
    Done items that had only ever been hand-added to BACKLOG.md directly,
    never to backlog.json -- added here to close that gap so a future
    backlog/checker.py regenerate of BACKLOG.md doesn't silently delete
    their writeups (see also the brier-tracking/confluence-detection/
    multi-sample-scoring status reconciliation in the same commit).
    whale-flag-lv-guarantee, whale-only-none-direction-crash,
    resolved-count-metric-desync, whale-actionability-scorecard, and
    signal-csv-strategy-review-2026-08 (2026-08-04 through 2026-08-16,
    same-project follow-ups) were each added to both files together from
    the start, avoiding a repeat of the same gap. per-heuristic-scorecard
    and heuristic-sunsetting flipped locked->ready in the same pass
    (resolved_count_per_category_max cleared 15) without changing the
    total item count. streamlit-dashboard-2026-08 and
    signal-scan-log-split-2026-08 (2026-08-16) shipped as commits without
    a backlog entry at the time -- found and closed during a full-project
    audit the next day (2026-08-17), same gap pattern as the original 43->50 fix.
    citations-provenance-grounding, net-edge-fee-depth-model, and
    cross-venue-expansion (2026-08-17) came from a roadmap-reconciliation
    handoff -- two of four originally-proposed items were added (one
    narrowed after verifying net_edge's real fee handling first), one was
    dropped as already-implemented (scorer-websearch-grounding: web search
    is already live on both scoring backends), matching this file's own
    "verify before adding" precedent.
    """
    assert len(backlog_data["items"]) == 75


def test_all_ids_unique(backlog_data):
    ids = [i["id"] for i in backlog_data["items"]]
    assert len(ids) == len(set(ids))


def test_every_item_valid(backlog_data):
    items = backlog_data["items"]
    glossary = backlog_data["metrics_glossary"]
    for item in items:
        others = [i for i in items if i["id"] != item["id"]]
        errs = validate_item(item, others, glossary)
        assert errs == [], f"Item {item['id']!r} failed: {errs}"


def test_every_depends_on_references_real_id(backlog_data):
    ids = {i["id"] for i in backlog_data["items"]}
    for item in backlog_data["items"]:
        for dep in item.get("depends_on", []):
            assert dep in ids, f"{item['id']} depends_on unknown id {dep!r}"


def test_every_trigger_metric_in_glossary(backlog_data):
    glossary = backlog_data["metrics_glossary"]
    for item in backlog_data["items"]:
        for cond in item.get("trigger", {}).get("all", []):
            assert cond["metric"] in glossary, (
                f"{item['id']} uses unknown metric {cond['metric']!r}"
            )


def test_item21_has_2_triggers_and_2_depends(backlog_data):
    item21 = next(i for i in backlog_data["items"] if i["id"] == "auto-calibration-loop")
    assert len(item21["trigger"]["all"]) == 2
    assert len(item21["depends_on"]) == 2


# ---------------------------------------------------------------------------
# parse_trigger
# ---------------------------------------------------------------------------

def test_parse_trigger_manual():
    assert parse_trigger("manual") == {"all": []}


def test_parse_trigger_empty_string():
    assert parse_trigger("") == {"all": []}


def test_parse_trigger_two_conditions():
    result = parse_trigger("resolved_count>=30,resolved_count_per_category_max>=15")
    conds = result["all"]
    assert len(conds) == 2
    assert conds[0] == {"metric": "resolved_count", "op": ">=", "value": 30}
    assert conds[1] == {"metric": "resolved_count_per_category_max", "op": ">=", "value": 15}
    assert isinstance(conds[0]["value"], int)
    assert isinstance(conds[1]["value"], int)


def test_parse_trigger_invalid_raises():
    with pytest.raises(ValueError):
        parse_trigger("bad condition")


# ---------------------------------------------------------------------------
# determine_status
# ---------------------------------------------------------------------------

def test_determine_status_ready():
    assert determine_status({"all": []}, []) == "ready"


def test_determine_status_locked():
    trigger = parse_trigger("resolved_count>=25")
    assert determine_status(trigger, []) == "locked"


def test_determine_status_blocked_any_deps():
    assert determine_status({"all": []}, ["some-id"]) == "blocked"


def test_determine_status_blocked_overrides_trigger():
    trigger = parse_trigger("resolved_count>=25")
    assert determine_status(trigger, ["some-id"]) == "blocked"


# ---------------------------------------------------------------------------
# add subcommand via tmp file
# ---------------------------------------------------------------------------

def _run_add(tmp_backlog, extra_args):
    return subprocess.run(
        [sys.executable, str(BACKLOG_PY), "--file", str(tmp_backlog), "add"] + extra_args,
        capture_output=True, text=True,
    )


def test_add_valid_manual_item(tmp_backlog):
    before = load_backlog(tmp_backlog)
    count_before = len(before["items"])

    result = _run_add(tmp_backlog, [
        "--id", "new-manual-item",
        "--title", "New Manual",
        "--area", "validation",
        "--priority", "3",
        "--action", "Do something manually.",
    ])
    assert result.returncode == 0, result.stderr

    after = load_backlog(tmp_backlog)
    assert len(after["items"]) == count_before + 1
    added = next(i for i in after["items"] if i["id"] == "new-manual-item")
    assert added["status"] == "ready"


def test_add_data_trigger_gives_locked(tmp_backlog):
    result = _run_add(tmp_backlog, [
        "--id", "trigger-item",
        "--title", "Triggered",
        "--area", "calibration",
        "--priority", "4",
        "--action", "Run after threshold.",
        "--trigger", "resolved_count>=40",
    ])
    assert result.returncode == 0, result.stderr
    after = load_backlog(tmp_backlog)
    added = next(i for i in after["items"] if i["id"] == "trigger-item")
    assert added["status"] == "locked"


def test_add_depends_on_real_id_gives_blocked(tmp_backlog):
    result = _run_add(tmp_backlog, [
        "--id", "dep-item",
        "--title", "Dependent",
        "--area", "backtesting",
        "--priority", "5",
        "--action", "Runs after backtest-harness.",
        "--depends-on", "backtest-harness",
    ])
    assert result.returncode == 0, result.stderr
    after = load_backlog(tmp_backlog)
    added = next(i for i in after["items"] if i["id"] == "dep-item")
    assert added["status"] == "blocked"


def test_add_duplicate_id_rejected(tmp_backlog):
    before_count = len(load_backlog(tmp_backlog)["items"])
    result = _run_add(tmp_backlog, [
        "--id", "trade-reconciliation",
        "--title", "Dup",
        "--area", "execution",
        "--priority", "1",
        "--action", "Duplicate.",
    ])
    assert result.returncode != 0
    assert len(load_backlog(tmp_backlog)["items"]) == before_count


def test_add_bad_area_rejected(tmp_backlog):
    before_count = len(load_backlog(tmp_backlog)["items"])
    result = _run_add(tmp_backlog, [
        "--id", "bad-area-item",
        "--title", "Bad Area",
        "--area", "nonexistent-area",
        "--priority", "3",
        "--action", "Something.",
    ])
    assert result.returncode != 0
    assert len(load_backlog(tmp_backlog)["items"]) == before_count


def test_add_trigger_metric_not_in_glossary_rejected(tmp_backlog):
    before_count = len(load_backlog(tmp_backlog)["items"])
    result = _run_add(tmp_backlog, [
        "--id", "bad-metric-item",
        "--title", "Bad Metric",
        "--area", "validation",
        "--priority", "3",
        "--action", "Something.",
        "--trigger", "unknown_metric>=10",
    ])
    assert result.returncode != 0
    assert len(load_backlog(tmp_backlog)["items"]) == before_count


def test_add_depends_on_missing_id_rejected(tmp_backlog):
    before_count = len(load_backlog(tmp_backlog)["items"])
    result = _run_add(tmp_backlog, [
        "--id", "missing-dep-item",
        "--title", "Missing Dep",
        "--area", "execution",
        "--priority", "3",
        "--action", "Something.",
        "--depends-on", "nonexistent-id",
    ])
    assert result.returncode != 0
    assert len(load_backlog(tmp_backlog)["items"]) == before_count


# ---------------------------------------------------------------------------
# status subcommand
# ---------------------------------------------------------------------------

def test_status_exits_zero():
    result = subprocess.run(
        [sys.executable, str(BACKLOG_PY), "status"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
