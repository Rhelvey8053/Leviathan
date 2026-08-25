"""
scripts/weekly_code_audit.py - Unattended weekly system-and-code health check.

Runs Claude Code headlessly (claude --print) against a fixed prompt
(scripts/weekly_code_audit_prompt.md): investigate Task Scheduler health,
DB integrity, disk/log sanity, and recent commits' correctness -- report
only, never edits code, never touches Task Scheduler or a live process,
never commits. Sibling to scripts/weekly_audit.py (that one drafts
calibration fixes to core/scanner.py under its own strict rules; this one
is pure read-only investigation, no draft-a-fix step at all, so its tool
permissions are correspondingly tighter -- Edit scoped only to this run's
report file, nowhere else).

Scheduled via Windows Task Scheduler -- see
scripts/setup_weekly_code_audit_scheduler.ps1. Output also captured to
logs/weekly_code_audit.log for debugging a failed run.

2026-08-24: the first-ever run (2026-08-23, the first Sunday after this
task was registered) hit an uncaught subprocess.TimeoutExpired at the
1800s wall, crashing with a raw Python traceback instead of a clean
failure message -- caught only because daily_digest.py's weekly-log-tail
section surfaced the raw traceback the next day. Root cause: with no
prior report in reports/code_audits/, the prompt's "since the last
audit, else HEAD~10" bound only applied to the diff --stat step -- the
next bullet's "real read every diff since the last audit" step had no
such fallback, so on a from-scratch run it was effectively unbounded
against this project's full history (412 commits at the time). Fixed in
weekly_code_audit_prompt.md (bounded to the last 10 commits on a
from-scratch run, matching the already-bounded stat step) and here
(TIMEOUT_SECONDS raised for real margin on top of that fix, and the
subprocess call now fails gracefully instead of crashing raw).

That fix's own live verification run (2026-08-24) completed within
budget but STILL produced no report -- a separate, sneakier bug:
Write(reports/code_audits/*.md) in ALLOWED_TOOLS isn't matched by the
permission system's file-write checks at all (confirmed via the CLI's
own stderr: "Edit rules cover all file-editing tools", including Write),
so the one Write call this run ever needed was silently denied the
entire time. Exit code was still 0 -- looks like success unless someone
actually checks reports/code_audits/ for a new file. Fixed by expressing
the same scope as Edit(reports/code_audits/*.md) instead (and dropping
the blanket "Edit" that used to sit in DISALLOWED_TOOLS, since it would
have shadowed the new scoped allow).
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROMPT_FILE = ROOT / "scripts" / "weekly_code_audit_prompt.md"

# Report-only: no source-file edits, only the one report file this run
# writes. 2026-08-24: the first successful (non-timeout) live run still
# produced no report -- `Write(reports/code_audits/*.md)` isn't matched by
# the permission system's file checks at all; per the CLI's own error,
# "Edit rules cover all file-editing tools" including Write, so the path
# scope has to be expressed as Edit(...) even though the model calls the
# Write tool to create the new file. Matches weekly_audit.py's own
# already-working pattern (Edit(core/scanner.py) Edit(tests/test_scanner.py)),
# which is how this should have been written from the start.
ALLOWED_TOOLS = (
    "Read Grep Glob "
    "Edit(reports/code_audits/*.md) "
    "Bash(git status) Bash(git log*) Bash(git diff*) Bash(git show*) "
    "Bash(py -m pytest*) Bash(py -c*) "
    "PowerShell(Get-ScheduledTask*) PowerShell(Get-Process*) "
    "PowerShell(Get-WinEvent*) PowerShell(Get-PSDrive*) "
    "PowerShell(Get-CimInstance*) PowerShell(Get-ChildItem*) "
    "PowerShell(Get-Content*)"
)
# NotebookEdit stays blocked; the blanket "Edit" that used to sit here was
# removed -- it would have shadowed the scoped Edit(reports/code_audits/*.md)
# allow above (a disallow wins over an allow on the same tool). Deny-by-
# default already keeps Edit off every other path with no explicit rule.
DISALLOWED_TOOLS = (
    "NotebookEdit "
    "Bash(git commit*) Bash(git push*) Bash(git add*) "
    "PowerShell(Set-ScheduledTask*) PowerShell(Register-ScheduledTask*) "
    "PowerShell(Unregister-ScheduledTask*) PowerShell(Start-ScheduledTask*) "
    "PowerShell(Stop-ScheduledTask*) PowerShell(Stop-Process*) "
    "PowerShell(Start-Process*)"
)

# Was 1800 (30 min); raised for real margin on top of the prompt-scope
# fix (weekly_code_audit_prompt.md's from-scratch-run diff-read is now
# bounded) -- this is an unattended Sunday job nobody is waiting on, so a
# longer ceiling costs nothing.
TIMEOUT_SECONDS = 3600


def run_audit(claude_path: str, prompt: str, clean_env: dict) -> int:
    """Runs the claude --print audit. Returns the process exit code."""
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
        print(f"[weekly_code_audit] claude --print timed out after {TIMEOUT_SECONDS}s -- "
              "no report was written to reports/code_audits/. Not necessarily a hang; "
              "see this module's docstring for the 2026-08-24 first-run scope issue "
              "this was raised to cover. If this recurs, check whether the audit is "
              "genuinely running out of budget partway through the checklist in "
              "weekly_code_audit_prompt.md.", file=sys.stderr)
        return 1

    print(result.stdout)
    if result.stderr:
        print("[weekly_code_audit] stderr:", result.stderr, file=sys.stderr)
    if result.returncode != 0:
        print(f"[weekly_code_audit] claude exited {result.returncode}", file=sys.stderr)
        return result.returncode
    print("[weekly_code_audit] done")
    return 0


def main():
    claude_path = shutil.which("claude")
    if not claude_path:
        print("[weekly_code_audit] claude CLI not found in PATH", file=sys.stderr)
        sys.exit(1)

    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    # Same free CLI/Pro-auth path main.py's scorer.py and weekly_audit.py
    # use -- excluding ANTHROPIC_API_KEY keeps this off metered billing.
    clean_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    print(f"\n[weekly_code_audit] {datetime.now(timezone.utc).isoformat()}")
    # Prompt via stdin, not a positional CLI argument -- same Windows
    # command-line-length fix weekly_audit.py already needed.
    sys.exit(run_audit(claude_path, prompt, clean_env))


if __name__ == "__main__":
    main()
