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


# ─── Phase 2: render_detail / expected_fields / _diff ──────────────────────

def _bl_item(id="alpha", status="done", priority=1, area="calibration",
             action="Did the thing.", trigger=None, depends_on=None):
    return {
        "id": id, "title": id.title(), "area": area, "priority": priority,
        "status": status, "trigger": trigger or {"all": []},
        "depends_on": depends_on or [], "action": action, "notes": "",
    }


def test_render_detail_plain_for_done_item():
    item = _bl_item(status="done", action="Fixed the bug.")
    assert ms.render_detail(item, {}) == "Fixed the bug."


def test_render_detail_appends_gate_for_locked_item():
    item = _bl_item(status="locked", action="Do the thing.",
                     trigger={"all": [{"metric": "resolved_count", "op": ">=", "value": 25}]})
    detail = ms.render_detail(item, {"resolved_count": 10})
    assert detail.startswith("Do the thing.")
    assert "Gate: resolved_count=10 >= 25 (not met)" in detail


def test_render_detail_gate_shows_met_when_trigger_satisfied():
    item = _bl_item(status="locked", action="Do the thing.",
                     trigger={"all": [{"metric": "resolved_count", "op": ">=", "value": 25}]})
    detail = ms.render_detail(item, {"resolved_count": 30})
    assert "Gate: resolved_count=30 >= 25 (MET)" in detail


def test_render_detail_flags_sentinel_metric_as_human_decision():
    """A metric compute_metrics never computes (a policy/human gate, e.g.
    api_spend_authorized) must read as requiring a decision, not as a
    live 0 that could clear on its own -- this is the exact signal that
    was missing from the board and let Liam's PM-agent reports recommend
    unblocking items still gated behind one (see docs/PROGRESS.md)."""
    item = _bl_item(status="blocked", action="Do the thing.", depends_on=["dep-a"],
                     trigger={"all": [{"metric": "api_spend_authorized", "op": ">=", "value": 1}]})
    detail = ms.render_detail(item, {})
    assert "requires human decision, never auto-computed" in detail


def test_render_detail_appends_waiting_on_for_blocked_item():
    item = _bl_item(status="blocked", action="Do the thing.", depends_on=["other-item"])
    detail = ms.render_detail(item, {})
    assert "Waiting on: other-item" in detail


def test_render_detail_truncates_long_action():
    item = _bl_item(status="done", action="x" * 3000)
    detail = ms.render_detail(item, {})
    assert len(detail) <= ms.DETAIL_MAX_CHARS
    assert "truncated" in detail


def test_render_detail_no_truncation_under_cap():
    item = _bl_item(status="done", action="short text")
    assert "truncated" not in ms.render_detail(item, {})


def test_expected_fields_maps_status_to_group_and_label():
    assert ms.expected_fields(_bl_item(status="ready"), {})["group_title"] == "Ready"
    assert ms.expected_fields(_bl_item(status="ready"), {})["status_label"] == "Ready"
    assert ms.expected_fields(_bl_item(status="locked"), {})["group_title"] == "Locked"
    assert ms.expected_fields(_bl_item(status="blocked"), {})["group_title"] == "Blocked"
    assert ms.expected_fields(_bl_item(status="done"), {})["group_title"] == "Completed"
    assert ms.expected_fields(_bl_item(status="done"), {})["status_label"] == "Done"


COL_IDS = {"status": "c_status", "priority": "c_pri", "area": "c_area",
           "detail": "c_detail", "backlog_id": "c_bid", "completed_date": "c_cd"}


def _board_item(name="alpha", group_title="Completed", status_label="Done",
                 priority="1", area="calibration", detail="Fixed the bug."):
    return {
        "id": "item-1", "name": name, "group": {"id": "g1", "title": group_title},
        "column_values": [
            {"id": COL_IDS["status"], "text": status_label, "value": None},
            {"id": COL_IDS["priority"], "text": priority, "value": None},
            {"id": COL_IDS["area"], "text": area, "value": None},
            {"id": COL_IDS["detail"], "text": detail, "value": None},
            {"id": COL_IDS["backlog_id"], "text": name, "value": None},
        ],
    }


