"""
tests/test_weekly_audit_runner.py — Offline tests for
scripts/weekly_audit.py's run_audit() (unattended-ops: graceful timeout
handling, mirroring the same fix made in weekly_code_audit.py after its
2026-08-24 uncaught-TimeoutExpired crash).

Covers only the new graceful-handling behavior -- not the full live
claude --print invocation.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import scripts.weekly_audit as wa


def test_run_audit_success(capsys):
    with patch.object(wa.subprocess, "run",
                       return_value=MagicMock(stdout="report body", stderr="", returncode=0)):
        code = wa.run_audit("claude", "prompt text", {})
    assert code == 0
    assert "done" in capsys.readouterr().out


def test_run_audit_claude_nonzero_exit(capsys):
    with patch.object(wa.subprocess, "run",
                       return_value=MagicMock(stdout="", stderr="oops", returncode=2)):
        code = wa.run_audit("claude", "prompt text", {})
    assert code == 2


def test_run_audit_timeout_is_graceful_not_a_crash(capsys):
    with patch.object(wa.subprocess, "run",
                       side_effect=wa.subprocess.TimeoutExpired(cmd="claude", timeout=wa.TIMEOUT_SECONDS)):
        code = wa.run_audit("claude", "prompt text", {})
    assert code == 1
    captured = capsys.readouterr()
    assert "timed out" in captured.err
