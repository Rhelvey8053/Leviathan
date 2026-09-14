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
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROMPT_FILE = ROOT / "scripts" / "ai_research_scan_prompt.md"

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


def main():
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
    sys.exit(run_scan(claude_path, prompt, clean_env))


if __name__ == "__main__":
    main()
