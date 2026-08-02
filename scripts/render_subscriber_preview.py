"""
scripts/render_subscriber_preview.py — Phase 0 harness (GOAL_subscriber_report.md).

Standalone preview: reads real signals from data/leviathan.db, builds a
signals/run_meta pair matching what a live main.py run already has, and
calls core.report.render_subscriber_html() -- the actual Phase 1
implementation -- to write data/powerbi_export/subscriber_preview.html.

This file used to contain its own copy of the render logic (the original
Phase 0 reference); now it's a thin wrapper so there is exactly one
implementation of the subscriber layout, not two that could drift.

Zero pipeline changes: read-only against the DB, stdlib only.

"Calls" vs "watch": since main.py only calls logger.log_signal() for a
NEW (non-repeat) ticker -- a repeat observation intentionally does not
get a fresh row, per the pipeline's own dedup design -- the most recent
signals row per ticker already represents "the current known call" for
that market. This harness takes the latest row per ticker among
still-open markets (close_time in the future) and hands the whole
population to render_subscriber_html(), which does its own calls/watch
split exactly as it would for a live run's final_signals.

Usage:
    python scripts\\render_subscriber_preview.py
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.logger import DB_PATH
from core.report import render_subscriber_html, render_track_record_html

OUT_PATH = os.path.join(ROOT, "data", "powerbi_export", "subscriber_preview.html")
# Same directory as OUT_PATH -- the subscriber template's footer links to
# "track_record.html" as a relative sibling path (GOAL_subscriber_report.md
# Phase 6's "links from the digest footer resolve" acceptance criterion),
# so this must be written alongside it, not to an arbitrary location.
TRACK_RECORD_OUT_PATH = os.path.join(ROOT, "data", "powerbi_export", "track_record.html")
CONFIG_PATH = os.path.join(ROOT, "config.json")


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _query_latest_per_ticker(db_path: str = DB_PATH) -> list[dict]:
    """
    Latest signals row per ticker among paper signals whose market hasn't
    closed yet -- see module docstring for why "most recent row" already
    represents "the current call" given repeats are never re-logged.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT s.* FROM signals s
            INNER JOIN (
                SELECT ticker, MAX(timestamp) AS max_ts
                FROM signals
                WHERE (source = 'paper' OR source IS NULL)
                  AND ticker != ''
                  AND close_time > ?
                GROUP BY ticker
            ) latest ON s.ticker = latest.ticker AND s.timestamp = latest.max_ts
            WHERE (s.source = 'paper' OR s.source IS NULL)
            ORDER BY s.timestamp DESC
        """, (now_iso,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _latest_run_meta(db_path: str = DB_PATH) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT markets_scanned FROM runs ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return {"markets_scanned": (row["markets_scanned"] if row else 0) or 0}
    finally:
        conn.close()


def _query_resolved_recap(db_path: str = DB_PATH, days: int = 7) -> list[dict]:
    """
    Settled paper signals from the last N days (GOAL_subscriber_report.md
    Phase 5) -- same filter as core.logger.get_resolved_track_record(days),
    but queried directly against db_path like this harness's other two
    queries, rather than through core.logger's own DB_PATH global, so the
    db_path override this function already supports keeps working for every
    query the harness makes, not just two of three.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT * FROM signals
            WHERE (source = 'paper' OR source IS NULL)
              AND direction != 'PASS'
              AND outcome != '' AND outcome IS NOT NULL
              AND timestamp >= ?
            ORDER BY timestamp DESC
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def render(db_path: str = DB_PATH, now_utc: "datetime | None" = None) -> str:
    """Thin wrapper: query the DB, call the real Phase 1/5 renderer."""
    config = _load_config()
    signals = _query_latest_per_ticker(db_path)
    run_meta = _latest_run_meta(db_path)
    resolved_recap = _query_resolved_recap(db_path)
    return render_subscriber_html(signals, run_meta, config, now_utc=now_utc,
                                   resolved_recap=resolved_recap)


def main():
    out = render()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Rendered subscriber preview -> {OUT_PATH}")

    # Written alongside the digest so its "Full track record" footer link
    # (a relative "track_record.html" path) actually resolves when both
    # files are opened from the same folder.
    track_record_out = render_track_record_html()
    with open(TRACK_RECORD_OUT_PATH, "w", encoding="utf-8") as f:
        f.write(track_record_out)
    print(f"Rendered track record page -> {TRACK_RECORD_OUT_PATH}")


if __name__ == "__main__":
    main()