def test_diff_empty_when_board_already_matches():
    item = _bl_item(status="done", priority=1, area="calibration", action="Fixed the bug.")
    board_item = _board_item()
    assert ms._diff(item, board_item, COL_IDS, {}) == {}


def test_diff_detects_status_and_group_change():
    item = _bl_item(id="beta", status="ready", priority=1, area="validation", action="x")
    board_item = _board_item(name="beta", group_title="Blocked", status_label="Blocked",
                              priority="1", area="validation", detail="x")
    changes = ms._diff(item, board_item, COL_IDS, {})
    assert "status" in changes
    assert "group" in changes
    assert "priority" not in changes
    assert "area" not in changes


def test_diff_detects_priority_change():
    item = _bl_item(priority=3)
    board_item = _board_item(priority="1")
    changes = ms._diff(item, board_item, COL_IDS, {})
    assert changes["priority"] == ("1", 3)


def test_diff_detects_area_change():
    item = _bl_item(area="infra")
    board_item = _board_item(area="infrastructure")
    changes = ms._diff(item, board_item, COL_IDS, {})
    assert changes["area"] == ("infrastructure", "infra")


def test_diff_returns_create_marker_when_no_board_item():
    item = _bl_item()
    changes = ms._diff(item, None, COL_IDS, {})
    assert "_create" in changes


# ─── Phase 2: phase2_sync() integration (fully mocked) ─────────────────────

def _phase2_schema():
    return {
        "name": "Leviathan",
        "groups": [{"id": f"g_{t.lower()}", "title": t}
                   for t in ("Ready", "Locked", "Blocked", "Completed", "To-Do")],
        "columns": [
            {"id": COL_IDS["status"], "title": "Status", "type": "status"},
            {"id": COL_IDS["priority"], "title": "Priority", "type": "numbers"},
            {"id": COL_IDS["area"], "title": "Area", "type": "text"},
            {"id": COL_IDS["detail"], "title": "Detail", "type": "long_text"},
            {"id": COL_IDS["backlog_id"], "title": "backlog_id", "type": "text"},
            {"id": COL_IDS["completed_date"], "title": "Completed date", "type": "date"},
        ],
    }


@pytest.fixture()
def tmp_backlog2(tmp_path, monkeypatch):
    path = tmp_path / "backlog.json"
    data = {"updated": "2026-01-01", "items": [
        _bl_item(id="alpha", status="done", priority=1, area="calibration", action="Fixed the bug."),
        _bl_item(id="beta", status="ready", priority=2, area="validation", action="Do the thing."),
        _bl_item(id="gamma", status="locked", priority=1, area="calibration", action="Wait for gate.",
                 trigger={"all": [{"metric": "resolved_count", "op": ">=", "value": 25}]}),
    ]}
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(ms, "BACKLOG_PATH", path)
    # Deterministic live metrics -- gamma's trigger (resolved_count >= 25)
    # evaluates to "not met" everywhere this fixture is used, rather than
    # hitting the real DB via the unmocked default.
    monkeypatch.setattr(ms, "compute_metrics", lambda: {"resolved_count": 10})
    return path


def test_phase2_sync_no_writes_when_board_already_matches(tmp_backlog2, monkeypatch, tmp_path):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    log_path = tmp_path / "monday_sync.log"
    monkeypatch.setattr(ms, "LOG_PATH", log_path)

    schema = _phase2_schema()
    board_items = [
        _board_item(name="alpha", group_title="Completed", status_label="Done",
                    priority="1", area="calibration", detail="Fixed the bug."),
        _board_item(name="beta", group_title="Ready", status_label="Ready",
                    priority="2", area="validation", detail="Do the thing."),
        _board_item(name="gamma", group_title="Locked", status_label="Locked",
                    priority="1", area="calibration",
                    detail="Wait for gate.\n\nGate: resolved_count=10 >= 25 (not met)"),
    ]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items), \
         patch("scripts.monday_sync.gql") as mock_gql:
        summary = ms.phase2_sync(board_id=999, dry_run=False)

    assert summary == {"created": [], "updated": [], "moved": [],
                        "completed_stamped": [], "unmatched_board_items": []}
    mock_gql.assert_not_called()
    assert log_path.exists()


