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
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import scripts.weekly_code_audit as wca


def test_run_audit_success(capsys, tmp_path):
    def _fake_run(*a, **k):
        # Simulates the CLI's own Write/Edit call happening during the
        # subprocess -- must be written AFTER run_audit() captures its
        # start_time, matching what a real successful run does.
        (tmp_path / "2026-08-30.md").write_text("# Weekly Audit", encoding="utf-8")
        return MagicMock(stdout="report body", stderr="", returncode=0)

    with patch.object(wca, "REPORT_DIR", tmp_path), \
         patch.object(wca.subprocess, "run", side_effect=_fake_run):
        code = wca.run_audit("claude", "prompt text", {})
    assert code == 0
    assert "done" in capsys.readouterr().out


def test_run_audit_claude_nonzero_exit(capsys):
    with patch.object(wca.subprocess, "run",
                       return_value=MagicMock(stdout="", stderr="oops", returncode=2)):
        code = wca.run_audit("claude", "prompt text", {})
    assert code == 2


def test_run_audit_exit_zero_but_no_report_written_is_a_failure(capsys, tmp_path):
    """
    Real bug found 2026-08-30: a run exited 0 with garbled, leaked-looking
    agent-reasoning text as its captured stdout instead of the expected
    structured report, and never wrote reports/code_audits/<date>.md at
    all -- likely a resource-contention symptom from several concurrent
    claude CLI invocations on the same machine that day. Exit code 0 alone
    must not be trusted; the actual report file has to exist and be fresh.
    """
    with patch.object(wca, "REPORT_DIR", tmp_path), \
         patch.object(wca.subprocess, "run",
                       return_value=MagicMock(stdout="garbled agent reasoning, not a report", stderr="", returncode=0)):
        code = wca.run_audit("claude", "prompt text", {})
    assert code == 2
    assert "no new report file" in capsys.readouterr().err


def test_run_audit_exit_zero_with_stale_report_is_a_failure(capsys, tmp_path):
    """A report file existing from a PRIOR run doesn't count -- it has to
    be newer than when this run started, not just present."""
    stale_report = tmp_path / "2026-08-23.md"
    stale_report.write_text("# old report", encoding="utf-8")
    old_time = time.time() - 3600
    import os as _os
    _os.utime(stale_report, (old_time, old_time))
    with patch.object(wca, "REPORT_DIR", tmp_path), \
         patch.object(wca.subprocess, "run",
                       return_value=MagicMock(stdout="garbled", stderr="", returncode=0)):
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
