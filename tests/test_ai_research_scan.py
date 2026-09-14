"""
tests/test_ai_research_scan.py — Offline tests for scripts/ai_research_scan.py's
report-emailing path (added 2026-09-14: user asked for a dedicated email per
scan, not just the WEEKLY_LOGS tail excerpt buried in the daily digest).

No live email (core.report.send_report is patched throughout), no live
claude CLI subprocess -- these tests exercise compose_email/email_report/
load_config directly against a tmp_path report file, never run_scan().
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import scripts.ai_research_scan as ars

CONFIG = {"report": {"email_to": "owner@example.com"}}


# ─── compose_email ──────────────────────────────────────────────────────────

def test_compose_email_subject_includes_date():
    body, subject = ars.compose_email("some report text", "2026-09-14")
    assert "2026-09-14" in subject
    assert "AI Research Scan" in subject


def test_compose_email_body_is_report_verbatim():
    """The report is already written for a human to read start to finish --
    sent as-is, not re-summarized a second time."""
    text = "# Report\n\nSome **markdown** content with an arrow → here."
    body, _ = ars.compose_email(text, "2026-09-14")
    assert body == text


# ─── email_report: sends via core.report.send_report ───────────────────────

def test_email_report_sends_with_correct_subject(tmp_path):
    report_path = tmp_path / "2026-09-14.md"
    report_path.write_text("# Report\n\nFindings here.", encoding="utf-8")

    with patch.object(ars, "send_report") as mock_send:
        ok = ars.email_report(report_path, CONFIG, dry_run=False)

    assert ok is True
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert kwargs["subject_override"] == "Leviathan — AI Research Scan — 2026-09-14"
    assert kwargs["config"] == CONFIG
    assert kwargs["signals"] == []
    assert kwargs["whale_flags"] == 0


def test_email_report_date_comes_from_filename_not_file_mtime(tmp_path):
    """Uses report_path.stem for the date, not today's real date or the
    file's mtime -- so a rerun/backfill for an older date still emails
    with that date's own subject, not today's."""
    report_path = tmp_path / "2026-01-01.md"
    report_path.write_text("old report", encoding="utf-8")

    with patch.object(ars, "send_report") as mock_send:
        ars.email_report(report_path, CONFIG, dry_run=False)

    _, kwargs = mock_send.call_args
    assert "2026-01-01" in kwargs["subject_override"]


# ─── email_report: dry-run ──────────────────────────────────────────────────

def test_email_report_dry_run_sends_nothing(tmp_path, capsys):
    report_path = tmp_path / "2026-09-14.md"
    report_path.write_text("# Report\n\nFindings here.", encoding="utf-8")

    with patch.object(ars, "send_report") as mock_send:
        ok = ars.email_report(report_path, CONFIG, dry_run=True)

    mock_send.assert_not_called()
    assert ok is True
    captured = capsys.readouterr()
    assert "AI Research Scan" in captured.out
    assert "Findings here." in captured.out


def test_email_report_dry_run_handles_non_ascii_content(tmp_path):
    """Regression: found live 2026-09-14 -- print() on Windows defaults to
    the cp1252 console codepage and raised UnicodeEncodeError on plain
    report content (an arrow character), before main() started reconfiguring
    stdout/stderr to utf-8/errors=replace. email_report() itself doesn't
    reconfigure streams (main() does, once, for the whole process) --
    this test only guards that email_report's own print calls don't
    introduce a NEW non-ASCII string that main()'s fix wouldn't already
    cover, by asserting the call succeeds against the same character class
    that caused the original crash."""
    report_path = tmp_path / "2026-09-14.md"
    report_path.write_text("Findings → next steps — done.", encoding="utf-8")

    with patch.object(ars, "send_report") as mock_send:
        ok = ars.email_report(report_path, CONFIG, dry_run=True)

    mock_send.assert_not_called()
    assert ok is True


# ─── email_report: send failure ─────────────────────────────────────────────

def test_email_report_send_failure_returns_false_not_raises(tmp_path):
    report_path = tmp_path / "2026-09-14.md"
    report_path.write_text("# Report", encoding="utf-8")

    with patch.object(ars, "send_report", side_effect=RuntimeError("SMTP down")):
        ok = ars.email_report(report_path, CONFIG, dry_run=False)

    assert ok is False


# ─── load_config ─────────────────────────────────────────────────────────────

def test_load_config_reads_config_json(tmp_path, monkeypatch):
    fake_root = tmp_path
    (fake_root / "config.json").write_text(
        json.dumps({"report": {"email_to": "x@example.com"}}), encoding="utf-8",
    )
    monkeypatch.setattr(ars, "ROOT", fake_root)
    cfg = ars.load_config()
    assert cfg["report"]["email_to"] == "x@example.com"


def test_load_config_falls_back_to_example(tmp_path, monkeypatch):
    fake_root = tmp_path
    (fake_root / "config.example.json").write_text(
        json.dumps({"report": {"email_to": "example@example.com"}}), encoding="utf-8",
    )
    monkeypatch.setattr(ars, "ROOT", fake_root)
    cfg = ars.load_config()
    assert cfg["report"]["email_to"] == "example@example.com"
