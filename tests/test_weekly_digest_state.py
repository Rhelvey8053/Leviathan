"""
Offline tests for main.py's _save_weekly_digest_state() -- the persisted
marker that lets scripts/daily_digest.py confirm the Sunday weekly digest
actually completed (see backlog: health-check-is-presence-not-outcome).

No network calls, no Claude CLI, no DB access -- pure file I/O against
tmp_path.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import main


def test_save_weekly_digest_state_ok(tmp_path):
    state_path = tmp_path / "weekly_digest_state.json"
    with patch.object(main, "WEEKLY_DIGEST_STATE_PATH", str(state_path)):
        main._save_weekly_digest_state(
            datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc), ok=True, note=None)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state == {"date": "2026-09-13", "ok": True, "note": None}


def test_save_weekly_digest_state_failure(tmp_path):
    state_path = tmp_path / "weekly_digest_state.json"
    with patch.object(main, "WEEKLY_DIGEST_STATE_PATH", str(state_path)):
        main._save_weekly_digest_state(
            datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc), ok=False, note="SSLError: EOF")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["ok"] is False
    assert state["note"] == "SSLError: EOF"


def test_save_weekly_digest_state_creates_parent_dir(tmp_path):
    state_path = tmp_path / "nested" / "weekly_digest_state.json"
    with patch.object(main, "WEEKLY_DIGEST_STATE_PATH", str(state_path)):
        main._save_weekly_digest_state(
            datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc), ok=True, note=None)
    assert state_path.exists()


def test_save_weekly_digest_state_never_raises_on_bad_path():
    """A write failure here must stay non-fatal -- main.py is deep into
    step 6/8 by the time this runs and shouldn't crash the whole pipeline
    over a digest-state write."""
    with patch.object(main, "WEEKLY_DIGEST_STATE_PATH", "\0invalid"):
        main._save_weekly_digest_state(
            datetime(2026, 9, 13, 12, 0, 0, tzinfo=timezone.utc), ok=True, note=None)
