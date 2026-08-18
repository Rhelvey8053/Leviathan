"""
scripts/monday_sync.py - Leviathan backlog <-> monday.com board sync.

Source of truth stays backlog/backlog.json (see docs/monday_sync_discovery.md
for the full Phase 0 investigation). monday is a view + progress log, not an
editing surface, in v1. GraphQL over REST -- deliberately not the interactive
monday MCP connector, since a scheduled script can't depend on one.

Phases (see the original handoff for the full plan):
  Phase 0 (done)  - discovery + backfill. docs/monday_sync_discovery.md.
  Phase 1 (this file implements `--phase1`) - board schema prep: create the
    backlog_id (text) and Completed date (date) columns if absent, backfill
    backlog_id onto every board item whose current Name matches a
    backlog.json id (the join key going forward -- Name match is a one-time
    bridge, not the steady-state match strategy), verify the four groups
    and status labels exist. Idempotent: re-running with nothing to change
    makes zero writes.
  Phase 2 (this file implements `--phase2`) - one-way push sync,
    local -> monday. Matches every backlog.json item to its board item by
    backlog_id (the Phase 1 join key, not Name -- Name is no longer
    trusted as a match key from here on). Creates any backlog.json item
    with no board match; for existing matches, writes only the columns
    that actually differ (status, group, priority, area, detail) --
    never touches an item whose board state already agrees with
    backlog.json. Stamps Completed date ONLY on a genuine fresh
    ready/locked/blocked -> done transition observed in THIS run (i.e.
    the board's own status before this write wasn't already "Done") --
    never backdates or invents a completion date for an item that was
    already done before the sync existed; see docs/monday_sync_discovery.md
    section 8 for why historical dates are a separate, git-sourced task.
    Board items with no backlog_id at all are left completely untouched
    (unmanaged). --dry-run prints the full diff and writes nothing.
  Phase 3 (this file implements `--phase3`) - progress log, layered onto
    the same Phase 2 sync pass (phase2_sync(post_progress=True)), not a
    separate mechanism. Posts one dated Updates-tab comment on every item
    that was created, transitioned status/group, or was newly marked done
    -- NOT on a routine content-only detail/priority/area sync, which
    would just be noise. Creates (idempotently, matched by exact Name) one
    pinned "Leviathan Sync Log" item in the To-Do group and posts exactly
    one per-run summary line to it every run, including a no-op run
    ("no changes") -- never silent, never more than one summary post.
    log_item_id is resolved once and persisted into config.json.
  Phase 4 (scheduling, runbook) - not yet built.

Usage:
    python scripts/monday_sync.py --phase1              # live run
    python scripts/monday_sync.py --phase1 --dry-run    # preview, writes nothing
    python scripts/monday_sync.py --phase2              # live run (sync only)
    python scripts/monday_sync.py --phase2 --dry-run    # preview, writes nothing
    python scripts/monday_sync.py --phase3              # live run (sync + progress log)
    python scripts/monday_sync.py --phase3 --dry-run    # preview, writes nothing

MONDAY_API_TOKEN must be set in .env (never committed -- see .gitignore).
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

load_dotenv()

from backlog.engine import load_backlog

CONFIG_PATH = ROOT / "config.json"
BACKLOG_PATH = ROOT / "backlog" / "backlog.json"
LOG_PATH = ROOT / "logs" / "monday_sync.log"
API_URL = "https://api.monday.com/v2"
API_VERSION = "2024-10"

DEFAULT_BOARD_ID = 18426940027
EXPECTED_GROUPS = {"Ready", "Locked", "Blocked", "Completed"}
DETAIL_MAX_CHARS = 1500
DETAIL_TRUNCATION_NOTE = "\n\n... [truncated for the board -- full text lives in backlog.json]"

STATUS_TO_GROUP = {"ready": "Ready", "locked": "Locked", "blocked": "Blocked", "done": "Completed"}
STATUS_TO_LABEL = {"ready": "Ready", "locked": "Locked", "blocked": "Blocked", "done": "Done"}
EXPECTED_STATUS_LABELS = {"Ready", "Locked", "Blocked", "Done"}


def _token() -> str:
    token = os.environ.get("MONDAY_API_TOKEN")
    if not token:
        raise RuntimeError("MONDAY_API_TOKEN not set in .env")
    return token


def gql(query: str, variables: dict | None = None, max_retries: int = 4) -> dict:
    """
    POST a GraphQL query/mutation. The monday API returns HTTP 200 even on
    GraphQL-level errors, so success is only "no `errors` key in the body",
    never the status code alone. Retries on 429 with exponential backoff.
    """
    headers = {
        "Authorization": _token(),
        "Content-Type": "application/json",
        "API-Version": API_VERSION,
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    for attempt in range(max_retries):
        resp = requests.post(API_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code == 429:
            wait = 5 * (2 ** attempt)
            print(f"  [monday] 429 rate limited, retrying in {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"monday API error: {json.dumps(body['errors'])}")
        return body["data"]
    raise RuntimeError("monday API: exceeded retries on repeated 429")


def load_local_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_local_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def get_board_schema(board_id: int) -> dict:
    data = gql(
        """
        query($board_id: [ID!]) {
          boards(ids: $board_id) {
            name
            groups { id title }
            columns { id title type settings_str }
          }
        }
        """,
        {"board_id": [str(board_id)]},
    )
    boards = data["boards"]
    if not boards:
        raise RuntimeError(f"monday board {board_id} not found (bad id, or token lacks access)")
    return boards[0]


def get_all_items(board_id: int) -> list[dict]:
    items: list[dict] = []
    cursor = None
    while True:
        if cursor is None:
            data = gql(
                """
                query($board_id: [ID!]) {
                  boards(ids: $board_id) {
                    items_page(limit: 100) {
                      cursor
                      items { id name group { id title } column_values { id text value } }
                    }
                  }
                }
                """,
                {"board_id": [str(board_id)]},
            )
            page = data["boards"][0]["items_page"]
        else:
            data = gql(
                """
                query($cursor: String!) {
                  next_items_page(limit: 100, cursor: $cursor) {
                    cursor
                    items { id name group { id title } column_values { id text value } }
                  }
                }
                """,
                {"cursor": cursor},
            )
            page = data["next_items_page"]
        items.extend(page["items"])
        cursor = page["cursor"]
        if not cursor:
            break
    return items


def ensure_column(board_id: int, schema: dict, title: str, column_type: str, dry_run: bool) -> tuple[str, bool]:
    """Returns (column_id, created). Idempotent -- does nothing if a column
    with this exact title already exists."""
    for col in schema["columns"]:
        if col["title"] == title:
            return col["id"], False

    if dry_run:
        print(f"  [dry-run] would create column {title!r} ({column_type})")
        return f"<would-create:{title}>", True

    print(f"  [monday] creating column {title!r} ({column_type})")
    data = gql(
        """
        mutation($board_id: ID!, $title: String!, $column_type: ColumnType!) {
          create_column(board_id: $board_id, title: $title, column_type: $column_type) { id }
        }
        """,
        {"board_id": str(board_id), "title": title, "column_type": column_type},
    )
    return data["create_column"]["id"], True


def _status_labels(schema: dict) -> set[str]:
    for col in schema["columns"]:
        if col["title"] == "Status" and col["type"] == "status":
            settings = json.loads(col["settings_str"])
            return set(settings.get("labels", {}).values())
    return set()


def verify_groups_and_labels(schema: dict) -> None:
    """Fail loud (per the handoff's own rule) rather than silently creating
    duplicate groups/labels if something expected is missing -- these are
    cheap to verify and expensive to get wrong on a live board."""
    group_titles = {g["title"] for g in schema["groups"]}
    missing_groups = EXPECTED_GROUPS - group_titles
    if missing_groups:
        raise RuntimeError(f"monday board is missing expected group(s): {missing_groups}")

    labels = _status_labels(schema)
    missing_labels = EXPECTED_STATUS_LABELS - labels
    if missing_labels:
        raise RuntimeError(f"monday board's Status column is missing expected label(s): {missing_labels}")

    print(f"  [monday] verified: groups {sorted(group_titles)} present, "
          f"status labels {sorted(labels)} present")


def phase1_setup(board_id: int = DEFAULT_BOARD_ID, dry_run: bool = False) -> dict:
    """
    Board schema prep (handoff Phase 1). Returns a summary dict:
    {backlog_id_col, completed_date_col, items_backfilled,
     items_already_set, items_unmatched}.
    """
    print(f"[monday_sync] Phase 1 {'(dry-run) ' if dry_run else ''}-- board {board_id}")

    schema = get_board_schema(board_id)
    verify_groups_and_labels(schema)

    backlog_id_col, created_a = ensure_column(board_id, schema, "backlog_id", "text", dry_run)
    completed_date_col, created_b = ensure_column(board_id, schema, "Completed date", "date", dry_run)
    if created_a or created_b:
        # re-fetch schema so subsequent lookups (none needed right now, but
        # keeps this function correct if extended) see the real column ids
        # rather than the dry-run placeholders.
        if not dry_run:
            schema = get_board_schema(board_id)

    bl = load_backlog(BACKLOG_PATH)
    bl_ids = {i["id"] for i in bl["items"]}

    items = get_all_items(board_id)

    def backlog_id_value(item: dict) -> str | None:
        for cv in item["column_values"]:
            if cv["id"] == backlog_id_col:
                return cv["text"] or None
        return None

    backfilled = 0
    already_set = 0
    unmatched = []

    for item in items:
        name = item["name"]
        if name not in bl_ids:
            unmatched.append(name)
            continue

        current = backlog_id_value(item)
        if current == name:
            already_set += 1
            continue

        if dry_run:
            print(f"  [dry-run] would set backlog_id={name!r} on item {item['id']} ({name!r})")
            backfilled += 1
            continue

        gql(
            """
            mutation($item_id: ID!, $board_id: ID!, $column_id: String!, $value: String!) {
              change_simple_column_value(item_id: $item_id, board_id: $board_id,
                column_id: $column_id, value: $value) { id }
            }
            """,
            {"item_id": item["id"], "board_id": str(board_id),
             "column_id": backlog_id_col, "value": name},
        )
        backfilled += 1
        time.sleep(0.3)  # space out writes, per the handoff's API notes

    summary = {
        "backlog_id_col": backlog_id_col,
        "completed_date_col": completed_date_col,
        "items_backfilled": backfilled,
        "items_already_set": already_set,
        "items_unmatched": unmatched,
    }

    print(f"[monday_sync] Phase 1 summary: backfilled={backfilled} "
          f"already_set={already_set} unmatched={len(unmatched)}")
    if unmatched:
        print(f"  unmatched board items (no backlog.json id match): {unmatched}")

    if not dry_run:
        _update_config_monday_section(board_id, schema, backlog_id_col, completed_date_col)

    return summary


def _update_config_monday_section(board_id: int, schema: dict, backlog_id_col: str,
                                   completed_date_col: str, log_item_id: str | None = None) -> None:
    """Persists resolved live ids into config.json's monday section (created
    if absent) -- section 5 of the handoff. config.json is gitignored; this
    never touches config.example.json."""
    cfg = load_local_config()

    groups = {g["title"].lower(): g["id"] for g in schema["groups"] if g["title"] in EXPECTED_GROUPS}
    columns_by_title = {c["title"]: c["id"] for c in schema["columns"]}

    cfg["monday"] = {
        "enabled": True,
        "api_token_env": "MONDAY_API_TOKEN",
        "board_id": board_id,
        "workspace_id": cfg.get("monday", {}).get("workspace_id", 17049665),
        "dry_run_default": cfg.get("monday", {}).get("dry_run_default", True),
        "groups": {
            "ready": groups.get("ready"),
            "locked": groups.get("locked"),
            "blocked": groups.get("blocked"),
            "done": groups.get("completed"),
        },
        "columns": {
            "priority": columns_by_title.get("Priority"),
            "area": columns_by_title.get("Area"),
            "detail": columns_by_title.get("Detail"),
            "status": columns_by_title.get("Status"),
            "backlog_id": backlog_id_col,
            "completed_date": completed_date_col,
        },
        "log_item_id": log_item_id or cfg.get("monday", {}).get("log_item_id", "<fill in Phase 3>"),
    }
    save_local_config(cfg)
    print("  [monday_sync] config.json 'monday' section updated with resolved ids")


# ─── Phase 2: one-way push sync ────────────────────────────────────────────

def _gate_context(item: dict) -> str:
    """Appends gate/depends_on context to a locked/blocked item's Detail
    text, per the handoff's field mapping (section 6)."""
    status = item.get("status")
    if status == "locked":
        conds = item.get("trigger", {}).get("all", [])
        if conds:
            gate = "; ".join(f"{c['metric']} {c['op']} {c['value']}" for c in conds)
            return f"\n\nGate: {gate}"
    elif status == "blocked":
        deps = item.get("depends_on", [])
        if deps:
            return f"\n\nWaiting on: {', '.join(deps)}"
    return ""