def test_phase2_sync_creates_missing_item(tmp_backlog2, monkeypatch, tmp_path):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    monkeypatch.setattr(ms, "LOG_PATH", tmp_path / "monday_sync.log")

    schema = _phase2_schema()
    board_items = [
        _board_item(name="alpha", group_title="Completed", status_label="Done",
                    priority="1", area="calibration", detail="Fixed the bug."),
        _board_item(name="beta", group_title="Ready", status_label="Ready",
                    priority="2", area="validation", detail="Do the thing."),
        # 'gamma' is missing entirely -- must be created
    ]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items), \
         patch("scripts.monday_sync.gql", return_value={"create_item": {"id": "new-1"}}) as mock_gql, \
         patch("scripts.monday_sync.time.sleep"):
        summary = ms.phase2_sync(board_id=999, dry_run=False)

    assert summary["created"] == ["gamma"]
    mock_gql.assert_called_once()


def test_phase2_sync_updates_only_changed_field(tmp_backlog2, monkeypatch, tmp_path):
    """beta's priority differs (board says 1, json says 2) -- must write
    only priority, not touch status/group/area/detail which already match."""
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    monkeypatch.setattr(ms, "LOG_PATH", tmp_path / "monday_sync.log")

    schema = _phase2_schema()
    board_items = [
        _board_item(name="alpha", group_title="Completed", status_label="Done",
                    priority="1", area="calibration", detail="Fixed the bug."),
        _board_item(name="beta", group_title="Ready", status_label="Ready",
                    priority="1", area="validation", detail="Do the thing."),  # priority wrong
        _board_item(name="gamma", group_title="Locked", status_label="Locked",
                    priority="1", area="calibration",
                    detail="Wait for gate.\n\nGate: resolved_count=10 >= 25 (not met)"),
    ]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items), \
         patch("scripts.monday_sync.gql") as mock_gql, \
         patch("scripts.monday_sync.time.sleep"):
        summary = ms.phase2_sync(board_id=999, dry_run=False)

    assert summary["updated"] == ["beta"]
    assert summary["moved"] == []  # group already correct, must not call move_item_to_group
    mock_gql.assert_called_once()
    written = json.loads(mock_gql.call_args[0][1]["column_values"])
    assert written == {COL_IDS["priority"]: "2"}


def test_phase2_sync_stamps_completed_date_only_on_fresh_transition(tmp_backlog2, monkeypatch, tmp_path):
    """alpha is done in json; board currently shows it NOT done (still
    Ready) -- this is a genuine fresh transition, so Completed date must
    be stamped. Must not fire for items already Done on the board."""
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    monkeypatch.setattr(ms, "LOG_PATH", tmp_path / "monday_sync.log")

    schema = _phase2_schema()
    board_items = [
        _board_item(name="alpha", group_title="Ready", status_label="Ready",
                    priority="1", area="calibration", detail="Fixed the bug."),
        _board_item(name="beta", group_title="Ready", status_label="Ready",
                    priority="2", area="validation", detail="Do the thing."),
        _board_item(name="gamma", group_title="Locked", status_label="Locked",
                    priority="1", area="calibration",
                    detail="Wait for gate.\n\nGate: resolved_count=10 >= 25 (not met)"),
    ]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items), \
         patch("scripts.monday_sync.gql") as mock_gql, \
         patch("scripts.monday_sync.time.sleep"):
        summary = ms.phase2_sync(board_id=999, dry_run=False)

    assert summary["completed_stamped"] == ["alpha"]
    # alpha's content-mutation call (the first of its two calls -- content
    # then group move) must include the completed_date column.
    written_values = json.loads(mock_gql.call_args_list[0][0][1]["column_values"])
    assert COL_IDS["completed_date"] in written_values


