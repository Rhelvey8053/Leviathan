"""
analysis/eval_dataset.py — Freeze the resolved track record as a versioned
eval dataset.

Pulls through the MCP server's get_resolved_track_record tool (the same
surface mcp_server/server.py exposes) rather than writing a second query
path against the DB. Each row: market_id, our_estimate, market_price at
signal time, and the actual resolved outcome (both as "YES"/"NO" and as
a binary 0/1 for the grader).

Usage:
    python analysis/eval_dataset.py     # freeze + print a summary
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from mcp_server import server as mcp_server

DATA_DIR = ROOT / "analysis" / "eval_data"


def _fetch_resolved_rows() -> list[dict]:
    """Call the MCP server's resolved-outcomes tool in-process and return the raw rows."""
    _content, structured = asyncio.run(
        mcp_server.mcp.call_tool("get_resolved_track_record", {})
    )
    return structured.get("result", [])


def freeze_dataset(version: str | None = None) -> dict:
    """
    Pulls the current resolved track record and writes it as a versioned
    JSON file (analysis/eval_data/resolved_<version>.json) plus a
    resolved_latest.json pointer. Returns the frozen payload.
    """
    rows = _fetch_resolved_rows()
    version = version or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    dataset_rows = []
    for r in rows:
        outcome = (r.get("outcome") or "").strip().upper()
        dataset_rows.append({
            "market_id":            r["ticker"],
            "our_estimate":         r["our_estimate"],
            "market_price":         r["market_price"],
            "actual_outcome":       outcome,
            "actual_outcome_binary": 1 if outcome == "YES" else 0,
        })

    payload = {
        "version": version,
        "n": len(dataset_rows),
        "source": "mcp_server.server:get_resolved_track_record",
        "rows": dataset_rows,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dated_path = DATA_DIR / f"resolved_{version}.json"
    latest_path = DATA_DIR / "resolved_latest.json"
    for path in (dated_path, latest_path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    return payload


def load_latest() -> dict:
    """Load the most recently frozen dataset, freezing one now if none exists."""
    latest_path = DATA_DIR / "resolved_latest.json"
    if not latest_path.exists():
        return freeze_dataset()
    with open(latest_path, encoding="utf-8") as f:
        return json.load(f)


def main():
    payload = freeze_dataset()
    print(f"Froze {payload['n']} resolved rows as version {payload['version']}")
    print(f"  -> analysis/eval_data/resolved_{payload['version']}.json")
    print(f"  -> analysis/eval_data/resolved_latest.json")
    for row in payload["rows"]:
        print(f"  {row['market_id']:<45} est={row['our_estimate']:.3f}  "
              f"price={row['market_price']:.3f}  outcome={row['actual_outcome']}")


if __name__ == "__main__":
    main()
