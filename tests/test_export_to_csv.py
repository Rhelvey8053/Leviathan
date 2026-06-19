"""
tests/test_export_to_csv.py — Offline tests for export_to_csv.py (Goal 3c).

All tests use a tmp SQLite DB — no network calls, no writes to the real DB.
No existing test was modified.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from export_to_csv import export_csvs


def _make_db(path: str, with_data: bool = True) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            call_id TEXT PRIMARY KEY, timestamp TEXT, ticker TEXT, title TEXT,
            market_price REAL, our_estimate REAL, edge REAL, direction TEXT,
            confidence TEXT, outcome TEXT, result TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, timestamp TEXT, markets_scanned INTEGER,
            signals_generated INTEGER, model_used TEXT
        );
    """)
    if with_data:
        conn.execute("""
            INSERT INTO signals VALUES
            ('abc1','2026-06-19T00:00:00Z','KXTEST-001','Test market',
             0.55,0.70,0.15,'YES','MED',NULL,NULL)
        """)
        conn.execute("""
            INSERT INTO runs VALUES
            ('run1','2026-06-19T00:00:00Z',100,1,'claude-sonnet-4-6')
        """)
    conn.commit()
    conn.close()


class TestExportCreatesFiles(unittest.TestCase):

    def test_signals_and_runs_csvs_created(self):
        """export_csvs() creates both signals.csv and runs.csv."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            out = os.path.join(tmpdir, "export")
            _make_db(db)
            export_csvs(db_path=db, export_dir=out)
            self.assertTrue(os.path.exists(os.path.join(out, "signals.csv")))
            self.assertTrue(os.path.exists(os.path.join(out, "runs.csv")))

    def test_signals_csv_has_expected_columns(self):
        """signals.csv contains the core schema columns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            out = os.path.join(tmpdir, "export")
            _make_db(db)
            export_csvs(db_path=db, export_dir=out)
            import csv
            with open(os.path.join(out, "signals.csv"), newline="", encoding="utf-8") as f:
                headers = next(csv.reader(f))
            for col in ("call_id", "ticker", "direction", "confidence", "market_price"):
                self.assertIn(col, headers, f"Missing column: {col}")

    def test_runs_csv_has_expected_columns(self):
        """runs.csv contains the core schema columns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            out = os.path.join(tmpdir, "export")
            _make_db(db)
            export_csvs(db_path=db, export_dir=out)
            import csv
            with open(os.path.join(out, "runs.csv"), newline="", encoding="utf-8") as f:
                headers = next(csv.reader(f))
            for col in ("run_id", "timestamp", "markets_scanned", "signals_generated"):
                self.assertIn(col, headers, f"Missing column: {col}")

    def test_double_run_does_not_error(self):
        """Running export_csvs() twice overwrites cleanly without raising."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            out = os.path.join(tmpdir, "export")
            _make_db(db)
            export_csvs(db_path=db, export_dir=out)
            export_csvs(db_path=db, export_dir=out)  # second call must not raise

    def test_returns_correct_row_counts(self):
        """Return dict reflects actual row counts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            out = os.path.join(tmpdir, "export")
            _make_db(db, with_data=True)
            result = export_csvs(db_path=db, export_dir=out)
            self.assertEqual(result["signals"], 1)
            self.assertEqual(result["runs"],    1)


class TestEmptyDB(unittest.TestCase):

    def test_empty_db_produces_header_only_csvs(self):
        """An empty DB writes CSVs with headers but zero data rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "empty.db")
            out = os.path.join(tmpdir, "export")
            _make_db(db, with_data=False)
            result = export_csvs(db_path=db, export_dir=out)
            self.assertEqual(result["signals"], 0)
            self.assertEqual(result["runs"],    0)
            import csv
            with open(os.path.join(out, "signals.csv"), newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            # Header row present, no data rows
            self.assertEqual(len(rows), 1)

    def test_empty_db_csv_has_headers(self):
        """Even with zero rows, signals.csv has column headers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "empty.db")
            out = os.path.join(tmpdir, "export")
            _make_db(db, with_data=False)
            export_csvs(db_path=db, export_dir=out)
            import csv
            with open(os.path.join(out, "signals.csv"), newline="", encoding="utf-8") as f:
                headers = next(csv.reader(f))
            self.assertGreater(len(headers), 0)


class TestMissingDB(unittest.TestCase):

    def test_missing_db_returns_zeros(self):
        """A missing DB path returns {"signals": 0, "runs": 0} without raising."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db  = os.path.join(tmpdir, "does_not_exist.db")
            out = os.path.join(tmpdir, "export")
            result = export_csvs(db_path=db, export_dir=out)
            self.assertEqual(result, {"signals": 0, "runs": 0})

    def test_missing_db_does_not_raise(self):
        """export_csvs() never raises even when DB is absent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db  = os.path.join(tmpdir, "ghost.db")
            out = os.path.join(tmpdir, "export")
            try:
                export_csvs(db_path=db, export_dir=out)
            except Exception as exc:
                self.fail(f"export_csvs raised unexpectedly: {exc}")


if __name__ == "__main__":
    unittest.main()
