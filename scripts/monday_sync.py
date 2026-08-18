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
  Phase 2+ (one-way push sync, progress log, scheduling) - not yet built.

Usage:
    python scripts/monday_sync.py --phase1              # live run
    python scripts/monday_sync.py --phase1 --dry-run    # preview, writes nothing

MONDAY_API_TOKEN must be set in .env (never committed -- see .gitignore).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

load_dotenv()

from backlog.engine import load_backlog

CONFIG_PATH = ROOT / "config.json"
BACKLOG_PATH = ROOT / "backlog" / "backlog.json"
API_URL = "https://api.monday.com/v2"
API_VERSION = "2024-10"

DEFAULT_BOARD_ID = 18426940027
EXPECTED_GROUPS = {"Ready", "Locked", "Blocked", "Completed"}
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


def _update_config_monday_section(board_id: int, schema: dict, backlog_id_col: str, completed_date_col: str) -> None:
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
        "dry_run_default": True,
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
        "log_item_id": cfg.get("monday", {}).get("log_item_id", "<fill in Phase 3>"),
    }
    save_local_config(cfg)
    print("  [monday_sync] config.json 'monday' section updated with resolved ids")


def main() -> int:
    parser = argparse.ArgumentParser(description="Leviathan <-> monday.com sync")
    parser.add_argument("--phase1", action="store_true", help="Run Phase 1: board schema prep")
    parser.add_argument("--board-id", type=int, default=DEFAULT_BOARD_ID)
    parser.add_argument("--dry-run", action="store_true", help="Preview writes, write nothing")
    args = parser.parse_args()

    if args.phase1:
        phase1_setup(board_id=args.board_id, dry_run=args.dry_run)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
