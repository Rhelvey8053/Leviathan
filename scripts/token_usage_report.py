"""
Measures actual Claude Code token usage for this repo, so token-reduction
changes (CLAUDE.md, MCP server registration, etc.) can be verified instead
of assumed.

Source of truth: Claude Code's own local session transcripts at
~/.claude/projects/<encoded-repo-path>/*.jsonl. Each assistant turn logs a
real `usage` block from the Anthropic API response — cache_creation_input_tokens
is the number of *fresh* (uncached) input tokens that turn cost, which is the
right proxy for "how much re-derivation/exploration Claude had to do": a
turn that hits CLAUDE.md and prior cached context pays cache_read instead.

Usage:
    python scripts/token_usage_report.py                  # full report
    python scripts/token_usage_report.py --since 2026-08-21  # split before/after a date
    python scripts/token_usage_report.py --write-baseline    # record docs/token_usage_baseline.md
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _encode_project_path(path: Path) -> str:
    return str(path).replace(":", "-").replace("\\", "-").replace("/", "-")


def _session_dir() -> Path:
    return Path.home() / ".claude" / "projects" / _encode_project_path(REPO_ROOT)


def _session_stats(jsonl_path: Path) -> dict | None:
    seen_msg_ids = set()
    fresh_tokens = 0
    cache_read_tokens = 0
    output_tokens = 0
    thinking_tokens = 0
    timestamps = []

    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = obj.get("timestamp")
            if ts:
                timestamps.append(ts)
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message", {})
            mid = msg.get("id")
            usage = msg.get("usage")
            if not usage or not mid or mid in seen_msg_ids:
                continue
            seen_msg_ids.add(mid)
            fresh_tokens += usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
            cache_read_tokens += usage.get("cache_read_input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)
            thinking_tokens += (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0)

    if not timestamps:
        return None
    timestamps.sort()
    session_date = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00")).date()
    return {
        "session_id": jsonl_path.stem,
        "date": session_date,
        "fresh_tokens": fresh_tokens,
        "cache_read_tokens": cache_read_tokens,
        "output_tokens": output_tokens,
        "thinking_tokens": thinking_tokens,
        "turns": len(seen_msg_ids),
    }


def collect_sessions() -> list[dict]:
    sdir = _session_dir()
    if not sdir.is_dir():
        print(f"No session directory found at {sdir}", file=sys.stderr)
        return []
    sessions = []
    for jsonl_path in sorted(sdir.glob("*.jsonl")):
        stats = _session_stats(jsonl_path)
        if stats and stats["turns"] > 0:
            sessions.append(stats)
    sessions.sort(key=lambda s: s["date"])
    return sessions


def _avg(sessions: list[dict], key: str) -> float:
    return sum(s[key] for s in sessions) / len(sessions) if sessions else 0.0


def print_report(sessions: list[dict], since: date | None) -> None:
    if not sessions:
        print("No sessions with usage data found.")
        return

    print(f"{'date':<11} {'session':<10} {'fresh':>9} {'cache_read':>11} {'output':>8} {'turns':>6}")
    for s in sessions:
        print(
            f"{s['date']} {s['session_id'][:8]:<10} {s['fresh_tokens']:>9,} "
            f"{s['cache_read_tokens']:>11,} {s['output_tokens']:>8,} {s['turns']:>6}"
        )

    print()
    print(f"Sessions: {len(sessions)}")
    print(f"Total fresh (uncached) input tokens: {sum(s['fresh_tokens'] for s in sessions):,}")
    print(f"Avg fresh tokens/session: {_avg(sessions, 'fresh_tokens'):,.0f}")
    print(f"Avg cache-read tokens/session: {_avg(sessions, 'cache_read_tokens'):,.0f}")

    if since is not None:
        before = [s for s in sessions if s["date"] < since]
        after = [s for s in sessions if s["date"] >= since]
        print()
        print(f"--- Split at {since.isoformat()} ---")
        print(f"Before: {len(before)} sessions, avg fresh tokens/session = {_avg(before, 'fresh_tokens'):,.0f}")
        print(f"After:  {len(after)} sessions, avg fresh tokens/session = {_avg(after, 'fresh_tokens'):,.0f}")
        if before and after:
            b, a = _avg(before, "fresh_tokens"), _avg(after, "fresh_tokens")
            if b > 0:
                pct = (a - b) / b * 100
                print(f"Change: {pct:+.1f}%")


def write_baseline(sessions: list[dict], since: date) -> None:
    before = [s for s in sessions if s["date"] < since]
    if not before:
        print("Nothing to baseline: no sessions before the cutoff date.", file=sys.stderr)
        return
    avg_fresh = _avg(before, "fresh_tokens")
    avg_cache_read = _avg(before, "cache_read_tokens")
    out_path = REPO_ROOT / "docs" / "token_usage_baseline.md"
    out_path.write_text(
        f"""# Token usage baseline

Recorded {date.today().isoformat()} as the reference point for judging whether
CLAUDE.md + the `leviathan` MCP server registration (added {since.isoformat()})
actually reduce Claude Code's token usage on this repo, instead of assuming it.

Source: `python scripts/token_usage_report.py --since {since.isoformat()}`, which
reads real per-turn `usage` blocks from local Claude Code session transcripts
(`~/.claude/projects/<encoded-repo-path>/*.jsonl`) — not an estimate.

## Baseline (sessions before {since.isoformat()})

- Sessions: {len(before)}
- Avg fresh (uncached) input tokens/session: {avg_fresh:,.0f}
- Avg cache-read tokens/session: {avg_cache_read:,.0f}

"fresh tokens" = `input_tokens + cache_creation_input_tokens`, summed per
session, deduped by message id. It's the right proxy for "how much Claude had
to actually read/re-derive" — content served from cache_read is the cheap
path, uncached content is what a good CLAUDE.md and fewer ad-hoc scripts
should shrink.

## How to check progress

Re-run `python scripts/token_usage_report.py --since {since.isoformat()}` after
accumulating a handful of post-baseline sessions and compare the "After" avg
against the {avg_fresh:,.0f} figure above. Don't judge it off one session —
early post-baseline sessions include the CLAUDE.md authoring work itself.
""",
        encoding="utf-8",
    )
    print(f"Wrote baseline to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=str, default=None, help="YYYY-MM-DD split date (default: today)")
    parser.add_argument("--write-baseline", action="store_true", help="Write docs/token_usage_baseline.md")
    args = parser.parse_args()

    since = date.fromisoformat(args.since) if args.since else date.today()
    sessions = collect_sessions()

    if args.write_baseline:
        write_baseline(sessions, since)
    else:
        print_report(sessions, since)


if __name__ == "__main__":
    main()