def render_detail(item: dict) -> str:
    text = (item.get("action") or "") + _gate_context(item)
    if len(text) > DETAIL_MAX_CHARS:
        keep = DETAIL_MAX_CHARS - len(DETAIL_TRUNCATION_NOTE)
        text = text[:keep] + DETAIL_TRUNCATION_NOTE
    return text


def expected_fields(item: dict) -> dict:
    return {
        "status_label": STATUS_TO_LABEL[item["status"]],
        "group_title": STATUS_TO_GROUP[item["status"]],
        "priority": item["priority"],
        "area": item["area"],
        "detail": render_detail(item),
    }


def _resolve_column_ids(schema: dict) -> dict:
    by_title = {c["title"]: c["id"] for c in schema["columns"]}
    required = {"Status": "status", "Priority": "priority", "Area": "area",
                "Detail": "detail", "backlog_id": "backlog_id", "Completed date": "completed_date"}
    ids = {}
    for title, key in required.items():
        if title not in by_title:
            raise RuntimeError(
                f"monday board is missing expected column {title!r} -- run --phase1 first"
            )
        ids[key] = by_title[title]
    return ids


def current_fields(board_item: dict, col_ids: dict) -> dict:
    cv_by_id = {cv["id"]: cv for cv in board_item["column_values"]}
    return {
        "status_label": cv_by_id.get(col_ids["status"], {}).get("text"),
        "group_title": board_item["group"]["title"],
        "priority": cv_by_id.get(col_ids["priority"], {}).get("text"),
        "area": cv_by_id.get(col_ids["area"], {}).get("text"),
        "detail": cv_by_id.get(col_ids["detail"], {}).get("text"),
    }


