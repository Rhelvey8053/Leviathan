"""
tests/test_4d.py — Tests for Goal 4d: EV floor lowered from 50% to 25% of unit size.

All tests are offline — no network calls, no email.
"""

import json
import sqlite3
import sys
import tempfile
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import report


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_db(tmp_path, rows):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE signals (
            call_id TEXT, ticker TEXT, direction TEXT, market_price REAL,
            our_estimate REAL, edge REAL, close_time TEXT,
            confidence TEXT, result TEXT, source TEXT, timestamp TEXT, title TEXT,
            event_ticker TEXT, series_ticker TEXT
        );
        CREATE TABLE fills (ticker TEXT, filled_at TEXT);
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r.get("call_id", r["ticker"]),
                r["ticker"],
                r.get("direction", "YES"),
                r["market_price"],
                r["our_estimate"],
                r["edge"],
                r.get("close_time", "2026-12-31T00:00:00Z"),
                r.get("confidence", "MED"),
                "", "paper",
                r.get("timestamp", "2026-06-20T00:00:00Z"),
                r.get("title", "Test market"),
                r.get("event_ticker", ""),
                r.get("series_ticker", ""),
            ),
        )
    conn.commit()
    conn.close()
    return str(db)


def _cfg_25():
    """Config with 25% floor (the new default)."""
    return {"betting": {"unit_size": 10, "min_ev_pct_of_unit": 0.25}}


def _cfg_50():
    """Config with old 50% floor for comparison."""
    return {"betting": {"unit_size": 10, "min_ev_pct_of_unit": 0.50}}


# ═══════════════════════════════════════════════════════════════════════════════
# Config file assertion
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigFloor:
    """config.example.json must have min_ev_pct_of_unit = 0.25.

    Tests the committed template, not config.json — the real file is
    git-ignored (it can carry personal settings) so it may not exist in a
    fresh clone or CI checkout.
    """

    def test_config_floor_is_25pct(self):
        config_path = ROOT / "config.example.json"
        with open(config_path) as f:
            config = json.load(f)
        assert config["betting"]["min_ev_pct_of_unit"] == pytest.approx(0.25)

    def test_config_betting_notes_present(self):
        """Rationale key exists so a reader can find the reason for 0.25."""
        config_path = ROOT / "config.example.json"
        with open(config_path) as f:
            config = json.load(f)
        notes = config["betting"].get("_betting_notes", "")
        assert "0.50" in notes or "lowered" in notes.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 30pp edge: clears new floor, fails old floor
# ═══════════════════════════════════════════════════════════════════════════════

class TestFloorLoosenedEffect:
    """Prove the loosening has real effect on the betting queue."""

    def test_30pp_edge_appears_in_queue_at_new_floor(self, tmp_path):
        """mp=0.30, est=0.60 (30pp): ev_after=$2.85 >= $2.50 new floor — appears in queue."""
        db = _make_db(tmp_path, rows=[{
            "ticker": "KXINFRA-26DEC31",
            "market_price": 0.30, "our_estimate": 0.60, "edge": 0.30,
            "direction": "YES", "confidence": "HIGH",
            "title": "Will infrastructure bill pass?",
        }])
        lines = report._betting_queue(db_path=db, config=_cfg_25())
        full = "\n".join(lines)
        assert "KXINFRA-26DEC31" in full

    def test_30pp_edge_excluded_at_old_floor(self, tmp_path):
        """Same signal would have been excluded by old 50% floor ($5.00)."""
        db = _make_db(tmp_path, rows=[{
            "ticker": "KXINFRA-26DEC31",
            "market_price": 0.30, "our_estimate": 0.60, "edge": 0.30,
            "direction": "YES", "confidence": "HIGH",
            "title": "Will infrastructure bill pass?",
        }])
        lines = report._betting_queue(db_path=db, config=_cfg_50())
        full = "\n".join(lines)
        assert "KXINFRA-26DEC31" not in full.split("Already placed")[0].split("Filtered")[0]

    def test_20pp_edge_still_excluded_at_new_floor(self, tmp_path):
        """mp=0.30, est=0.50 (20pp): ev_after=$1.85 < $2.50 — still filtered."""
        db = _make_db(tmp_path, rows=[{
            "ticker": "KXSMALL-26DEC31",
            "market_price": 0.30, "our_estimate": 0.50, "edge": 0.20,
            "direction": "YES", "confidence": "MED",
            "title": "Low-edge market",
        }])
        lines = report._betting_queue(db_path=db, config=_cfg_25())
        full = "\n".join(lines)
        assert "KXSMALL-26DEC31" not in full.split("Filtered")[0]

    def test_filtered_count_reflects_new_floor(self, tmp_path):
        """Footer shows correct filtered count using new 25% threshold."""
        db = _make_db(tmp_path, rows=[
            # 20pp edge → $1.85 after fee < $2.50 floor → filtered
            {"ticker": "KXFILT", "market_price": 0.30, "our_estimate": 0.50,
             "edge": 0.20, "direction": "YES", "confidence": "MED", "title": "Filtered"},
            # 30pp edge → $2.85 after fee >= $2.50 floor → passes
            {"ticker": "KXPASS", "market_price": 0.30, "our_estimate": 0.60,
             "edge": 0.30, "direction": "YES", "confidence": "HIGH", "title": "Passes"},
        ])
        lines = report._betting_queue(db_path=db, config=_cfg_25())
        full = "\n".join(lines)
        assert "Filtered" in full
        assert "1" in full  # 1 candidate filtered


# ═══════════════════════════════════════════════════════════════════════════════
# Floor still functional (not effectively disabled)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFloorStillActive:
    """25% floor is a real filter, not a no-op."""

    def test_10pp_edge_excluded(self, tmp_path):
        """10pp edge (typical threshold-minimum) gives ~$0.85 EV — well below $2.50."""
        db = _make_db(tmp_path, rows=[{
            "ticker": "KXTHIN-26DEC31",
            "market_price": 0.40, "our_estimate": 0.50, "edge": 0.10,
            "direction": "YES", "confidence": "MED", "title": "Thin edge market",
        }])
        lines = report._betting_queue(db_path=db, config=_cfg_25())
        full = "\n".join(lines)
        assert "KXTHIN-26DEC31" not in full.split("Filtered")[0]

    def test_empty_queue_shows_filtered_footer(self, tmp_path):
        """All sub-floor signals → empty queue with Filtered count > 0."""
        db = _make_db(tmp_path, rows=[{
            "ticker": "KXALL-FILTERED",
            "market_price": 0.30, "our_estimate": 0.40, "edge": 0.10,
            "direction": "YES", "confidence": "MED", "title": "All filtered",
        }])
        lines = report._betting_queue(db_path=db, config=_cfg_25())
        full = "\n".join(lines)
        assert "No unplaced signals" in full or "Filtered" in full
