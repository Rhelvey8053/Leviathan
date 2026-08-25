"""
tests/test_weekly_code_audit.py — Offline tests for
scripts/weekly_code_audit.py's run_audit() (unattended-ops: graceful
timeout handling).

2026-08-24: the first-ever run hit an uncaught subprocess.TimeoutExpired
and crashed with a raw Python traceback instead of a clean failure. This
covers only the new graceful-handling behavior -- not the full live
claude --print invocation, which (like main.py's own live-API paths)
isn't unit tested.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import scripts.weekly_code_audit as wca


def test_run_audit_success(capsys):
    with patch.object(wca.subprocess, "run",
                       return_value=MagicMock(stdout="report body", stderr="", returncode=0)):
        code = wca.run_audit("claude", "prompt text", {})
    assert code == 0
    assert "done" in capsys.readouterr().out


def test_run_audit_claude_nonzero_exit(capsys):
    with patch.object(wca.subprocess, "run",
                       return_value=MagicMock(stdout="", stderr="oops", returncode=2)):
        code = wca.run_audit("claude", "prompt text", {})
    assert code == 2


def test_run_audit_timeout_is_graceful_not_a_crash(capsys):
    """The 2026-08-24 bug: TimeoutExpired must not propagate as a raw traceback."""
    with patch.object(wca.subprocess, "run",
                       side_effect=wca.subprocess.TimeoutExpired(cmd="claude", timeout=wca.TIMEOUT_SECONDS)):
        code = wca.run_audit("claude", "prompt text", {})
    assert code == 1
    captured = capsys.readouterr()
    assert "timed out" in captured.err
    assert str(wca.TIMEOUT_SECONDS) in captured.err


def test_timeout_seconds_has_real_margin():
    """Raised from 1800s after the first-ever run hit that wall exactly."""
    assert wca.TIMEOUT_SECONDS >= 1800