def _diff(item: dict, board_item: dict | None, col_ids: dict) -> dict:
    """Returns {field: (old, new)} for fields that actually differ. Never
    includes a field whose current value already matches -- the caller
    uses an empty dict (and no fresh-done transition) as "nothing to do"."""
    exp = expected_fields(item)
    if board_item is None:
        return {"_create": (None, exp)}

    cur = current_fields(board_item, col_ids)
    changes = {}
    if cur["group_title"] != exp["group_title"]:
        changes["group"] = (cur["group_title"], exp["group_title"])
    if cur["status_label"] != exp["status_label"]:
        changes["status"] = (cur["status_label"], exp["status_label"])
    if cur["priority"] != str(exp["priority"]):
        changes["priority"] = (cur["priority"], exp["priority"])
    if (cur["area"] or "") != exp["area"]:
        changes["area"] = (cur["area"], exp["area"])
    if (cur["detail"] or "") != exp["detail"]:
        changes["detail"] = (len(cur["detail"] or ""), len(exp["detail"]))
    return changes


def _create_board_item(board_id: int, group_id: str, item: dict, col_ids: dict) -> str:
    exp = expected_fields(item)
    cvs = {
        col_ids["status"]: {"label": exp["status_label"]},
        col_ids["priority"]: str(exp["priority"]),
        col_ids["area"]: exp["area"],
        col_ids["detail"]: exp["detail"],
        col_ids["backlog_id"]: item["id"],
    }
    data = gql(
        """
        mutation($board_id: ID!, $group_id: String!, $item_name: String!, $column_values: JSON!) {
          create_item(board_id: $board_id, group_id: $group_id, item_name: $item_name,
            column_values: $column_values, create_labels_if_missing: true) { id }
        }
        """,
        {"board_id": str(board_id), "group_id": group_id, "item_name": item["id"],
         "column_values": json.dumps(cvs)},
    )
    time.sleep(0.3)
    return data["create_item"]["id"]