def test_phase2_sync_never_stamps_completed_date_for_already_done_item(tmp_backlog2, monkeypatch, tmp_path):
    """A no-op item (already Done on the board) must never get a
    completed_date write -- confirms no backdating on ordinary matches."""
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    monkeypatch.setattr(ms, "LOG_PATH", tmp_path / "monday_sync.log")

    schema = _phase2_schema()
    board_items = [
        _board_item(name="alpha", group_title="Completed", status_label="Done",
                    priority="1", area="calibration", detail="Fixed the bug."),
        _board_item(name="beta", group_title="Ready", status_label="Ready",
                    priority="2", area="validation", detail="Do the thing."),
        _board_item(name="gamma", group_title="Locked", status_label="Locked",
                    priority="1", area="calibration",
                    detail="Wait for gate.\n\nGate: resolved_count=10 >= 25 (not met)"),
    ]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items), \
         patch("scripts.monday_sync.gql") as mock_gql:
        summary = ms.phase2_sync(board_id=999, dry_run=False)

    assert summary["completed_stamped"] == []


def test_phase2_sync_dry_run_writes_nothing(tmp_backlog2, monkeypatch, tmp_path):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    monkeypatch.setattr(ms, "LOG_PATH", tmp_path / "monday_sync.log")

    schema = _phase2_schema()
    board_items = []  # nothing matches -- everything would be created

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items), \
         patch("scripts.monday_sync.gql") as mock_gql:
        summary = ms.phase2_sync(board_id=999, dry_run=True)

    assert summary["created"] == ["alpha", "beta", "gamma"]
    mock_gql.assert_not_called()


def test_phase2_sync_leaves_unmanaged_board_items_untouched(tmp_backlog2, monkeypatch, tmp_path):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    monkeypatch.setattr(ms, "LOG_PATH", tmp_path / "monday_sync.log")

    schema = _phase2_schema()
    board_items = [
        _board_item(name="alpha", group_title="Completed", status_label="Done",
                    priority="1", area="calibration", detail="Fixed the bug."),
        _board_item(name="beta", group_title="Ready", status_label="Ready",
                    priority="2", area="validation", detail="Do the thing."),
        _board_item(name="gamma", group_title="Locked", status_label="Locked",
                    priority="1", area="calibration",
                    detail="Wait for gate.\n\nGate: resolved_count=10 >= 25 (not met)"),
        {  # unmanaged: no backlog_id column value at all
            "id": "item-pm", "name": "Set up PM", "group": {"id": "g_todo", "title": "To-Do"},
            "column_values": [{"id": COL_IDS["status"], "text": None, "value": None}],
        },
    ]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items), \
         patch("scripts.monday_sync.gql") as mock_gql:
        summary = ms.phase2_sync(board_id=999, dry_run=False)

    assert summary["unmatched_board_items"] == ["Set up PM"]
    mock_gql.assert_not_called()


# ─── verify_phase2() ────────────────────────────────────────────────────────

def test_verify_phase2_ok_when_everything_matches(tmp_backlog2, monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    schema = _phase2_schema()
    board_items = [
        _board_item(name="alpha", group_title="Completed", status_label="Done",
                    priority="1", area="calibration", detail="Fixed the bug."),
        _board_item(name="beta", group_title="Ready", status_label="Ready",
                    priority="2", area="validation", detail="Do the thing."),
        _board_item(name="gamma", group_title="Locked", status_label="Locked",
                    priority="1", area="calibration",
                    detail="Wait for gate.\n\nGate: resolved_count=10 >= 25 (not met)"),
    ]
    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items):
        result = ms.verify_phase2(board_id=999)
    assert result == {"ok": True, "mismatches": []}


def test_verify_phase2_flags_mismatch(tmp_backlog2, monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    schema = _phase2_schema()
    board_items = [
        _board_item(name="alpha", group_title="Ready", status_label="Ready",  # wrong!
                    priority="1", area="calibration", detail="Fixed the bug."),
        _board_item(name="beta", group_title="Ready", status_label="Ready",
                    priority="2", area="validation", detail="Do the thing."),
        _board_item(name="gamma", group_title="Locked", status_label="Locked",
                    priority="1", area="calibration",
                    detail="Wait for gate.\n\nGate: resolved_count=10 >= 25 (not met)"),
    ]
    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items):
        result = ms.verify_phase2(board_id=999)
    assert result["ok"] is False
    assert result["mismatches"][0]["backlog_id"] == "alpha"


