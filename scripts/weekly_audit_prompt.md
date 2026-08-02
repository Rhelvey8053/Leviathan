You are running as an unattended, scheduled weekly health check AND
calibration-fine-tuning pass on the Leviathan prediction-market bot codebase
at the current working directory. Nobody is watching this run live — you
cannot ask questions and must not wait for approval. Work within the
constraints below exactly; there is no human to catch a mistake before it
happens except the one checkpoint you must always leave in place: nothing
gets committed.

## Hard constraints (never violate these)

- You MAY edit core/scanner.py's `_HEURISTIC_RULES` table and files under
  tests/ (see "Drafting a calibration fix" below). Do NOT edit any other
  source file for any reason — no drive-by cleanups, no unrelated fixes,
  no touching main.py/core/logger.py/core/scorer.py/anything else, even if
  you spot something that looks wrong. Report it instead.
- Never run `git commit`, `git push`, `git add`, or any other command that
  changes repo state. Read-only git commands only (status, log, diff, show).
  Leave any edits you make as an uncommitted working-tree diff — that IS the
  deliverable, not a commit.
- Never modify data/leviathan.db, any file under data/, or the schema in
  core/logger.py. Read-only DB queries only, and only via the analysis
  scripts named below or throwaway read-only `py -c` one-liners — never raw
  sqlite3 UPDATE/INSERT/DELETE/ALTER, never ALTER TABLE.
- Never run scripts/position_reconciliation.py's underlying fetch, main.py,
  or anything that calls the live Kalshi API to place or imply a trade.
  Never run core.logger.pull_real_fills for any reason.
- If you end the run with ANY uncommitted edit still in the working tree
  that is not accompanied by a full explanation in the report (what changed,
  why, what you verified), that is a failure of this task — the report and
  the diff must always be readable together as one story.
- If your test suite run fails, or a full-corpus diff (see below) turns up
  anything unexpected, REVERT your edit (`git checkout -- core/scanner.py
  tests/...` for the specific files, never a bare `git checkout .`) and
  report the finding as text instead of leaving a broken diff behind.

## What to check this run

1. Run the full test suite (`py -m pytest tests/ -q`). Report pass/fail
   counts. If anything fails, include the failure output.
2. Run `py analysis/heuristic_backtest.py` and report the overall Brier
   score and directional accuracy. Compare against the numbers in the most
   recent prior `reports/audits/*.md` file if one exists — flag any material
   regression (Brier getting worse, not just noise).
3. Read BACKLOG.md's Locked section. For each gated item, check whether its
   stated threshold (e.g. `resolved_count >= 25`) is now met by querying
   data/leviathan.db read-only (e.g. `SELECT COUNT(*) FROM signals WHERE
   direction != 'PASS' AND result != ''`) via a throwaway `py -c` one-liner,
   the same way past sessions in this repo have done. Flag any item whose
   gate now appears to be satisfied.
4. Run `git log --oneline -10` and `git status`. Note anything that looks
   like abandoned work-in-progress (long-uncommitted changes, a stale
   branch) — but do not act on it.
5. Skim BACKLOG.md's Ready section (if non-empty) for anything that looks
   stale or already resolved by code you can see now.

## Drafting a calibration fix (the one thing you're allowed to change)

Look at `analysis/heuristic_backtest.py`'s per-label breakdown (step 2
above). For each label with n >= 20 and |calibration gap| > 0.15, investigate
it exactly the way past sessions in this repo have (see BACKLOG.md's Done
entries for `win-catchall-recalibration`, `show-renewal-recalibration`,
`price-threshold-recalibration`, `production-delivery-milestone-recalibration`,
and `hurricane-recalibration` for the full worked methodology and writing
style to match):

1. Pull every real settled_markets title matching that label (via
   `core.scanner.get_heuristic_label`), grouped by `event_ticker`.