def _apply_update(board_id: int, item_id: str, col_ids: dict, exp: dict,
                   changes: dict, newly_done: bool, groups_by_title: dict) -> None:
    cvs = {}
    if "status" in changes:
        cvs[col_ids["status"]] = {"label": exp["status_label"]}
    if "priority" in changes:
        cvs[col_ids["priority"]] = str(exp["priority"])
    if "area" in changes:
        cvs[col_ids["area"]] = exp["area"]
    if "detail" in changes:
        cvs[col_ids["detail"]] = exp["detail"]
    if newly_done:
        cvs[col_ids["completed_date"]] = {"date": date.today().isoformat()}

    if cvs:
        gql(
            """
            mutation($board_id: ID!, $item_id: ID!, $column_values: JSON!) {
              change_multiple_column_values(board_id: $board_id, item_id: $item_id,
                column_values: $column_values, create_labels_if_missing: true) { id }
            }
            """,
            {"board_id": str(board_id), "item_id": item_id, "column_values": json.dumps(cvs)},
        )
        time.sleep(0.3)

    if "group" in changes:
        gql(
            """
            mutation($item_id: ID!, $group_id: String!) {
              move_item_to_group(item_id: $item_id, group_id: $group_id) { id }
            }
            """,
            {"item_id": item_id, "group_id": groups_by_title[exp["group_title"]]},
        )
        time.sleep(0.3)