# ─── Phase 3: _transition_message / _format_summary_line ──────────────────

def test_transition_message_for_created_item():
    item = _bl_item(status="ready")
    msg = ms._transition_message(item, {}, newly_done=False, created=True)
    assert msg.startswith("Created (Ready)")


def test_transition_message_for_status_change_includes_gate_when_freshly_ready():
    item = _bl_item(status="ready",
                     trigger={"all": [{"metric": "resolved_count", "op": ">=", "value": 25}]})
    changes = {"status": ("Locked", "Ready")}
    msg = ms._transition_message(item, changes, newly_done=False, created=False)
    assert "Locked -> Ready" in msg
    assert "Gate cleared: resolved_count >= 25" in msg


def test_transition_message_for_status_change_no_gate_text_when_no_trigger():
    item = _bl_item(status="ready", trigger={"all": []})
    changes = {"status": ("Blocked", "Ready")}
    msg = ms._transition_message(item, changes, newly_done=False, created=False)
    assert "Blocked -> Ready" in msg
    assert "Gate cleared" not in msg


def test_transition_message_for_group_only_move():
    item = _bl_item(status="ready")
    changes = {"group": ("Blocked", "Ready")}
    msg = ms._transition_message(item, changes, newly_done=False, created=False)
    assert "Moved Blocked -> Ready" in msg


def test_transition_message_includes_completion_note():
    item = _bl_item(status="done")
    changes = {"status": ("Ready", "Done"), "group": ("Ready", "Completed")}
    msg = ms._transition_message(item, changes, newly_done=True, created=False)
    assert "Marked Done" in msg
    assert "Completed date stamped" in msg


def test_transition_message_none_for_content_only_change():
    """A pure detail/priority/area diff (no status/group/newly_done) isn't
    a state change -- must not synthesize a message for it."""
    item = _bl_item(status="ready")
    msg = ms._transition_message(item, {"priority": ("1", 2)}, newly_done=False, created=False)
    assert msg is None


def test_format_summary_line_no_changes():
    summary = {"created": [], "updated": [], "moved": [], "completed_stamped": [],
               "unmatched_board_items": []}
    line = ms._format_summary_line(summary, [], [])
    assert "no changes" in line


def test_format_summary_line_with_real_changes():
    summary = {"created": ["a"], "updated": ["b", "c"], "moved": ["b"],
               "completed_stamped": [], "unmatched_board_items": []}
    line = ms._format_summary_line(summary, transitioned=["a", "b"], content_only=["c"])
    assert "created 1" in line
    assert "updated 2" in line
    assert "2 transitions" in line
    assert "1 content-only" in line


# ─── Phase 3: ensure_log_item() ────────────────────────────────────────────

