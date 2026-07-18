"""
tests/test_gate_notifier.py — Tests for scripts/gate_notifier.py.

No live email, no live SMTP, no live DB. core.report.send_report and
core.logger's stats functions are mocked/monkeypatched throughout;
BACKLOG.md and gate_state.json are synthetic tmp_path files.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts import gate_notifier as gn


SAMPLE_BACKLOG = """# Leviathan Backlog
Last updated: 2026-01-01 | Metrics: resolved=11, fills=7

## Ready (0)
| Priority | ID | Action | Area |
|----------|-----|--------|------|

## Locked (4)
| Priority | ID | Gate | Area |
|----------|-----|------|------|
| 4 | test-resolved-gate | resolved_count >= 25 | calibration |
| 4 | test-fills-gate | fills_count >= 20 | execution |
| 4 | test-category-gate | resolved_count_per_category_max >= 15 | reporting |
| 4 | test-wallet-gate | resolved_count_per_wallet_max >= 10 | smart-money |

## Blocked (1)
| Priority | ID | Waiting On | Area |
|----------|-----|-----------|------|
| 5 | test-dependent | test-resolved-gate | reporting |

## Done (0)
| Priority | ID | Action | Area |
|----------|-----|--------|------|
"""

MALFORMED_BACKLOG = """# Leviathan Backlog
Last updated: 2026-01-01 | Metrics: resolved=11, fills=7

