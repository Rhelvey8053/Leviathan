# Power BI Export Schema — Findings (powerbi-schema-hardening)

Recorded 2026-07-24 against `data/powerbi_export/signals.csv` (182 rows) and
`data/leviathan.db`. This is a decision record, not a spec — it documents
what the export currently means, not what it should mean. No existing
column value was changed to produce this document.

---

## 1. `run_id` foreign key

`run_id` is now an explicit column in `signals.csv`, joining to `run_id` in
`runs.csv`.

**Coverage:** 165/182 rows (91%) have a populated `run_id`. All 165 are
`source='paper'` rows — every paper signal has one, with zero exceptions.

**The 17 blank rows are never backfilled by guessing.** A backfill
(`core.logger.backfill_run_id()`) was run once against the real DB. Result:
**0 backfilled, 17 unrecoverable.** All 17 are `real_fill` (7) or
`research_probe` (10) rows:

- `log_probe()` (research_probe rows) never writes `run_id` — its INSERT
  statement doesn't include the column. There was never a value to lose.
- `pull_real_fills()` (real_fill rows) hardcodes `run_id=''` for every fill,
  matched to a prior paper signal or not. A fill is an execution event, not
  a scan run — there is no `runs` table row a fill directly belongs to.

One indirect recovery path exists in the schema and is checked: if a
`real_fill` row's `signal_call_id` points to a paper row that has a real
`run_id`, that run_id is borrowed (genuine FK traversal, not an inference).
On the current data, zero of the 17 blank rows have a populated
`signal_call_id` — none of the affected fills were matched to a prior paper
signal — so this path recovers nothing today, but the function is real and
tested (`tests/test_logger.py`) in case future data hits it.

**Conclusion:** blank `run_id` currently means "this row did not originate
from a `main.py` scan run" — a structural fact, not a data-quality gap. No
nearest-timestamp or other inferred join was used, since the pipeline runs
twice daily and any such join would misattribute rows to the wrong run.

---

## 2. `source` discriminator

**Finding, correcting the item's original notes:** the notes assumed "no
value other than paper currently appears." That is **false** as of this
audit — `source` currently has three distinct values:

| source | count | % |
|---|---|---|
| paper | 165 | 91% |
| real_fill | 7 | 4% |
| research_probe | 10 | 5% |

`source` is populated on **every** row (0/182 blank) — it is a reliable,
always-present discriminator regardless of the value-count assumption being
wrong.

**Does it reliably distinguish live paper signals?** Yes. Every aggregate
function in `core/logger.py` that reports on "paper signals" filters via
`source = 'paper' OR source IS NULL` (the `_PAPER` constant) — confirmed
directly by `get_brier_score()`/`get_market_baseline_brier_score()` already
excluding `real_fill`/`research_probe` rows (tested). A future `replay-runner`
source value (e.g. `'replay'`) would be excluded by this same filter
automatically, **as long as it picks a distinct value and never reuses
`'paper'`** — the mechanism generalizes correctly; it just wasn't verified
against real data before this audit. `signals.csv` itself does not filter by
source at export time — it exports every row, `source` included, so a
Power BI report can filter as needed. `core.logger.audit_source_discriminator()`
is now available to re-run this check whenever a new source value is added.

---

## 3. Blank-vs-zero audit

Every column in `signals.csv` where a blank cell currently appears, what the
blank means, and whether it should ever be treated as `0` in a Power BI
aggregation. **Overall finding: no column in this export currently uses
blank to mean zero.** Every blank means "not computed" or "not applicable to
this row" — the pipeline is already consistent on this point; this audit
confirms it rather than finding a contradiction to fix.

| Column | Blank count | Blank means | Zero-safe to substitute? |
|---|---|---|---|
| `run_id` | 17 (9%) | Row did not originate from a scan run (real_fill/research_probe) — see §1 | No — there is no "run zero"; blank is the only correct representation |
| `title` | 1 (1%) | Title scrape failed at log time (known gap — see `title-scraping-fix` backlog item) | No — an empty title is not "no title needed" |
| `confidence` | 7 (4%) | Confidence tier was never assigned (rows outside the scorer's confidence path) | No — HIGH/MED/LOW has no zero-equivalent |
| `flag_path` | 25 (14%) | Row wasn't flagged via HEURISTIC or DRIFT (e.g. fills, probes) | No — not a numeric field |
| `time_horizon` | 25 (14%) | Horizon classification wasn't computed for this row | No |
| `market_price` | 1 (1%) | No price was captured at scan/log time (the one row also missing `market_baseline_brier`, see prior goal) | **No — this is the exact case that must never be coerced to 0.5 or 0; a real price of 0 is a distinct, meaningful value from "no price logged"** |
| `our_estimate` | 18 (10%) | Scorer never produced an estimate for this row (fills/probes/pre-scorer rows) | No — 0.0 would mean "certain NO," a real and very different claim |
| `edge` | 18 (10%) | Derived from `market_price`/`our_estimate`; blank whenever either input is blank | No |
| `net_edge` | 37 (20%) | Spread-adjusted edge unavailable (no order-book spread data for that row) | No |
| `base_rate` | 37 (20%) | No heuristic path applied to this row | No |
| `result` | 171 (94%) | Market hasn't resolved yet (pending) | **No — blank must never be read as LOSS; this is the most consequential one to get wrong** |
| `is_win` | 171 (94%) | Same as `result` — engineered blank-on-purpose (see code's own FIX 2 comment) so Power BI `SUM()`/`AVERAGE()` skip unresolved rows instead of counting them as 0 | No — this blank is intentional and load-bearing |
| `pnl_if_traded` | 171 (94%) | No P&L realized yet (unresolved) | No — do not read as "broke even" |
| `pnl_scaled` | 171 (94%) | Derived from `pnl_if_traded`; same meaning | No |
| `leviathan_score` | 27 (15%) | Row predates the LV scoring feature (`lv_band` already resolves this ambiguity on its own by showing `"Unscored"` rather than blank) | No |
| `close_time` | 25 (14%) | Row predates `close_time` capture | No |
| `heuristic_label` | 181 (99%) | Only 1 row in the whole DB has a heuristic label assigned | No |
| `brier_scorer` | 174 (96%) | Unresolved, or `our_estimate` missing (blank propagates from those columns) | **No — 0.0 would mean "perfect calibration," the opposite of "no data"** |
| `brier_market` | 172 (95%) | Unresolved, or `market_price` missing | Same as `brier_scorer` |

No column value was altered to produce this table, and none should be
altered to "fix" the blank rates above — every blank rate here reflects a
real gap in what has happened so far (rows not yet resolved, features added
partway through the DB's life, sources that don't participate in a given
computation), not a defect in the export logic.

---

## Top next steps

1. If a Power BI visual needs to distinguish "row too new to have a
   `leviathan_score`" from "row scored but scored 0," it already can —
   `lv_band = "Unscored"` vs a real letter band — no export change needed.
2. `title-scraping-fix` (existing backlog item) is the one blank in this
   table that represents a genuine defect rather than a structural gap;
   everything else here is expected behavior now confirmed, not a bug list.
3. Re-run `core.logger.audit_source_discriminator()` before `replay-runner`
   ships its first row, to confirm the new source value doesn't collide
   with `'paper'` — this was the concrete risk the item's notes were
   protecting against.
