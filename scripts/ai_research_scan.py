"""
scripts/ai_research_scan.py - Unattended AI/GitHub research scan, twice
weekly (Wednesday + Sunday, see scripts/setup_ai_research_scan_scheduler.ps1
-- 2026-09-14: bumped from Sunday-only after a user PM-review conversation
concluded weekly was too slow but daily would mostly restate "nothing new"
and burn Pro/CLI usage for little incremental signal).

Runs Claude Code headlessly (claude --print) against a fixed prompt
(scripts/ai_research_scan_prompt.md): research GitHub repos, Anthropic/
Claude and other model updates, and agent techniques/patterns that could
plausibly improve this project, filtered for actual relevance rather than
general AI news. Report-only -- restricted via --allowedTools/
--disallowedTools to Read/Grep/Glob for local context, WebSearch/WebFetch
for research, and Edit(reports/ai_research/*) for the one output file
(NOT Write(path) -- the CLI's own file-permission checks only match
Edit(path) rules, Edit covering all file-editing tools including Write;
the first live run hit this the hard way, see git history). No other Edit,
git limited to read-only commands, never touches data/leviathan.db, never
runs anything that places or implies a real trade, never files anything to
backlog/backlog.json itself (findings land in the report; a human decides
what's worth adding to the backlog).

Scheduled via Windows Task Scheduler -- see
scripts/setup_ai_research_scan_scheduler.ps1. Output also captured to
logs/ai_research_scan.log for debugging a failed run, and surfaced (as a
tail excerpt) by scripts/daily_digest.py's WEEKLY_LOGS section the same
way weekly_audit.log / weekly_code_audit.log already are.

2026-09-14: also emails the written report directly (user asked for a
dedicated email rather than only the WEEKLY_LOGS tail excerpt, which
truncates and is buried inside the daily digest). Reuses core.report.
send_report exactly like gate_notifier.py/heartbeat_check.py/
automation_health_check.py do -- same plain-text-body, subject_override
call shape, same try/except-and-log-not-crash on send failure. No new
state file: unlike gate_notifier's fire-once dedup, this just emails
whatever reports/ai_research/<today>.md contains after each run: the
scan is scheduled at most twice a week, so a same-day rerun (manual,
during dev) double-emailing is an acceptable, rare cost against the
complexity of tracking "already sent today" state for it.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.report import send_report

PROMPT_FILE = ROOT / "scripts" / "ai_research_scan_prompt.md"
REPORTS_DIR = ROOT / "reports" / "ai_research"

ALLOWED_TOOLS = (
    "Read Grep Glob WebSearch WebFetch "
    # Edit(path), not Write(path) -- the CLI's own permission-check error on
    # the 2026-09-14 first live run: "Permission allow rule (--allowed-
    # tools): Write(reports/ai_research/*) is not matched by file permission
    # checks -- only Edit(path) rules are." Edit(path) rules cover all
    # file-editing tools, Write included. That run's report never got saved
    # to disk because of this -- had to be recovered from the CLI's own
    # stdout/log and written by hand.
    "Edit(reports/ai_research/*) "
    "Bash(git status) Bash(git log*) Bash(git diff*) Bash(git show*)"
)
DISALLOWED_TOOLS = (
    "Edit NotebookEdit "
    "Bash(git commit*) Bash(git push*) Bash(git add*) "
    "Bash(pip install*) Bash(npm install*)"
)

TIMEOUT_SECONDS = 2400  # web research (multiple searches/fetches) runs longer than the local-only audits


def run_scan(claude_path: str, prompt: str, clean_env: dict) -> int:
    """Runs the claude --print research scan. Returns the process exit code."""
    try:
        result = subprocess.run(
            [
                claude_path, "--print",
                "--allowedTools", ALLOWED_TOOLS,
                "--disallowedTools", DISALLOWED_TOOLS,
                "--permission-mode", "dontAsk",
            ],
            input=prompt,
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS,
            env=clean_env, cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        # Mirrors weekly_audit.py's/weekly_code_audit.py's own handling of
        # this exact case -- a raw traceback on timeout is a worse failure
        # mode than a clean one-line message, for a run nobody is watching.
        print(f"[ai_research_scan] claude --print timed out after {TIMEOUT_SECONDS}s -- "
              "no report was written this run.", file=sys.stderr)
        return 1

    print(result.stdout)
    if result.stderr:
        print("[ai_research_scan] stderr:", result.stderr, file=sys.stderr)
    if result.returncode != 0:
        print(f"[ai_research_scan] claude exited {result.returncode}", file=sys.stderr)
        return result.returncode
    print("[ai_research_scan] done")
    return 0


def load_config() -> dict:
    cfg_path = ROOT / "config.json"
    if not cfg_path.exists():
        cfg_path = ROOT / "config.example.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def compose_email(report_text: str, date_str: str) -> tuple[str, str]:
    """Returns (body, subject) for today's report email. The report is
    already written for a human to read start to finish (see
    ai_research_scan_prompt.md's own structure) -- send it as-is rather
    than re-summarizing it a second time."""
    subject = f"Leviathan — AI Research Scan — {date_str}"
    return report_text, subject


def email_report(report_path: Path, config: dict, dry_run: bool = False) -> bool:
    """Emails the report at report_path. Returns True if sent (or if
    dry_run printed successfully), False on any failure -- never raises,
    matching gate_notifier/heartbeat_check/automation_health_check's own
    send-failure handling (log it, don't crash a run that otherwise
    succeeded)."""
    date_str = report_path.stem
    report_text = report_path.read_text(encoding="utf-8")
    body, subject = compose_email(report_text, date_str)

    if dry_run:
        print(subject)
        print()
        print(body)
        return True

    try:
        send_report(body, signals=[], whale_flags=0, config=config, subject_override=subject)
    except Exception as e:
        print(f"[ai_research_scan] email send FAILED (report was still written to disk): {e}",
              file=sys.stderr)
        return False

    # relative_to(ROOT) raises ValueError for a path outside ROOT (e.g. a
    # tmp_path in tests, or any future caller passing an arbitrary path) --
    # found by this function's own tests. Falls back to the absolute path
    # rather than let a cosmetic log line crash an otherwise-successful send.
    try:
        shown_path = report_path.relative_to(ROOT)
    except ValueError:
        shown_path = report_path
    print(f"[ai_research_scan] emailed report ({shown_path})")
    return True


def main():
    # Windows' console codepage (cp1252) can't encode characters the
    # research report/CLI output routinely contain (em dashes, arrows --
    # found 2026-09-14 testing email_report's own dry-run print, which
    # crashed with UnicodeEncodeError on '→'). This also silently
    # protected run_scan()'s pre-existing print(result.stdout) below, which
    # had the identical exposure and just hadn't been hit by an unlucky
    # character yet. errors="replace" over a stricter reconfigure: a
    # mangled character in a log/console is recoverable, a crashed run
    # that skips emailing an otherwise-good report is not.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Leviathan AI/GitHub research scan")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the email subject+body instead of sending")
    args = parser.parse_args()

    claude_path = shutil.which("claude")
    if not claude_path:
        print("[ai_research_scan] claude CLI not found in PATH", file=sys.stderr)
        sys.exit(1)

    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    # Same free CLI/Pro-auth path main.py's scorer.py and the other weekly
    # audits use -- excluding ANTHROPIC_API_KEY keeps this off metered
    # billing (see project memory: no Anthropic console API billing for
    # Leviathan, Pro subscription/CLI backend only).
    clean_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    print(f"\n[ai_research_scan] {datetime.now(timezone.utc).isoformat()}")
    # Prompt via stdin, not a positional CLI argument -- same reason as
    # weekly_audit.py: this prompt is long enough to risk Windows' command-
    # line length limit.
    exit_code = run_scan(claude_path, prompt, clean_env)

    # Email whatever today's report contains, independent of exit_code --
    # a report can exist even after a non-fatal issue is logged elsewhere,
    # and existence (not exit_code) is the actual signal that there's
    # something to send. No file for today just means no email, silently.
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"{today_str}.md"
    if report_path.exists():
        config = load_config()
        if not email_report(report_path, config, dry_run=args.dry_run):
            exit_code = exit_code or 1
    else:
        print(f"[ai_research_scan] no report at {report_path.relative_to(ROOT)} -- nothing to email")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