def test_ensure_log_item_reuses_existing_by_name(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    schema = _phase2_schema()
    board_items = [{"id": "log-1", "name": ms.LOG_ITEM_TITLE,
                    "group": {"id": "g_to-do", "title": "To-Do"}, "column_values": []}]
    with patch("scripts.monday_sync.gql") as mock_gql:
        log_id = ms.ensure_log_item(999, schema, board_items, dry_run=False)
    assert log_id == "log-1"
    mock_gql.assert_not_called()


def test_ensure_log_item_creates_when_absent(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    schema = _phase2_schema()
    with patch("scripts.monday_sync.gql",
               return_value={"create_item": {"id": "log-new"}}) as mock_gql, \
         patch("scripts.monday_sync.time.sleep"):
        log_id = ms.ensure_log_item(999, schema, [], dry_run=False)
    assert log_id == "log-new"
    mock_gql.assert_called_once()


def test_ensure_log_item_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    schema = _phase2_schema()
    with patch("scripts.monday_sync.gql") as mock_gql:
        log_id = ms.ensure_log_item(999, schema, [], dry_run=True)
    assert log_id.startswith("<would-create")
    mock_gql.assert_not_called()


# ─── Phase 3: phase2_sync(post_progress=True) integration ─────────────────

def test_phase3_posts_no_updates_on_no_op_run_but_posts_summary(tmp_backlog2, monkeypatch, tmp_path):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ms, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(ms, "LOG_PATH", tmp_path / "monday_sync.log")

    schema = _phase2_schema()
    log_item = {"id": "log-1", "name": ms.LOG_ITEM_TITLE,
                "group": {"id": "g_to-do", "title": "To-Do"}, "column_values": []}
    board_items = [
        log_item,
        _board_item(name="alpha", group_title="Completed", status_label="Done",
                    priority="1", area="calibration", detail="Fixed the bug."),
        _board_item(name="beta", group_title="Ready", status_label="Ready",
                    priority="2", area="validation", detail="Do the thing."),
        _board_item(name="gamma", group_title="Locked", status_label="Locked",
                    priority="1", area="calibration",
                    detail="Wait for gate.\n\nGate: resolved_count=10 >= 25 (not met)"),
    ]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items), \
         patch("scripts.monday_sync.gql") as mock_gql, \
         patch("scripts.monday_sync.time.sleep"):
        ms.phase2_sync(board_id=999, dry_run=False, post_progress=True)

    # exactly one call: the "no changes" summary post to the log item
    assert mock_gql.call_count == 1
    variables = mock_gql.call_args[0][1]
    assert variables["item_id"] == "log-1"
    assert "no changes" in variables["body"]


def test_phase3_posts_transition_update_and_summary(tmp_backlog2, monkeypatch, tmp_path):
    """beta transitions Blocked -> Ready -- must get its own Updates-tab
    post AND be reflected in the summary; alpha/gamma are no-ops and must
    not generate any per-item post."""
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ms, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(ms, "LOG_PATH", tmp_path / "monday_sync.log")

    schema = _phase2_schema()
    log_item = {"id": "log-1", "name": ms.LOG_ITEM_TITLE,
                "group": {"id": "g_to-do", "title": "To-Do"}, "column_values": []}
    board_items = [
        log_item,
        _board_item(name="alpha", group_title="Completed", status_label="Done",
                    priority="1", area="calibration", detail="Fixed the bug."),
        _board_item(name="beta", group_title="Blocked", status_label="Blocked",  # wrong -- should be Ready
                    priority="2", area="validation", detail="Do the thing."),
        _board_item(name="gamma", group_title="Locked", status_label="Locked",
                    priority="1", area="calibration",
                    detail="Wait for gate.\n\nGate: resolved_count=10 >= 25 (not met)"),
    ]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items), \
         patch("scripts.monday_sync.gql") as mock_gql, \
         patch("scripts.monday_sync.time.sleep"):
        summary = ms.phase2_sync(board_id=999, dry_run=False, post_progress=True)

    assert summary["updated"] == ["beta"]
    # calls: beta's change_multiple_column_values, beta's move_item_to_group,
    # beta's create_update, then the summary create_update -- 4 total.
    assert mock_gql.call_count == 4
    update_calls = [c for c in mock_gql.call_args_list
                    if "create_update" in c[0][0]]
    assert len(update_calls) == 2  # beta's transition post + the summary post
    beta_post = update_calls[0][0][1]
    assert beta_post["item_id"] == "item-1"  # _board_item's fixed id
    assert "Blocked -> Ready" in beta_post["body"]
    summary_post = update_calls[1][0][1]
    assert summary_post["item_id"] == "log-1"
    assert "1 transition" in summary_post["body"]
    assert "0 content-only" in summary_post["body"]


def test_phase3_dry_run_posts_nothing(tmp_backlog2, monkeypatch, tmp_path):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    monkeypatch.setattr(ms, "LOG_PATH", tmp_path / "monday_sync.log")

    schema = _phase2_schema()
    board_items = []  # everything would be created

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items), \
         patch("scripts.monday_sync.gql") as mock_gql:
        summary = ms.phase2_sync(board_id=999, dry_run=True, post_progress=True)

    assert summary["created"] == ["alpha", "beta", "gamma"]
    mock_gql.assert_not_called()


