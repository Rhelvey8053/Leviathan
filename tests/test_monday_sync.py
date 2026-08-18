"""
tests/test_monday_sync.py — Tests for scripts/monday_sync.py.

No live monday.com API calls. requests.post is mocked throughout;
backlog.json is read from a synthetic tmp_path copy where a test needs
one, never the real repo file.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts import monday_sync as ms


def _resp(status_code=200, data=None, errors=None):
    body = {}
    if data is not None:
        body["data"] = data
    if errors is not None:
        body["errors"] = errors
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = body
    r.raise_for_status = MagicMock()
    if status_code >= 400 and status_code != 429:
        r.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return r


# ─── gql() ──────────────────────────────────────────────────────────────────

def test_gql_requires_token(monkeypatch):
    monkeypatch.delenv("MONDAY_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="MONDAY_API_TOKEN"):
        ms.gql("query { x }")


def test_gql_raises_on_graphql_errors(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    with patch("scripts.monday_sync.requests.post",
               return_value=_resp(errors=[{"message": "bad"}])):
        with pytest.raises(RuntimeError, match="monday API error"):
            ms.gql("query { x }")


def test_gql_returns_data_on_success(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    with patch("scripts.monday_sync.requests.post",
               return_value=_resp(data={"boards": [{"id": "1"}]})):
        data = ms.gql("query { x }")
    assert data == {"boards": [{"id": "1"}]}


def test_gql_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    responses = [_resp(status_code=429), _resp(data={"ok": True})]
    with patch("scripts.monday_sync.requests.post", side_effect=responses) as mock_post, \
         patch("scripts.monday_sync.time.sleep"):
        data = ms.gql("query { x }")
    assert data == {"ok": True}
    assert mock_post.call_count == 2


def test_gql_gives_up_after_max_retries_of_429(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    with patch("scripts.monday_sync.requests.post", return_value=_resp(status_code=429)) as mock_post, \
         patch("scripts.monday_sync.time.sleep"):
        with pytest.raises(RuntimeError, match="exceeded retries"):
            ms.gql("query { x }", max_retries=2)
    assert mock_post.call_count == 2


# ─── ensure_column() ────────────────────────────────────────────────────────

def _schema(columns=None, groups=None):
    return {
        "name": "Leviathan",
        "groups": groups or [{"id": "g1", "title": "Ready"}],
        "columns": columns or [{"id": "name", "title": "Name", "type": "name"}],
    }


def test_ensure_column_returns_existing_id_without_writing(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    schema = _schema(columns=[{"id": "text_abc", "title": "backlog_id", "type": "text"}])
    with patch("scripts.monday_sync.requests.post") as mock_post:
        col_id, created = ms.ensure_column(123, schema, "backlog_id", "text", dry_run=False)
    assert col_id == "text_abc"
    assert created is False
    mock_post.assert_not_called()


def test_ensure_column_creates_when_absent(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    schema = _schema()
    with patch("scripts.monday_sync.requests.post",
               return_value=_resp(data={"create_column": {"id": "text_new"}})) as mock_post:
        col_id, created = ms.ensure_column(123, schema, "backlog_id", "text", dry_run=False)
    assert col_id == "text_new"
    assert created is True
    mock_post.assert_called_once()


def test_ensure_column_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    schema = _schema()
    with patch("scripts.monday_sync.requests.post") as mock_post:
        col_id, created = ms.ensure_column(123, schema, "backlog_id", "text", dry_run=True)
    assert created is True
    assert col_id.startswith("<would-create")
    mock_post.assert_not_called()


def test_ensure_column_uses_lowercase_column_type(monkeypatch):
    """Regression guard: monday's ColumnType enum values are lowercase
    ("text", "date") -- an earlier version .upper()'d this and got
    BAD_USER_INPUT from the real API on the first live Phase 1 run."""
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    schema = _schema()
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["variables"] = json.get("variables")
        return _resp(data={"create_column": {"id": "text_new"}})

    with patch("scripts.monday_sync.requests.post", side_effect=fake_post):
        ms.ensure_column(123, schema, "backlog_id", "text", dry_run=False)
    assert captured["variables"]["column_type"] == "text"


# ─── verify_groups_and_labels() ────────────────────────────────────────────

def test_verify_groups_and_labels_passes_when_all_present():
    schema = _schema(
        groups=[{"id": "g1", "title": t} for t in ("Ready", "Locked", "Blocked", "Completed", "To-Do")],
        columns=[{
            "id": "project_status", "title": "Status", "type": "status",
            "settings_str": json.dumps({"labels": {"0": "Working on it", "1": "Done",
                                                     "3": "Ready", "4": "Locked", "6": "Blocked"}}),
        }],
    )
    ms.verify_groups_and_labels(schema)  # must not raise


def test_verify_groups_and_labels_raises_on_missing_group():
    schema = _schema(groups=[{"id": "g1", "title": "Ready"}])
    with pytest.raises(RuntimeError, match="missing expected group"):
        ms.verify_groups_and_labels(schema)


def test_verify_groups_and_labels_raises_on_missing_status_label():
    schema = _schema(
        groups=[{"id": "g1", "title": t} for t in ("Ready", "Locked", "Blocked", "Completed")],
        columns=[{
            "id": "project_status", "title": "Status", "type": "status",
            "settings_str": json.dumps({"labels": {"3": "Ready", "4": "Locked"}}),
        }],
    )
    with pytest.raises(RuntimeError, match="missing expected.*label"):
        ms.verify_groups_and_labels(schema)


# ─── phase1_setup() idempotency and matching ───────────────────────────────

def _item(name, backlog_id_text=None, backlog_id_col="text_bid"):
    cvs = [{"id": "name", "text": name, "value": None}]
    if backlog_id_text is not None:
        cvs.append({"id": backlog_id_col, "text": backlog_id_text, "value": None})
    return {"id": f"item-{name}", "name": name, "group": {"id": "g1", "title": "Ready"},
            "column_values": cvs}


@pytest.fixture()
def tmp_backlog(tmp_path, monkeypatch):
    path = tmp_path / "backlog.json"
    data = {"updated": "2026-01-01", "items": [
        {"id": "alpha", "title": "Alpha", "area": "validation", "priority": 1,
         "status": "ready", "trigger": {"all": []}, "depends_on": [], "action": "a", "notes": ""},
        {"id": "beta", "title": "Beta", "area": "validation", "priority": 1,
         "status": "done", "trigger": {"all": []}, "depends_on": [], "action": "b", "notes": ""},
    ]}
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(ms, "BACKLOG_PATH", path)
    return path


def test_phase1_setup_backfills_only_items_missing_backlog_id(tmp_backlog, monkeypatch, tmp_path):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ms, "CONFIG_PATH", cfg_path)

    schema = _schema(
        groups=[{"id": "g1", "title": t} for t in ("Ready", "Locked", "Blocked", "Completed")],
        columns=[
            {"id": "text_bid", "title": "backlog_id", "type": "text"},
            {"id": "date_cd", "title": "Completed date", "type": "date"},
            {"id": "project_status", "title": "Status", "type": "status",
             "settings_str": json.dumps({"labels": {"3": "Ready", "4": "Locked",
                                                      "6": "Blocked", "1": "Done"}})},
        ],
    )
    items = [
        _item("alpha", backlog_id_text=None),   # needs backfill
        _item("beta", backlog_id_text="beta"),  # already correct, should be skipped
    ]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=items), \
         patch("scripts.monday_sync.gql") as mock_gql, \
         patch("scripts.monday_sync.time.sleep"):
        summary = ms.phase1_setup(board_id=999, dry_run=False)

    assert summary["items_backfilled"] == 1
    assert summary["items_already_set"] == 1
    assert summary["items_unmatched"] == []
    # exactly one write mutation (the backfill for 'alpha') -- 'beta' must
    # not trigger a redundant write.
    assert mock_gql.call_count == 1
    written = mock_gql.call_args[0][1]
    assert written["value"] == "alpha"


def test_phase1_setup_flags_unmatched_board_items(tmp_backlog, monkeypatch, tmp_path):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ms, "CONFIG_PATH", cfg_path)

    schema = _schema(
        groups=[{"id": "g1", "title": t} for t in ("Ready", "Locked", "Blocked", "Completed")],
        columns=[
            {"id": "text_bid", "title": "backlog_id", "type": "text"},
            {"id": "date_cd", "title": "Completed date", "type": "date"},
            {"id": "project_status", "title": "Status", "type": "status",
             "settings_str": json.dumps({"labels": {"3": "Ready", "4": "Locked",
                                                      "6": "Blocked", "1": "Done"}})},
        ],
    )
    items = [_item("alpha", backlog_id_text="alpha"), _item("orphan-not-in-json")]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=items), \
         patch("scripts.monday_sync.gql") as mock_gql, \
         patch("scripts.monday_sync.time.sleep"):
        summary = ms.phase1_setup(board_id=999, dry_run=False)

    assert summary["items_unmatched"] == ["orphan-not-in-json"]
    mock_gql.assert_not_called()  # nothing to backfill, and 'beta' isn't even on the board here


def test_phase1_setup_dry_run_never_calls_gql_for_writes(tmp_backlog, monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    schema = _schema(
        groups=[{"id": "g1", "title": t} for t in ("Ready", "Locked", "Blocked", "Completed")],
        columns=[
            {"id": "project_status", "title": "Status", "type": "status",
             "settings_str": json.dumps({"labels": {"3": "Ready", "4": "Locked",
                                                      "6": "Blocked", "1": "Done"}})},
        ],
    )
    items = [_item("alpha"), _item("beta")]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=items), \
         patch("scripts.monday_sync.gql") as mock_gql:
        summary = ms.phase1_setup(board_id=999, dry_run=True)

    assert summary["items_backfilled"] == 2
    mock_gql.assert_not_called()
