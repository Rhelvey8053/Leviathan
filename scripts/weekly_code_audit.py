"""
scripts/weekly_code_audit.py - Unattended weekly system-and-code health check.

Runs Claude Code headlessly (claude --print) against a fixed prompt
(scripts/weekly_code_audit_prompt.md): investigate Task Scheduler health,
DB integrity, disk/log sanity, and recent commits' correctness -- report
only, never edits code, never touches Task Scheduler or a live process,
never commits. Sibling to scripts/weekly_audit.py (that one drafts
calibration fixes to core/scanner.py under its own strict rules; this one
is pure read-only investigation, no draft-a-fix step at all, so its tool
permissions are correspondingly tighter -- no Edit at all, Write scoped
only to this run's report file).

Scheduled via Windows Task Scheduler -- see
scripts/setup_weekly_code_audit_scheduler.ps1. Output also captured to
logs/weekly_code_audit.log for debugging a failed run.
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROMPT_FILE = ROOT / "scripts" / "weekly_code_audit_prompt.md"

# Report-only: Edit is deliberately absent (unlike weekly_audit.py, which
# may draft a scoped calibration fix). Write is scoped to reports/code_audits/
# only, via the underlying permission system's path-glob support -- if that
# proves too strict in the first live run, the fallback the prompt would hit
# is a failed write reported as a finding, not a wrong-place write.
ALLOWED_TOOLS = (
    "Read Grep Glob "
    "Write(reports/code_audits/*.md) "
    "Bash(git status) Bash(git log*) Bash(git diff*) Bash(git show*) "
    "Bash(py -m pytest*) Bash(py -c*) "
    "PowerShell(Get-ScheduledTask*) PowerShell(Get-Process*) "
    "PowerShell(Get-WinEvent*) PowerShell(Get-PSDrive*) "
    "PowerShell(Get-CimInstance*) PowerShell(Get-ChildItem*) "
    "PowerShell(Get-Content*)"
)
DISALLOWED_TOOLS = (
    "NotebookEdit Edit "
    "Bash(git commit*) Bash(git push*) Bash(git add*) "
    "PowerShell(Set-ScheduledTask*) PowerShell(Register-ScheduledTask*) "
    "PowerShell(Unregister-ScheduledTask*) PowerShell(Start-ScheduledTask*) "
    "PowerShell(Stop-ScheduledTask*) PowerShell(Stop-Process*) "
    "PowerShell(Start-Process*)"
)


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
    result = subprocess.run(
        [
            claude_path, "--print",
            "--allowedTools", ALLOWED_TOOLS,
            "--disallowedTools", DISALLOWED_TOOLS,
            "--permission-mode", "dontAsk",
        ],
        input=prompt,
        capture_output=True, text=True, timeout=1800,
        env=clean_env, cwd=str(ROOT),
    )
    print(result.stdout)
    if result.stderr:
        print("[weekly_code_audit] stderr:", result.stderr, file=sys.stderr)
    if result.returncode != 0:
        print(f"[weekly_code_audit] claude exited {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)
    print("[weekly_code_audit] done")


if __name__ == "__main__":
    main()
