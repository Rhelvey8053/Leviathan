"""
tests/test_send_editorial_selftest.py — Tests for scripts/send_editorial_selftest.py.

Covers the 2026-08-23 fix only: _flatten_editorial_vars() existed and was
tested since Phase 1 but was never actually called before the HTML reached
send_report(), so every var(--sp-3)/var(--sp-6) etc. custom-property spacing
value silently collapsed in the real received email (Gmail doesn't reliably
resolve CSS custom properties) despite rendering perfectly in a real browser
(Playwright screenshots, the standalone preview file). No live SMTP call --
send_report is mocked throughout.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scripts import send_editorial_selftest as sut

_NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def test_send_one_flattens_css_vars_before_send():
    html_with_vars = "<style>body{background:var(--paper); padding:var(--sp-3);}</style>"
    with patch.object(sut, "send_report") as mock_send:
        sut._send_one("daily", html_with_vars, {"report": {"email_to": "x@example.com"}}, _NOW, dry_run=False)
    sent_html = mock_send.call_args.kwargs["html_body"]
    assert "var(" not in sent_html
    assert "#FBFAF7" in sent_html  # --paper's literal value
    assert "24px" in sent_html     # --sp-3's literal value


def test_send_one_dry_run_reports_flattened_length_and_makes_no_send_call(capsys):
    html_with_vars = "<style>body{background:var(--paper);}</style>"
    expected_len = len(sut._flatten_editorial_vars(html_with_vars))
    with patch.object(sut, "send_report") as mock_send:
        sut._send_one("daily", html_with_vars, {"report": {"email_to": "x@example.com"}}, _NOW, dry_run=True)
    mock_send.assert_not_called()
    # the previewed length must match what would actually be sent (flattened),
    # not the pre-flatten length -- a dry-run that lies about what it's
    # previewing defeats the point of having one
    assert f"HTML body length: {expected_len} chars" in capsys.readouterr().out
