"""
scripts/weekly_audit.py - Unattended weekly investigate-and-draft health check.

Runs Claude Code headlessly (claude --print) against a fixed prompt
(scripts/weekly_audit_prompt.md): investigate read-only, and optionally
DRAFT (never commit) at most one calibration fix to core/scanner.py's
_HEURISTIC_RULES + tests/test_scanner.py, per the prompt's own strict
disqualifying-conditions checklist. Restricted via
--allowedTools/--disallowedTools: Edit/Write scoped to only
core/scanner.py and tests/**, git limited to read-only commands (commit/
push/add explicitly disallowed as defense in depth -- the prompt already
forbids them, this is the second layer), never touches data/leviathan.db,
never runs anything that places or implies a real trade.

Scheduled via Windows Task Scheduler -- see
scripts/setup_weekly_audit_scheduler.ps1. Output also captured to
logs/weekly_audit.log for debugging a failed run.
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROMPT_FILE = ROOT / "scripts" / "weekly_audit_prompt.md"

ALLOWED_TOOLS = (
    "Read Grep Glob Write "
    "Edit(core/scanner.py) Edit(tests/test_scanner.py) "
    "Bash(git status) Bash(git log*) Bash(git diff*) Bash(git show*) "
    "Bash(git checkout -- *) "
    "Bash(py -m pytest*) Bash(py analysis/heuristic_backtest.py) Bash(py -c*)"
)
DISALLOWED_TOOLS = "NotebookEdit Bash(git commit*) Bash(git push*) Bash(git add*)"

TIMEOUT_SECONDS = 1800


def run_audit(claude_path: str, prompt: str, clean_env: dict) -> int:
    """Runs the claude --print audit/draft. Returns the process exit code."""
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
        # 2026-08-24: sibling script weekly_code_audit.py hit this exact
        # uncaught case (crashed with a raw traceback instead of a clean
        # failure message) on its first-ever run -- fixed there and
        # mirrored here defensively, even though this script's own log
        # has never shown a timeout in practice.
        print(f"[weekly_audit] claude --print timed out after {TIMEOUT_SECONDS}s -- "
              "no fix was drafted/committed this run.", file=sys.stderr)
        return 1

    print(result.stdout)
    if result.stderr:
        print("[weekly_audit] stderr:", result.stderr, file=sys.stderr)
    if result.returncode != 0:
        print(f"[weekly_audit] claude exited {result.returncode}", file=sys.stderr)
        return result.returncode
    print("[weekly_audit] done")
    return 0


def main():
    claude_path = shutil.which("claude")
    if not claude_path:
        print("[weekly_audit] claude CLI not found in PATH", file=sys.stderr)
        sys.exit(1)

    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    # Same free CLI/Pro-auth path main.py's scorer.py uses -- excluding
    # ANTHROPIC_API_KEY keeps this off metered billing.
    clean_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    print(f"\n[weekly_audit] {datetime.now(timezone.utc).isoformat()}")
    # Prompt goes via stdin, not as a positional CLI argument -- the drafting
    # rules in weekly_audit_prompt.md made the prompt long enough to exceed
    # Windows' command-line length limit ("The command line is too long",
    # confirmed live 2026-08-02 on the first drafting-enabled test run,
    # before any actual investigation happened). Matches the same pattern
    # core/scorer.py's _score_via_cli already uses for its (much shorter but
    # same-shaped) user_prompt.
    sys.exit(run_audit(claude_path, prompt, clean_env))


if __name__ == "__main__":
    main()
