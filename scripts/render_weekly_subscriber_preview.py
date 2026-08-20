"""
scripts/render_weekly_subscriber_preview.py — editorial weekly preview
harness (leviathan-report-format-decision.md Phase 2).

Mirrors scripts/render_subscriber_preview.py's own shape: read-only DB
queries, stdlib only, thin wrapper calling the real renderer
(core.report.render_weekly_subscriber_html) so there is exactly one
implementation of the weekly layout, not two that could drift. Reuses
the same weekly data computations main.py's live Sunday send already
uses (core.logger.get_week_signals/get_stats/get_stats_by_flag_path/
get_stats_by_heuristic_label/get_stats_by_whale/get_brier_score/
get_stats_by_leviathan_score) -- no new or divergent numbers, plus
get_market_baseline_brier_score() (existing, just not previously
threaded into either weekly renderer).

Two genuinely new read-only queries, following this harness's own
established pattern of direct sqlite3 queries with a db_path override
(same as _query_resolved_recap/_query_latest_per_ticker in the daily
harness): _query_upcoming_resolutions (pending signals closing in the
next 7 days) and _query_markets_scanned_week (sum of runs.markets_scanned
in the last 7 days) -- presentational aggregations over already-existing
columns, not new kinds of statistics.

NOT scheduled -- run manually. Does not send anything; see
scripts/send_editorial_selftest.py for that (daily only, as of
leviathan-report-format-decision.md Phase 3).

Usage:
    python scripts\\render_weekly_subscriber_preview.py
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import logger
from core.logger import DB_PATH
from core.report import render_weekly_subscriber_html

OUT_PATH = os.path.join(ROOT, "data", "powerbi_export", "weekly_preview.html")
CONFIG_PATH = os.path.join(ROOT, "config.json")


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _query_upcoming_resolutions(db_path: str = DB_PATH, days: int = 7) -> list[dict]:
    """Pending (unresolved) paper signals whose close_time falls in the next N days."""
    now_iso = datetime.now(timezone.utc).isoformat()
    cutoff  = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT * FROM signals
            WHERE (source = 'paper' OR source IS NULL)
              AND direction != 'PASS'
              AND (result = '' OR result IS NULL)
              AND close_time > ? AND close_time <= ?
            ORDER BY close_time ASC
        """, (now_iso, cutoff)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _query_markets_scanned_week(db_path: str = DB_PATH, days: int = 7) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(markets_scanned), 0) FROM runs WHERE timestamp >= ?",
            (cutoff,),
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def render(db_path: str = DB_PATH, now_utc: "datetime | None" = None) -> str:
    """Thin wrapper: query the DB (via core.logger where an existing function
    covers it, direct sqlite3 for the two new presentational queries), call
    the real Phase 2 renderer."""
    config = _load_config()

    week_signals = logger.get_week_signals(days=7)
    stats             = logger.get_stats()
    flag_path_stats   = logger.get_stats_by_flag_path()
    heuristic_stats   = logger.get_stats_by_heuristic_label()
    whale_stats       = logger.get_stats_by_whale()
    brier             = logger.get_brier_score()
    market_baseline_brier = logger.get_market_baseline_brier_score()
    lv_stats          = logger.get_stats_by_leviathan_score()

    resolved_recap = logger.get_resolved_track_record(days=7)
    upcoming       = _query_upcoming_resolutions(db_path)
    markets_scanned_week = _query_markets_scanned_week(db_path)

    return render_weekly_subscriber_html(
        week_signals, stats, config,
        flag_path_stats=flag_path_stats,
        brier=brier,
        market_baseline_brier=market_baseline_brier,
        lv_stats=lv_stats,
        heuristic_label_stats=heuristic_stats,
        whale_stats=whale_stats,
        resolved_recap=resolved_recap,
        upcoming=upcoming,
        markets_scanned_week=markets_scanned_week,
        now_utc=now_utc,
    )


def main():
    out = render()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Rendered weekly subscriber preview -> {OUT_PATH}")


if __name__ == "__main__":
    main()