def _log_summary(summary: dict, dry_run: bool) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    line = (
        f"{ts} {'[dry-run] ' if dry_run else ''}"
        f"created={len(summary['created'])} updated={len(summary['updated'])} "
        f"moved={len(summary['moved'])} completed_stamped={len(summary['completed_stamped'])} "
        f"unmatched_board_items={len(summary['unmatched_board_items'])}"
    )
    if summary["created"]:
        line += f" | created: {summary['created']}"
    if summary["moved"]:
        line += f" | moved: {summary['moved']}"
    if summary["completed_stamped"]:
        line += f" | completed_stamped: {summary['completed_stamped']}"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"[monday_sync] {line}")


LOG_ITEM_TITLE = "Leviathan Sync Log"
LOG_ITEM_GROUP = "To-Do"


def _post_update(item_id: str, body: str) -> None:
    gql(
        """
        mutation($item_id: ID!, $body: String!) {
          create_update(item_id: $item_id, body: $body) { id }
        }
        """,
        {"item_id": item_id, "body": body},
    )
    time.sleep(0.3)


def ensure_log_item(board_id: int, schema: dict, board_items: list[dict], dry_run: bool) -> str:
    """
    Finds or creates the pinned "Leviathan Sync Log" item (handoff Phase 3).
    Idempotent -- matches by exact Name, same as Phase 1's original
    backlog_id bridge. Lives in the To-Do group (currently empty on the
    real board -- the same group the handoff's own "Set up PM" template
    item used, a reasonable home for a non-managed reference item; easy to
    move later if that's ever wrong).
    """
    for it in board_items:
        if it["name"] == LOG_ITEM_TITLE:
            return it["id"]

    if dry_run:
        print(f"  [dry-run] would create pinned log item {LOG_ITEM_TITLE!r} in {LOG_ITEM_GROUP!r}")
        return "<would-create-log-item>"

    group_id = next((g["id"] for g in schema["groups"] if g["title"] == LOG_ITEM_GROUP), None)
    if group_id is None:
        raise RuntimeError(f"monday board is missing expected group {LOG_ITEM_GROUP!r}")
    print(f"  [monday] creating pinned log item {LOG_ITEM_TITLE!r}")
    data = gql(
        """
        mutation($board_id: ID!, $group_id: String!, $item_name: String!) {
          create_item(board_id: $board_id, group_id: $group_id, item_name: $item_name) { id }
        }
        """,
        {"board_id": str(board_id), "group_id": group_id, "item_name": LOG_ITEM_TITLE},
    )
    time.sleep(0.3)
    return data["create_item"]["id"]


