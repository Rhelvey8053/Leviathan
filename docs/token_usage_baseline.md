# Token usage baseline

Recorded 2026-08-21 as the reference point for judging whether
CLAUDE.md + the `leviathan` MCP server registration (added 2026-08-21)
actually reduce Claude Code's token usage on this repo, instead of assuming it.

Source: `python scripts/token_usage_report.py --since 2026-08-21`, which
reads real per-turn `usage` blocks from local Claude Code session transcripts
(`~/.claude/projects/<encoded-repo-path>/*.jsonl`) — not an estimate.

## Baseline (sessions before 2026-08-21)

- Sessions: 316
- Avg fresh (uncached) input tokens/session: 17,006
- Avg cache-read tokens/session: 122,467

"fresh tokens" = `input_tokens + cache_creation_input_tokens`, summed per
session, deduped by message id. It's the right proxy for "how much Claude had
to actually read/re-derive" — content served from cache_read is the cheap
path, uncached content is what a good CLAUDE.md and fewer ad-hoc scripts
should shrink.

## How to check progress

Re-run `python scripts/token_usage_report.py --since 2026-08-21` after
accumulating a handful of post-baseline sessions and compare the "After" avg
against the 17,006 figure above. Don't judge it off one session —
early post-baseline sessions include the CLAUDE.md authoring work itself.
