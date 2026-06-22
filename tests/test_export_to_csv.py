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

from core.export_to_csv import export_csvs


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


class TestNullHandling(unittest.TestCase):

    def _make_db_with_nulls(self, path: str) -> None:
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
        # Row with NULL result (the key Goal 3d case)
        conn.execute("""
            INSERT INTO signals VALUES
            ('null1','2026-06-20T00:00:00Z','KXTEST-001','Test market',
             0.55,0.70,0.15,'YES','MED',NULL,NULL)
        """)
        # Row with explicit LOSS result
        conn.execute("""
            INSERT INTO signals VALUES
            ('loss1','2026-06-20T00:00:00Z','KXTEST-002','Test market 2',
             0.40,0.25,0.15,'NO','HIGH',NULL,'LOSS')
        """)
        conn.execute("""
            INSERT INTO runs VALUES
            ('run1','2026-06-20T00:00:00Z',100,2,'claude-sonnet-4-6')
        """)
        conn.commit()
        conn.close()

    def test_null_result_becomes_empty_string_not_nan(self):
        """NULL result column must export as '' not 'NaN' or 'None'."""
        import csv
        with tempfile.TemporaryDirectory() as tmpdir:
            db  = os.path.join(tmpdir, "nulls.db")
            out = os.path.join(tmpdir, "export")
            self._make_db_with_nulls(db)
            export_csvs(db_path=db, export_dir=out)
            with open(os.path.join(out, "signals.csv"), newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                result_vals = [row["result"] for row in reader]
            self.assertNotIn("NaN",  result_vals, "NaN found in result column")
            self.assertNotIn("None", result_vals, "None found in result column")
            self.assertIn("",   result_vals, "Empty string expected for NULL result")
            self.assertIn("LOSS", result_vals, "LOSS row should still be present")

    def test_numeric_columns_not_converted(self):
        """Numeric columns (market_price, edge) must remain numeric, not become ''."""
        import csv
        with tempfile.TemporaryDirectory() as tmpdir:
            db  = os.path.join(tmpdir, "nulls.db")
            out = os.path.join(tmpdir, "export")
            self._make_db_with_nulls(db)
            export_csvs(db_path=db, export_dir=out)
            with open(os.path.join(out, "signals.csv"), newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            for row in rows:
                self.assertNotEqual(row["market_price"], "",
                                    "market_price should not be blanked out")
                val = float(row["market_price"])
                self.assertGreater(val, 0)


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


def _make_full_db(path: str) -> None:
    """Create a DB with the full signals schema: whitelist cols + pipeline plumbing cols."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            call_id TEXT PRIMARY KEY,
            timestamp TEXT,
            ticker TEXT,
            title TEXT,
            source TEXT,
            direction TEXT,
            confidence TEXT,
            flag_path TEXT,
            time_horizon TEXT,
            market_price REAL,
            edge REAL,
            net_edge REAL,
            base_rate REAL,
            result TEXT,
            pnl_if_traded REAL,
            leviathan_score INTEGER,
            close_time TEXT,
            sig_edge REAL,
            sig_drift REAL,
            sig_br_none REAL,
            watchlist_signal INTEGER,
            whale_detected INTEGER,
            heuristic_label TEXT,
            short_horizon INTEGER,
            run_id TEXT,
            from_signal TEXT,
            fill_count INTEGER,
            fill_fee REAL,
            contract_type TEXT,
            segment TEXT,
            outcome TEXT,
            our_estimate REAL,
            direction_aligned INTEGER,
            entry_price REAL,
            signal_call_id TEXT,
            logged_under TEXT,
            resolution_date TEXT,
            whale_direction TEXT,
            heuristic_direction TEXT
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, timestamp TEXT, markets_scanned INTEGER,
            signals_generated INTEGER, model_used TEXT
        );
    """)
    conn.execute("""
        INSERT INTO signals (
            call_id, timestamp, ticker, title, source, direction, confidence,
            flag_path, time_horizon, market_price, edge, net_edge, base_rate,
            result, pnl_if_traded, leviathan_score, close_time,
            sig_edge, sig_drift, sig_br_none, watchlist_signal, whale_detected,
            heuristic_label, short_horizon,
            run_id, from_signal, fill_count, fill_fee, contract_type, segment,
            outcome, our_estimate, direction_aligned, entry_price,
            signal_call_id, logged_under, resolution_date, whale_direction,
            heuristic_direction
        ) VALUES (
            'full1', '2026-06-19T10:00:00Z', 'KXTEST-001', 'Full test market',
            'Kalshi', 'YES', 'HIGH',
            NULL, 'WEEKLY', 0.60, 0.12, 0.10, 0.50,
            'WIN', 0.12, 72, '2026-06-25T00:00:00Z',
            0.05, 0.02, 0.30, 1, 1,
            'momentum', 0,
            'run1', NULL, 2, 0.01, 'binary', 'politics',
            'WIN', 0.55, 1, 0.61,
            'full1', 'full1', '2026-06-25', 'YES', 'UP'
        )
    """)
    conn.execute("""
        INSERT INTO runs VALUES ('run1','2026-06-19T10:00:00Z',50,1,'claude-sonnet-4-6')
    """)
    conn.commit()
    conn.close()


_DROPPED_COLS = [
    "run_id", "from_signal", "fill_count", "fill_fee", "contract_type",
    "segment", "outcome", "our_estimate", "direction_aligned", "entry_price",
    "signal_call_id", "logged_under", "resolution_date", "whale_direction",
    "heuristic_direction",
]

_COMPUTED_COLS_EXPECTED = [
    "is_win", "is_resolved", "lv_band", "pnl_scaled",
    "confidence_rank", "horizon_rank", "date",
]


class TestWhitelistExport(unittest.TestCase):

    def test_only_whitelisted_columns_in_output(self):
        """signals.csv must contain only columns from WHITELIST."""
        import csv
        from core.export_to_csv import WHITELIST
        with tempfile.TemporaryDirectory() as tmpdir:
            db  = os.path.join(tmpdir, "full.db")
            out = os.path.join(tmpdir, "export")
            _make_full_db(db)
            export_csvs(db_path=db, export_dir=out)
            with open(os.path.join(out, "signals.csv"), newline="", encoding="utf-8") as f:
                headers = next(csv.reader(f))
            for col in headers:
                self.assertIn(col, WHITELIST, f"Non-whitelist column in output: {col}")

    def test_dropped_columns_absent(self):
        """Pipeline plumbing columns must not appear in signals.csv."""
        import csv
        with tempfile.TemporaryDirectory() as tmpdir:
            db  = os.path.join(tmpdir, "full.db")
            out = os.path.join(tmpdir, "export")
            _make_full_db(db)
            export_csvs(db_path=db, export_dir=out)
            with open(os.path.join(out, "signals.csv"), newline="", encoding="utf-8") as f:
                headers = next(csv.reader(f))
            for col in _DROPPED_COLS:
                self.assertNotIn(col, headers, f"Dropped column still present: {col}")

    def test_computed_columns_present(self):
        """All 7 computed columns must appear in signals.csv."""
        import csv
        with tempfile.TemporaryDirectory() as tmpdir:
            db  = os.path.join(tmpdir, "full.db")
            out = os.path.join(tmpdir, "export")
            _make_full_db(db)
            export_csvs(db_path=db, export_dir=out)
            with open(os.path.join(out, "signals.csv"), newline="", encoding="utf-8") as f:
                headers = next(csv.reader(f))
            for col in _COMPUTED_COLS_EXPECTED:
                self.assertIn(col, headers, f"Computed column missing: {col}")

    def test_computed_column_values_correct(self):
        """Computed column values are derived correctly from the DB row."""
        import csv
        with tempfile.TemporaryDirectory() as tmpdir:
            db  = os.path.join(tmpdir, "full.db")
            out = os.path.join(tmpdir, "export")
            _make_full_db(db)
            export_csvs(db_path=db, export_dir=out)
            with open(os.path.join(out, "signals.csv"), newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["is_resolved"], "1",  "result=WIN should be resolved")
        self.assertEqual(row["is_win"],      "1",  "result=WIN should be a win")
        self.assertEqual(row["lv_band"],     "A",  "leviathan_score=72 → A band (>=70)")
        self.assertEqual(row["confidence_rank"], "0", "HIGH confidence → rank 0")
        self.assertEqual(row["horizon_rank"],    "1", "WEEKLY horizon → rank 1")
        self.assertEqual(row["date"],        "2026-06-19", "date extracted from timestamp")
        self.assertEqual(row["pnl_scaled"],  "1.2",        "0.12 * 10 = 1.2")

    def test_double_run_does_not_error(self):
        """Running export_csvs() twice with full schema overwrites cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db  = os.path.join(tmpdir, "full.db")
            out = os.path.join(tmpdir, "export")
            _make_full_db(db)
            export_csvs(db_path=db, export_dir=out)
            export_csvs(db_path=db, export_dir=out)


if __name__ == "__main__":
    unittest.main()