2. Determine the root cause. Two safe-to-fix shapes, both already seen
   repeatedly in this codebase:
   - **Ladder family**: >=90% of matches trace to one or a small number of
     event_tickers sharing one underlying real-world quantity with many
     threshold rungs (price levels, category levels, delivery counts) that
     resolve together. Fix: remove the keyword rule entirely (or, if the
     keyword also legitimately matches a genuine non-ladder case elsewhere
     in the corpus, do NOT remove it — see the disqualifying case below).
   - **Many-way field**: matches are a single low-probability outcome among
     a fixed, enumerable population (award nominees, competition entrants,
     named-storm lists). Fix: re-tune the flat rate to the measured YES rate
     across those matches.
3. Before drafting ANYTHING, verify the fix's actual effect with a
   throwaway script: for every settled_markets title, compare
   `get_heuristic_label`/`estimate_base_rate` under the CURRENT code vs. a
   simulated post-fix `_HEURISTIC_RULES` table. Confirm the change affects
   ONLY the titles you intended and nothing else changes.
4. **Do NOT draft a fix, and instead just report the finding, if ANY of
   these hold** (this list exists because a past session in this repo
   caught exactly this mistake before it shipped — see `hurricane-
   recalibration` in BACKLOG.md for the full story):
   - The real matches split into more than one structurally distinct
     sub-pattern (e.g. some are a ladder, others are a many-way field).
     Splitting one rule into several sub-rules with correctly ordered
     precedence is exactly the kind of judgment call this run must not
     make unattended.
   - A "removal" fix would NOT actually stop the titles from matching,
     because they'd still be caught by a different, broader, still-active
     rule elsewhere in `_HEURISTIC_RULES` (verify this explicitly in the
     same throwaway script — after removing the rule, re-run
     `get_heuristic_label` on the same titles and confirm they now return
     `None`, not some other label).
   - Fewer than 90% of matches share the same root cause, or the sample is
     genuinely mixed with no dominant explanation.
   - Any part of steps 1-3 above raises something you're not fully certain
     of. When genuinely unsure, report instead of drafting — a documented
     finding is always safe; a wrong edit to live calibration logic is not.
5. If you DO draft a fix: edit `_HEURISTIC_RULES` in core/scanner.py with a
   comment matching this codebase's existing style (root cause, evidence,
   date), add/update tests in tests/test_scanner.py following the existing
   pattern (see e.g. `test_hurricane_category_ladder_recalibration`), run
   `py -m pytest tests/ -q` and confirm 100% green, then re-run
   `analysis/heuristic_backtest.py` and confirm the label's own calibration
   gap improved. Leave the diff uncommitted. Do not touch BACKLOG.md's
   Ready/Done tables — describe the draft fully in this run's report instead
   so a human decides whether/how to log it once they review and commit.
6. At most ONE calibration fix per run. If multiple labels qualify, draft
   the one with the largest |gap| * n (most impactful) and report the rest
   as findings for a future run.

## Output

Write ONE file: `reports/audits/<YYYY-MM-DD>.md` (today's date). Use this
structure:

```
# Weekly Audit — <date>

## Test suite
<pass/fail summary>

## Calibration (heuristic_backtest.py)
<Brier, directional accuracy, comparison to last audit if available>

## Backlog gates
<any newly-unlocked Locked items, or "no gates newly met">

## Git / repo state
<anything notable, or "nothing notable">

## Drafted fix this run
<If you drafted a calibration fix: label, root cause, evidence (match
counts/tickers), the exact diff summary, full-corpus diff results, test
results, before/after calibration numbers. If you found a candidate but
did NOT draft a fix per the disqualifying rules above, say which rule
applied and why. If no label qualified at all, say so.>

## Findings
<bulleted list of anything else worth a human's attention, each with
enough detail to act on later — or "nothing to report this week">
```

If genuinely nothing is notable, still write the file with that stated
plainly — a thin, honest "nothing changed" entry is the correct output on a
quiet week, not a reason to skip writing the file or to manufacture findings.

Keep the whole report under 200 lines. This is a status file for a human to
skim, not a full investigation writeup — flag things, don't resolve them,
except for the one drafted fix (if any), which should be fully explained.
