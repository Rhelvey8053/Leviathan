"""
export_to_csv.py — Export leviathan.db tables to CSV for Power BI.

Writes data/powerbi_export/signals.csv and data/powerbi_export/runs.csv.
Uses stdlib only (csv + sqlite3) — no extra dependencies.
Importable without side effects; runnable standalone via __main__.
"""

import csv
import os
import sqlite3

_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH    = os.path.join(_ROOT, "leviathan.db")
EXPORT_DIR = os.path.join(_ROOT, "data", "powerbi_export")

# String columns where NULL should become "" so Power BI DAX comparisons
# (= "" and = "LOSS") work correctly. Numeric columns are left as-is.
_STRING_COLS = frozenset({
    "result", "outcome", "direction", "confidence", "flag_path", "source",
    "time_horizon", "heuristic_direction", "heuristic_label",
    "whale_direction", "ticker", "title", "run_id", "call_id",
    "close_time", "lv_band", "date",
})

# Computed columns that are derived at export time, not stored in the DB.
_COMPUTED_COLS = frozenset({
    "is_resolved", "is_win", "confidence_rank", "horizon_rank",
    "date", "pnl_scaled", "lv_band",
})

# Analysis-relevant columns written to signals.csv, in display order.
# Pipeline plumbing (run_id, from_signal, fill_count, fill_fee, outcome,
# our_estimate, direction_aligned, entry_price, signal_call_id, logged_under,
# resolution_date, whale_direction, heuristic_direction, etc.) are excluded.
WHITELIST = [
    "call_id", "date", "timestamp", "ticker", "title",
    "source", "direction", "confidence", "confidence_rank",
    "flag_path", "time_horizon", "horizon_rank",
    "market_price", "edge", "net_edge", "base_rate",
    "result", "is_resolved", "is_win", "pnl_if_traded", "pnl_scaled",
    "leviathan_score", "lv_band",
    "close_time", "sig_edge", "sig_drift", "sig_br_none",
    "watchlist_signal", "whale_detected",
    "heuristic_label", "short_horizon",
]

_CONF_RANK    = {"HIGH": 0, "MED": 1, "LOW": 2}
_HORIZON_RANK = {"INTRADAY": 0, "WEEKLY": 1, "MONTHLY": 2, "QUARTERLY": 3, "LONG": 4}


def _null_to_empty(headers: list[str], rows: list[tuple]) -> list[tuple]:
    """Replace None with '' in string columns; leave all other values untouched."""
    str_idx = {i for i, h in enumerate(headers) if h in _STRING_COLS}
    if not str_idx:
        return rows
    out = []
    for row in rows:
        row = list(row)
        for i in str_idx:
            if row[i] is None:
                row[i] = ""
        out.append(tuple(row))
    return out


def _add_computed_cols(row: dict) -> dict:
    """Return a copy of row with analysis-ready computed columns added."""
    r = dict(row)

    result              = r.get("result") or ""
    r["is_resolved"]    = 1 if result in ("WIN", "LOSS") else 0
    r["is_win"]         = 1 if result == "WIN" else 0

    conf                = (r.get("confidence") or "").upper()
    r["confidence_rank"] = _CONF_RANK.get(conf, "")

    horizon             = (r.get("time_horizon") or "").upper()
    r["horizon_rank"]   = _HORIZON_RANK.get(horizon, "")

    ts                  = r.get("timestamp") or ""
    r["date"]           = ts[:10] if ts else ""

    pnl = r.get("pnl_if_traded")
    try:
        r["pnl_scaled"] = round(float(pnl) * 10, 4)
    except (TypeError, ValueError):
        r["pnl_scaled"] = ""

    lv = r.get("leviathan_score")
    try:
        lv_int = int(lv)
        if lv_int >= 70:    r["lv_band"] = "A"
        elif lv_int >= 55:  r["lv_band"] = "B"
        elif lv_int >= 40:  r["lv_band"] = "C"
        else:               r["lv_band"] = "D"
    except (TypeError, ValueError):
        r["lv_band"] = ""

    return r


def _signals_to_csv(conn: sqlite3.Connection, dest: str) -> int:
    """
    Write signals table to CSV with computed columns added and whitelist applied.
    Pipeline plumbing columns are excluded; only WHITELIST columns are written.
    """
    cur        = conn.execute("SELECT * FROM signals")
    db_headers = [d[0] for d in cur.description]
    db_rows    = cur.fetchall()

    # Final column set: WHITELIST ∩ (DB columns ∪ computed columns), in WHITELIST order.
    available  = set(db_headers) | _COMPUTED_COLS
    final_cols = [c for c in WHITELIST if c in available]

    rows = []
    for raw in db_rows:
        row_dict = _add_computed_cols(dict(zip(db_headers, raw)))
        out_row  = []
        for col in final_cols:
            val = row_dict.get(col)
            if val is None and col in _STRING_COLS:
                val = ""
            out_row.append(val)
        rows.append(out_row)

    with open(dest, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(final_cols)
        writer.writerows(rows)
    return len(rows)


def _table_to_csv(conn: sqlite3.Connection, table: str, dest: str) -> int:
    """Write one table to a CSV file. Returns row count (excluding header)."""
    cur     = conn.execute(f"SELECT * FROM {table}")
    headers = [d[0] for d in cur.description]
    rows    = _null_to_empty(headers, cur.fetchall())
    with open(dest, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    return len(rows)


def export_csvs(db_path: str = DB_PATH, export_dir: str = EXPORT_DIR) -> dict:
    """
    Read signals and runs from leviathan.db and write CSVs to export_dir.
    Returns {"signals": row_count, "runs": row_count}.
    Prints a warning and returns zeros if the DB is missing or unreadable.
    """
    if not os.path.exists(db_path):
        print(f"[export] WARNING: DB not found at {db_path} — skipping export")
        return {"signals": 0, "runs": 0}

    os.makedirs(export_dir, exist_ok=True)

    counts = {"signals": 0, "runs": 0}
    try:
        conn = sqlite3.connect(db_path)
        try:
            counts["signals"] = _signals_to_csv(
                conn, os.path.join(export_dir, "signals.csv")
            )
            counts["runs"] = _table_to_csv(
                conn, "runs", os.path.join(export_dir, "runs.csv")
            )
        finally:
            conn.close()
    except Exception as e:
        print(f"[export] WARNING: export failed — {e}")

    return counts


if __name__ == "__main__":
    result = export_csvs()
    print(f"[export] signals.csv: {result['signals']} rows")
    print(f"[export] runs.csv:    {result['runs']} rows")
    print(f"[export] Written to:  {EXPORT_DIR}")