def test_phase3_persists_log_item_id_to_config(tmp_backlog2, monkeypatch, tmp_path):
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ms, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(ms, "LOG_PATH", tmp_path / "monday_sync.log")

    schema = _phase2_schema()
    log_item = {"id": "log-99", "name": ms.LOG_ITEM_TITLE,
                "group": {"id": "g_to-do", "title": "To-Do"}, "column_values": []}
    board_items = [
        log_item,
        _board_item(name="alpha", group_title="Completed", status_label="Done",
                    priority="1", area="calibration", detail="Fixed the bug."),
        _board_item(name="beta", group_title="Ready", status_label="Ready",
                    priority="2", area="validation", detail="Do the thing."),
        _board_item(name="gamma", group_title="Locked", status_label="Locked",
                    priority="1", area="calibration",
                    detail="Wait for gate.\n\nGate: resolved_count=10 >= 25 (not met)"),
    ]

    with patch("scripts.monday_sync.get_board_schema", return_value=schema), \
         patch("scripts.monday_sync.get_all_items", return_value=board_items), \
         patch("scripts.monday_sync.gql") as mock_gql, \
         patch("scripts.monday_sync.time.sleep"):
        ms.phase2_sync(board_id=999, dry_run=False, post_progress=True)

    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["monday"]["log_item_id"] == "log-99"


# ─── Phase 4: _resolve_dry_run() ────────────────────────────────────────────

def _args(dry_run=False, live=False):
    ns = MagicMock()
    ns.dry_run = dry_run
    ns.live = live
    return ns


def test_resolve_dry_run_explicit_dry_run_flag():
    assert ms._resolve_dry_run(_args(dry_run=True)) is True


def test_resolve_dry_run_explicit_live_flag():
    assert ms._resolve_dry_run(_args(live=True)) is False


def test_resolve_dry_run_rejects_both_flags():
    with pytest.raises(SystemExit, match="mutually exclusive"):
        ms._resolve_dry_run(_args(dry_run=True, live=True))


def test_resolve_dry_run_falls_back_to_config_dry_run_default_true(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"monday": {"dry_run_default": True}}), encoding="utf-8")
    monkeypatch.setattr(ms, "CONFIG_PATH", cfg_path)
    assert ms._resolve_dry_run(_args()) is True


def test_resolve_dry_run_falls_back_to_config_dry_run_default_false(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"monday": {"dry_run_default": False}}), encoding="utf-8")
    monkeypatch.setattr(ms, "CONFIG_PATH", cfg_path)
    assert ms._resolve_dry_run(_args()) is False


def test_resolve_dry_run_defaults_safe_when_config_missing(tmp_path, monkeypatch):
    """No config.json at all -- must default to dry-run (safe), never live."""
    monkeypatch.setattr(ms, "CONFIG_PATH", tmp_path / "does_not_exist.json")
    assert ms._resolve_dry_run(_args()) is True


def test_resolve_dry_run_defaults_safe_when_key_absent(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"monday": {}}), encoding="utf-8")
    monkeypatch.setattr(ms, "CONFIG_PATH", cfg_path)
    assert ms._resolve_dry_run(_args()) is True


def test_resolve_dry_run_explicit_flags_override_config(tmp_path, monkeypatch):
    """Even if config says dry_run_default=False, an explicit --dry-run
    flag must still win."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"monday": {"dry_run_default": False}}), encoding="utf-8")
    monkeypatch.setattr(ms, "CONFIG_PATH", cfg_path)
    assert ms._resolve_dry_run(_args(dry_run=True)) is True


# ─── Phase 4: _update_config_monday_section preserves dry_run_default ─────

def test_update_config_monday_section_preserves_existing_dry_run_default(tmp_path, monkeypatch):
    """Regression guard: an earlier version hardcoded dry_run_default=True
    on every write, silently clobbering a value Reed had deliberately set
    to False -- now that the script actually reads this field (Phase 4),
    clobbering it would be a real behavior change no one asked for."""
    monkeypatch.setenv("MONDAY_API_TOKEN", "tok")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"monday": {"dry_run_default": False}}), encoding="utf-8")
    monkeypatch.setattr(ms, "CONFIG_PATH", cfg_path)

    schema = _phase2_schema()
    ms._update_config_monday_section(999, schema, "text_bid", "date_cd")

    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved["monday"]["dry_run_default"] is False