def _transition_message(item: dict, changes: dict, newly_done: bool, created: bool) -> str | None:
    """
    Updates-tab message for a real state change, or None if this was a
    content-only sync (detail/priority/area edit) not worth an individual
    post -- handoff Phase 3 fires per-item posts on "create, status/group
    transition, completion" only, not on every field sync.
    """
    today = date.today().isoformat()
    if created:
        return f"Created ({STATUS_TO_LABEL[item['status']]}) on {today}."

    parts = []
    if "status" in changes:
        old, new = changes["status"]
        line = f"{old} -> {new} on {today}."
        conds = item.get("trigger", {}).get("all", [])
        if item["status"] == "ready" and conds:
            gate = "; ".join(f"{c['metric']} {c['op']} {c['value']}" for c in conds)
            line += f" Gate cleared: {gate}."
        parts.append(line)
    elif "group" in changes:
        old, new = changes["group"]
        parts.append(f"Moved {old} -> {new} on {today}.")

    if newly_done:
        parts.append(f"Marked Done, Completed date stamped {today}.")

    return " ".join(parts) if parts else None


def _format_summary_line(summary: dict, transitioned: list, content_only: list) -> str:
    today = date.today().isoformat()
    if not summary["created"] and not summary["updated"]:
        return f"{today}: no changes."
    return (
        f"{today}: created {len(summary['created'])}, "
        f"updated {len(summary['updated'])} "
        f"({len(transitioned)} transition{'s' if len(transitioned) != 1 else ''}, "
        f"{len(content_only)} content-only), "
        f"completed {len(summary['completed_stamped'])}."
    )


