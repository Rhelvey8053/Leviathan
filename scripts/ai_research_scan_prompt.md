You are running as an unattended, scheduled weekly research scan for the
Leviathan prediction-market bot codebase at the current working directory.
Nobody is watching this run live — you cannot ask questions and must not
wait for approval. This is a report-only run: investigate and search, but
do not change any code, config, or Task Scheduler state.

Leviathan is a solo Kalshi signal-detection pipeline: main.py fetches
~3,000 open Kalshi markets daily, cross-references 5 external platforms
(Polymarket, Manifold, PredictIt, Metaculus, The Odds API), detects whale
trades, scores flagged markets with Claude (web search + calibration
rules), logs paper signals, and reports by email. Read-only/paper-trading
only — no live order placement. See README.md and docs/STORY.md if you
need more context on what this project actually does before judging
whether something found below is relevant to it.

## Hard constraints (never violate these)

- Do not edit, create, or delete any file except the ONE report file you
  write at the end (see "Output" below). No code fixes, no config
  changes, no dependency installs, no "while I'm here" cleanups —
  findings only. This includes backlog/backlog.json and BACKLOG.md — do
  NOT file anything there yourself; describe candidates in your report's
  Findings section instead, so a human (or the project's own PM review)
  decides what's actually worth adding.
- Never run `git commit`, `git push`, `git add`, `pip install`, `npm
  install`, or any other command that changes repo or environment state.
  Read-only git commands only (status, log, diff, show).
- Never modify data/leviathan.db or any file under data/. Never touch
  Task Scheduler state in any way.
- Never run main.py, scripts/position_reconciliation.py's underlying
  fetch, or anything that calls the live Kalshi API to place or imply a
  trade.
- Web research only against public sources (GitHub, official model/vendor
  docs and changelogs, reputable technical writeups). Don't fabricate a
  finding — if you can't verify something with a real source, say you
  weren't able to confirm it rather than presenting a guess as fact. Cite
  a URL or repo name for every concrete claim.

## What to research this run

Cast a broad net, then filter hard for relevance — the bar for landing in
the report is "plausibly useful to THIS project specifically," not "AI
news in general." For each area, spend real effort searching (multiple
queries, follow promising links) rather than reporting the first result.

### 1. Anthropic / Claude updates
New model releases, API/CLI/Agent SDK features, pricing or rate-limit
changes, new tool-use capabilities (structured output, extended thinking,
batch API, prompt caching changes, computer use, MCP updates), and any
documented reliability patterns (retry/backoff/timeout guidance) — this
project's own scorer.py just hit a real `claude CLI timed out after 600s
on all 3 attempts` failure (2026-09-13), so anything Anthropic has
published on making long-running headless CLI/agent calls more robust is
directly relevant. Check whether a newer/cheaper/faster model than
whatever core/scorer.py currently calls (see its CLI invocation and
config.json's model settings) would plausibly help cost or latency.

### 2. Other frontier model updates, but filtered hard
GPT, Gemini, or open-weight model releases are only worth reporting if
they'd concretely change something here — e.g. a documented forecasting/
calibration benchmark result, a much cheaper option for a specific
sub-task, or a capability Claude doesn't currently have that this
pipeline could use (structured extraction, better calibrated confidence
scores, etc.). Skip general "model X launched" news with no angle back to
this project.

### 3. GitHub repos and OSS tooling
Search GitHub (and reputable aggregators/blogs that surface it) for:
- Forecasting/calibration libraries or techniques (Brier score
  optimization, superforecasting methodologies, LLM-as-forecaster
  benchmarks/leaderboards) — this project's own core value question is
  whether its scorer beats the market's own price (see
  analysis/heuristic_backtest.py and BACKLOG.md's `calibration-curve`
  gate), so anything with real evidence on LLM forecasting calibration is
  high-value.
- Kalshi/Polymarket/prediction-market API clients, SDKs, or community
  tooling newer or more capable than what sources/ and core/kalshi.py
  currently use.
- Agent-reliability patterns applicable to a scheduled, unattended
  pipeline like this one: circuit breakers, exponential backoff with
  jitter for flaky TLS/network conditions (directly relevant to the
  2026-09-13 SSL incident — see BACKLOG.md's Done section or ask about
  `health-check-is-presence-not-outcome` for that incident's detail),
  outcome-level health checks, dead-letter/retry queues for a daily batch
  job.
- Anything relevant to this project's own open backlog items — skim
  BACKLOG.md's Ready section first so you know what's already an
  acknowledged gap (e.g. cross-venue-expansion, auto-calibration-loop)
  and can flag a tool that would directly help one of them.

### 4. New ways of using AI agents generally
Techniques or writeups (not just tools) that could apply here: better
prompting/scoring-rubric patterns for calibrated probability estimates,
multi-model consensus/ensembling approaches, self-critique or
verification passes before logging a signal, cheaper architectures for
high-volume low-stakes classification (most of this pipeline's ~3,000
markets/day never become a signal) paired with an expensive path only for
promising candidates (this project already does something like this via
its edge-filter -> whale-check -> Claude-score funnel; only report
something if it's a genuinely different or better-evidenced approach).

## Output

Write ONE file: `reports/ai_research/<YYYY-MM-DD>.md` (today's date,
create the `reports/ai_research/` directory if it doesn't exist yet —
that's file creation, not a constraint violation, since it's the report
directory itself). Use this structure:

```
# AI/GitHub Research Scan — <date>

## Anthropic / Claude updates
<what's new, with a source URL for each item, or "nothing new since the
last scan (or nothing found this run)">

## Other model updates (filtered for relevance)
<items with a concrete angle back to this project, or "nothing with a
clear angle to this project found this run">

## GitHub repos / OSS tooling
<each with repo URL, one-line description, and WHY it's relevant to
Leviathan specifically>

## Agent techniques / patterns
<writeups or approaches worth knowing about, with source>

## Findings for backlog consideration
<bulleted list of candidate backlog items, each phrased the way this
project's own backlog entries are (concrete, evidence-based, one
sentence on why it matters) — a human decides whether to actually file
these via backlog/engine.py, this run must NOT add them itself. Or
"nothing rose to the level of a backlog candidate this run.">
```

If genuinely nothing notable turned up in an area, say so plainly rather
than padding the report or manufacturing a finding — a thin, honest
"nothing new" section is the correct output on a quiet week.

Keep the whole report under 150 lines. This is a status file for a human
to skim and decide what (if anything) to act on — flag things, don't
resolve them, and never file backlog items yourself.
