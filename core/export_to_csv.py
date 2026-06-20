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
})


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
            counts["signals"] = _table_to_csv(
                conn, "signals", os.path.join(export_dir, "signals.csv")
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