def phase2_sync(board_id: int = DEFAULT_BOARD_ID, dry_run: bool = False,
                post_progress: bool = False) -> dict:
    """
    One-way push sync (handoff Phase 2). Returns a summary dict:
    {created, updated, moved, completed_stamped, unmatched_board_items}
    (each a list of backlog_ids, except unmatched_board_items which is a
    list of board item Names).

    post_progress=True additionally runs handoff Phase 3: posts a dated
    Updates-tab comment on every item that was created, transitioned
    status/group, or was newly marked done (not on content-only
    detail/priority/area syncs), then posts exactly one summary line to
    the pinned "Leviathan Sync Log" item -- "no changes" on a no-op run.
    dry_run suppresses every post the same way it suppresses every other
    write.
    """
    print(f"[monday_sync] Phase 2 {'(dry-run) ' if dry_run else ''}"
          f"{'+ Phase 3 progress log ' if post_progress else ''}-- board {board_id}")

    schema = get_board_schema(board_id)
    col_ids = _resolve_column_ids(schema)
    groups_by_title = {g["title"]: g["id"] for g in schema["groups"]}

    bl = load_backlog(BACKLOG_PATH)
    bl_ids = {i["id"] for i in bl["items"]}
    board_items = get_all_items(board_id)

    log_item_id = None
    if post_progress:
        log_item_id = ensure_log_item(board_id, schema, board_items, dry_run)

    by_backlog_id: dict[str, dict] = {}
    for bit in board_items:
        for cv in bit["column_values"]:
            if cv["id"] == col_ids["backlog_id"] and cv["text"]:
                by_backlog_id[cv["text"]] = bit

    # "unmatched" = no current backlog.json item claims this board item --
    # covers both "never had a backlog_id" AND "had one from an earlier
    # Phase 1/2 run but that id was since removed from backlog.json" (e.g.
    # an item dropped back to unmanaged after a schema-fit decision). Either
    # way, this sync must leave it completely untouched.
    unmatched_board_items = [
        bit["name"] for bit in board_items
        if not any(cv["id"] == col_ids["backlog_id"] and cv["text"] in bl_ids
                   for cv in bit["column_values"])
    ]

    created, updated, moved, completed_stamped = [], [], [], []
    transitioned, content_only = [], []

    for item in bl["items"]:
        bid = item["id"]
        board_item = by_backlog_id.get(bid)
        changes = _diff(item, board_item, col_ids)

        if "_create" in changes:
            exp = expected_fields(item)
            if dry_run:
                print(f"  [dry-run] would CREATE {bid!r} in group {exp['group_title']!r}")
                if post_progress:
                    print(f"  [dry-run] would post creation update on {bid!r}")
            else:
                new_id = _create_board_item(board_id, groups_by_title[exp["group_title"]], item, col_ids)
                if post_progress:
                    msg = _transition_message(item, {}, False, created=True)
                    _post_update(new_id, msg)
            created.append(bid)
            transitioned.append(bid)
            continue

        if not changes:
            continue  # already matches -- idempotent no-op

        exp = expected_fields(item)
        cur = current_fields(board_item, col_ids)
        newly_done = cur["status_label"] != "Done" and exp["status_label"] == "Done"
        is_transition = "status" in changes or "group" in changes or newly_done

        if dry_run:
            extra = " + stamp Completed date" if newly_done else ""
            print(f"  [dry-run] would UPDATE {bid!r}: {changes}{extra}")
            if post_progress and is_transition:
                print(f"  [dry-run] would post transition update on {bid!r}")
        else:
            _apply_update(board_id, board_item["id"], col_ids, exp, changes, newly_done, groups_by_title)
            if post_progress and is_transition:
                msg = _transition_message(item, changes, newly_done, created=False)
                if msg:
                    _post_update(board_item["id"], msg)
        updated.append(bid)
        if "group" in changes:
            moved.append(bid)
        if newly_done:
            completed_stamped.append(bid)
        (transitioned if is_transition else content_only).append(bid)

    summary = {
        "created": created, "updated": updated, "moved": moved,
        "completed_stamped": completed_stamped,
        "unmatched_board_items": unmatched_board_items,
    }
    _log_summary(summary, dry_run)

    if post_progress:
        summary_line = _format_summary_line(summary, transitioned, content_only)
        if dry_run:
            print(f"  [dry-run] would post summary to {LOG_ITEM_TITLE!r}: {summary_line!r}")
        else:
            _post_update(log_item_id, summary_line)
            _update_config_monday_section(board_id, schema, col_ids["backlog_id"],
                                          col_ids["completed_date"], log_item_id=log_item_id)

    return summary