## Locked (1)
| Priority | ID | Gate | Area |
|----------|-----|------|------|
| 4 | test-bad-gate | some nonsense text | calibration |
"""


@pytest.fixture
def backlog_file(tmp_path):
    p = tmp_path / "BACKLOG.md"
    p.write_text(SAMPLE_BACKLOG, encoding="utf-8")
    return p


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "gate_state.json"


def _cfg():
    return {"report": {"email_to": "test@example.com"}, "llm": {}}


# ── gate cell / table parsing ─────────────────────────────────────────────────

def test_parse_gate_cell_valid():
    assert gn.parse_gate_cell("resolved_count >= 25") == ("resolved_count", ">=", 25.0)


def test_parse_gate_cell_variants():
    assert gn.parse_gate_cell("fills_count > 5") == ("fills_count", ">", 5.0)
    assert gn.parse_gate_cell("resolved_count == 0") == ("resolved_count", "==", 0.0)


def test_parse_gate_cell_invalid_returns_none():
    assert gn.parse_gate_cell("some nonsense text") is None


def test_parse_locked_table_mixed_gates():
    rows, failures = gn.parse_locked_table(SAMPLE_BACKLOG)
    assert failures == []
    ids = {r["id"] for r in rows}
    assert ids == {"test-resolved-gate", "test-fills-gate",
                    "test-category-gate", "test-wallet-gate"}
    resolved_row = next(r for r in rows if r["id"] == "test-resolved-gate")
    assert gn.parse_gate_cell(resolved_row["gate_cell"]) == ("resolved_count", ">=", 25.0)


def test_parse_blocked_table():
    rows = gn.parse_blocked_table(SAMPLE_BACKLOG)
    assert len(rows) == 1
    assert rows[0]["id"] == "test-dependent"
    assert rows[0]["waiting_on"] == "test-resolved-gate"


# ── malformed rows fail loud ───────────────────────────────────────────────────

def test_malformed_row_produces_parse_failure_not_silent_drop():
    rows, failures = gn.parse_locked_table(MALFORMED_BACKLOG)
    assert rows == []
    assert len(failures) == 1
    assert "test-bad-gate" in failures[0]


def test_run_raises_gateparseerror_on_malformed_row(tmp_path, state_file):
    bad_backlog = tmp_path / "BACKLOG.md"
    bad_backlog.write_text(MALFORMED_BACKLOG, encoding="utf-8")
    with pytest.raises(gn.GateParseError) as exc_info:
        gn.run(_cfg(), backlog_path=bad_backlog, state_path=state_file, dry_run=True)
    assert "test-bad-gate" in str(exc_info.value)


# ── metric mapping ─────────────────────────────────────────────────────────────

def test_resolved_count_gate_unlocked_above_threshold():
    row = {"id": "x", "area": "a", "gate_cell": "resolved_count >= 25"}
    with patch.object(gn.logger, "get_stats", return_value={"resolved": 30}):
        result = gn.evaluate_gate(row, _cfg())
    assert result["status"] == "unlocked"
    assert result["value"] == 30


def test_resolved_count_gate_locked_below_threshold():
    row = {"id": "x", "area": "a", "gate_cell": "resolved_count >= 50"}
    with patch.object(gn.logger, "get_stats", return_value={"resolved": 30}):
        result = gn.evaluate_gate(row, _cfg())
    assert result["status"] == "locked"


def test_fills_count_metric_mapping():
    row = {"id": "x", "area": "a", "gate_cell": "fills_count >= 20"}
    with patch.object(gn.logger, "get_stats_real", return_value={"total_fills": 25}):
        result = gn.evaluate_gate(row, _cfg())
    assert result["status"] == "unlocked"
    assert result["value"] == 25


def test_category_max_metric_mapping():
    row = {"id": "x", "area": "a", "gate_cell": "resolved_count_per_category_max >= 15"}
    with patch.object(gn.logger, "get_stats_by_flag_path",
                       return_value=[{"total": 8}, {"total": 20}, {"total": 3}]):
        result = gn.evaluate_gate(row, _cfg())
    assert result["value"] == 20
    assert result["status"] == "unlocked"


def test_category_max_metric_zero_when_no_data():
    row = {"id": "x", "area": "a", "gate_cell": "resolved_count_per_category_max >= 15"}
    with patch.object(gn.logger, "get_stats_by_flag_path", return_value=[]):
        result = gn.evaluate_gate(row, _cfg())
    assert result["value"] == 0
    assert result["status"] == "locked"


# ── UNKNOWN never fires ─────────────────────────────────────────────────────

def test_unknown_metric_classified_and_never_unlocks(backlog_file, state_file):
    with patch.object(gn.logger, "get_stats", return_value={"resolved": 999}), \
         patch.object(gn.logger, "get_stats_real", return_value={"total_fills": 999}), \
         patch.object(gn.logger, "get_stats_by_flag_path", return_value=[{"total": 999}]):
        result = gn.run(_cfg(), backlog_path=backlog_file, state_path=state_file, dry_run=True)

    wallet_result = result["current"]["test-wallet-gate"]
    assert wallet_result["status"] == "unknown"
    assert wallet_result["value"] is None

    unlocked_ids = {g["id"] for g in result["newly_unlocked"]}
    assert "test-wallet-gate" not in unlocked_ids
    # Every other gate DID unlock in this scenario, proving the unknown one
    # is excluded specifically, not because nothing unlocked at all.
    assert "test-resolved-gate" in unlocked_ids
    assert "test-fills-gate" in unlocked_ids
    assert "test-category-gate" in unlocked_ids


# ── fire-once ────────────────────────────────────────────────────────────────

def test_fire_once_does_not_renotify(backlog_file, state_file):
    with patch.object(gn.logger, "get_stats", return_value={"resolved": 30}), \
         patch.object(gn.logger, "get_stats_real", return_value={"total_fills": 2}), \
         patch.object(gn.logger, "get_stats_by_flag_path", return_value=[{"total": 3}]), \
         patch.object(gn, "send_report") as mock_send:
        result1 = gn.run(_cfg(), backlog_path=backlog_file, state_path=state_file, dry_run=False)

        assert result1["sent"] is True
        assert "test-resolved-gate" in {g["id"] for g in result1["newly_unlocked"]}
        mock_send.assert_called_once()
        sent_body = mock_send.call_args.args[0] if mock_send.call_args.args else mock_send.call_args.kwargs["body"]
        assert "test-resolved-gate" in sent_body

        result2 = gn.run(_cfg(), backlog_path=backlog_file, state_path=state_file, dry_run=False)

        assert result2["newly_unlocked"] == []
        assert result2["sent"] is False
        mock_send.assert_called_once()  # still just the one call from run 1


# ── unknown -> unlocked ────────────────────────────────────────────────────

def test_unknown_to_unlocked_notifies(backlog_file, state_file, monkeypatch):
    """
    Simulates a metric gaining a registry mapping between two runs (the
    realistic way a gate ever leaves "unknown" — a future code change adds
    the metric, not a data change). unknown -> unlocked must notify;
    unknown -> unknown (unchanged) must not.
    """
    full_registry = dict(gn.KNOWN_METRICS)
    registry_without_category = {
        k: v for k, v in full_registry.items() if k != "resolved_count_per_category_max"
    }

    # Run 1: registry does not yet know resolved_count_per_category_max.
    monkeypatch.setattr(gn, "KNOWN_METRICS", registry_without_category)
    with patch.object(gn.logger, "get_stats", return_value={"resolved": 5}), \
         patch.object(gn.logger, "get_stats_real", return_value={"total_fills": 2}), \
         patch.object(gn, "send_report") as mock_send1:
        result1 = gn.run(_cfg(), backlog_path=backlog_file, state_path=state_file, dry_run=False)

    assert result1["current"]["test-category-gate"]["status"] == "unknown"
    mock_send1.assert_not_called()  # resolved/fills still locked too; nothing to notify

    # Run 2: registry now knows the metric (simulating a future code update), and it passes.
    monkeypatch.setattr(gn, "KNOWN_METRICS", full_registry)
    with patch.object(gn.logger, "get_stats", return_value={"resolved": 5}), \
         patch.object(gn.logger, "get_stats_real", return_value={"total_fills": 2}), \
         patch.object(gn.logger, "get_stats_by_flag_path", return_value=[{"total": 20}]), \
         patch.object(gn, "send_report") as mock_send2:
        result2 = gn.run(_cfg(), backlog_path=backlog_file, state_path=state_file, dry_run=False)

    unlocked_ids = {g["id"] for g in result2["newly_unlocked"]}
    assert "test-category-gate" in unlocked_ids
    mock_send2.assert_called_once()


# ── send-failure safety ───────────────────────────────────────────────────────

def test_send_failure_does_not_persist_unlocked_status(backlog_file, state_file):
    # Simulate a prior successful run where everything was locked/unknown.
    initial_state = {
        "updated_at": "2026-01-01T00:00:00+00:00",
        "gates": {
            "test-resolved-gate": {"id": "test-resolved-gate", "area": "calibration",
                                    "metric_name": "resolved_count", "op": ">=", "threshold": 25.0,
                                    "status": "locked", "value": 5},
            "test-fills-gate": {"id": "test-fills-gate", "area": "execution",
                                 "metric_name": "fills_count", "op": ">=", "threshold": 20.0,
                                 "status": "locked", "value": 2},
            "test-category-gate": {"id": "test-category-gate", "area": "reporting",
                                    "metric_name": "resolved_count_per_category_max", "op": ">=",
                                    "threshold": 15.0, "status": "locked", "value": 3},
            "test-wallet-gate": {"id": "test-wallet-gate", "area": "smart-money",
                                  "metric_name": "resolved_count_per_wallet_max", "op": ">=",
                                  "threshold": 10.0, "status": "unknown", "value": None},
        },
    }
    state_file.write_text(json.dumps(initial_state), encoding="utf-8")

    with patch.object(gn.logger, "get_stats", return_value={"resolved": 30}), \
         patch.object(gn.logger, "get_stats_real", return_value={"total_fills": 2}), \
         patch.object(gn.logger, "get_stats_by_flag_path", return_value=[{"total": 3}]), \
         patch.object(gn, "send_report", side_effect=RuntimeError("SMTP down")):
        result = gn.run(_cfg(), backlog_path=backlog_file, state_path=state_file, dry_run=False)

    assert result["error"] is not None
    assert result["sent"] is False

    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert persisted["gates"]["test-resolved-gate"]["status"] == "locked"
    assert persisted["gates"]["test-wallet-gate"]["status"] == "unknown"

    # Next run (no send failure this time) should retry and succeed.
    with patch.object(gn.logger, "get_stats", return_value={"resolved": 30}), \
         patch.object(gn.logger, "get_stats_real", return_value={"total_fills": 2}), \
         patch.object(gn.logger, "get_stats_by_flag_path", return_value=[{"total": 3}]), \
         patch.object(gn, "send_report") as mock_send:
        result2 = gn.run(_cfg(), backlog_path=backlog_file, state_path=state_file, dry_run=False)

    assert "test-resolved-gate" in {g["id"] for g in result2["newly_unlocked"]}
    mock_send.assert_called_once()


# ── dry-run ──────────────────────────────────────────────────────────────────

def test_dry_run_prints_and_does_not_persist(backlog_file, state_file, capsys):
    with patch.object(gn.logger, "get_stats", return_value={"resolved": 30}), \
         patch.object(gn.logger, "get_stats_real", return_value={"total_fills": 2}), \
         patch.object(gn.logger, "get_stats_by_flag_path", return_value=[{"total": 3}]):
        result = gn.run(_cfg(), backlog_path=backlog_file, state_path=state_file, dry_run=True)

    captured = capsys.readouterr()
    assert "test-resolved-gate" in captured.out
    assert result["sent"] is False
    assert not state_file.exists()


def test_dry_run_is_repeatable(backlog_file, state_file):
    """Running --dry-run twice must produce the same newly-unlocked result both times."""
    with patch.object(gn.logger, "get_stats", return_value={"resolved": 30}), \
         patch.object(gn.logger, "get_stats_real", return_value={"total_fills": 2}), \
         patch.object(gn.logger, "get_stats_by_flag_path", return_value=[{"total": 3}]):
        result1 = gn.run(_cfg(), backlog_path=backlog_file, state_path=state_file, dry_run=True)
        result2 = gn.run(_cfg(), backlog_path=backlog_file, state_path=state_file, dry_run=True)

    ids1 = {g["id"] for g in result1["newly_unlocked"]}
    ids2 = {g["id"] for g in result2["newly_unlocked"]}
    # fills_count=2 (<20) and category-max=3 (<15) both stay locked; only resolved_count unlocks.
    assert ids1 == ids2 == {"test-resolved-gate"}


def test_no_newly_unlocked_sends_nothing_and_exits_quietly(backlog_file, state_file):
    with patch.object(gn.logger, "get_stats", return_value={"resolved": 1}), \
         patch.object(gn.logger, "get_stats_real", return_value={"total_fills": 1}), \
         patch.object(gn.logger, "get_stats_by_flag_path", return_value=[{"total": 1}]), \
         patch.object(gn, "send_report") as mock_send:
        result = gn.run(_cfg(), backlog_path=backlog_file, state_path=state_file, dry_run=False)

    assert result["newly_unlocked"] == []
    assert result["sent"] is False
    mock_send.assert_not_called()