def verify_phase2(board_id: int = DEFAULT_BOARD_ID) -> dict:
    """
    Read-back verification (handoff constraint #6: never report success off
    mutation responses alone). Re-queries the board fresh and asserts every
    backlog.json item's board state now matches. Returns
    {ok: bool, mismatches: [{backlog_id, diff}]}.
    """
    schema = get_board_schema(board_id)
    col_ids = _resolve_column_ids(schema)
    bl = load_backlog(BACKLOG_PATH)
    board_items = get_all_items(board_id)

    by_backlog_id = {}
    for bit in board_items:
        for cv in bit["column_values"]:
            if cv["id"] == col_ids["backlog_id"] and cv["text"]:
                by_backlog_id[cv["text"]] = bit

    mismatches = []
    for item in bl["items"]:
        board_item = by_backlog_id.get(item["id"])
        if board_item is None:
            mismatches.append({"backlog_id": item["id"], "diff": "missing from board"})
            continue
        changes = _diff(item, board_item, col_ids)
        if changes:
            mismatches.append({"backlog_id": item["id"], "diff": changes})

    ok = not mismatches
    print(f"[monday_sync] verify_phase2: {'OK' if ok else 'MISMATCHES FOUND'} "
          f"({len(bl['items'])} items checked, {len(mismatches)} mismatched)")
    for m in mismatches:
        print(f"  {m['backlog_id']}: {m['diff']}")
    return {"ok": ok, "mismatches": mismatches}


def _resolve_dry_run(args: argparse.Namespace) -> bool:
    """
    --dry-run / --live are explicit overrides (mutually exclusive). With
    neither given, falls back to config.json's monday.dry_run_default --
    the handoff's own documented safety knob (section 5), previously
    written but never actually read by this script. Defaults to True
    (safe/dry-run) if config.json or the key is missing, so a bare
    invocation from a clean prompt with no flags at all never
    accidentally writes.
    """
    if args.dry_run and args.live:
        raise SystemExit("--dry-run and --live are mutually exclusive")
    if args.dry_run:
        return True
    if args.live:
        return False
    try:
        cfg = load_local_config()
    except FileNotFoundError:
        return True
    return bool(cfg.get("monday", {}).get("dry_run_default", True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Leviathan <-> monday.com sync")
    parser.add_argument("--phase1", action="store_true", help="Run Phase 1: board schema prep")
    parser.add_argument("--phase2", action="store_true", help="Run Phase 2: one-way push sync")
    parser.add_argument("--phase3", action="store_true",
                        help="Run Phase 2 sync + Phase 3 progress log (per-item updates, pinned summary)")
    parser.add_argument("--board-id", type=int, default=DEFAULT_BOARD_ID)
    parser.add_argument("--dry-run", action="store_true", help="Preview writes, write nothing")
    parser.add_argument("--live", action="store_true",
                        help="Force a live run, overriding config.json's monday.dry_run_default")
    parser.add_argument("--once", action="store_true",
                        help="No-op marker for the scheduled task -- this script always runs "
                             "once per invocation and exits; accepted so a --once in the "
                             "scheduler's command line doesn't need special-casing.")
    args = parser.parse_args()
    dry_run = _resolve_dry_run(args)

    if args.phase1:
        phase1_setup(board_id=args.board_id, dry_run=dry_run)
        return 0

    if args.phase2:
        phase2_sync(board_id=args.board_id, dry_run=dry_run, post_progress=False)
        if not dry_run:
            verify_phase2(board_id=args.board_id)
        return 0

    # Reached only when --phase1/--phase2 weren't given (both return above)
    # -- covers --phase3 explicitly AND no --phaseN flag at all. This is
    # the full steady-state sync (Phase 2 + Phase 3 together): what the
    # scheduled task runs, and what a bare
    # `python scripts/monday_sync.py --dry-run` is meant to preview.
    phase2_sync(board_id=args.board_id, dry_run=dry_run, post_progress=True)
    if not dry_run:
        verify_phase2(board_id=args.board_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
