# Pre-Registered Kill Criterion — Scorer Edge vs. Market-Price Baseline

**Registered:** 2026-07-25
**Status:** ACTIVE — not yet evaluated
**Append-only.** Once a checkpoint below is evaluated, the result is appended
with its own date; no earlier entry in this file is edited or deleted, the
same discipline `docs/IDEAS.md` applies to scope instead of results.

---

## Why this document exists

`core/scorer.py:649` injects the current market price into every scoring
prompt, and `core/scorer.py:245-253` (the ANCHORING GUARD) explicitly
instructs the model to move its estimate toward that price absent strong
contrary evidence. This is deliberate, reasonable scorer design — but it
means a good-looking scorer Brier score is not, by itself, evidence of real
edge. A scorer that simply echoed the market price back would also score
well. `market-baseline-brier` (shipped 2026-07-23) built the instrument that
can tell the two apart: `core.logger.get_brier_score()` (the scorer) and
`get_market_baseline_brier_score()` (the market price alone), computed with
the identical formula (`brier_component()`) over the identical rows.

Committing to a falsification threshold **now**, before the data exists to
evaluate it, is the only way this check means anything. A threshold picked
after seeing the n=50 result is not a test — it's a rationalization. This
document is worthless if amended after the fact, which is why it is
append-only.

---

## Hypothesis under test

**H1 (edge hypothesis):** the scorer's probability estimates are more
accurate — lower Brier score — than the market price alone, by a margin too
large to be explained by chance at n=50.

**H0 (null / anchoring hypothesis):** the scorer's apparent accuracy is
attributable to anchoring on the market price, and provides no
demonstrable improvement over reading the price directly.

This document tests **the scorer as it exists today** (single-pass,
price-anchored). It does not test a hypothetical price-blind scorer —
`price-blind-arm` (backlog, blocked on this item + `llm-cost-ceiling`) is
the correct future instrument for that question, once it exists.

---

## Population (which rows count)

A row counts toward n only if **all** of the following hold:

1. `source = 'paper'` (or `NULL`, the pre-migration default) — matches
   `core.logger._PAPER`, excluding `real_fill` and `research_probe` rows,
   same population `get_brier_score()`/`get_market_baseline_brier_score()`
   already use.
2. `result IN ('WIN','LOSS')` — resolved.
3. `direction IN ('YES','NO')`.
4. **Both** `our_estimate` and `market_price` are non-NULL — a genuine
   paired comparison needs both the scorer's estimate and the market price
   for the same row. This is stricter than either metric's own n
   individually (each only requires its own value); the paired-comparison n
   can be smaller than both.

No row is ever included by substituting a default (0.5, or any other
value) for a missing `our_estimate` or `market_price` — a row with either
missing simply does not count, in either direction.

---

## Metric

For each qualifying row, using `core.logger.brier_component(value, direction, result)`:

```
brier_scorer_i   = brier_component(our_estimate_i,  direction_i, result_i)
brier_market_i   = brier_component(market_price_i,  direction_i, result_i)
delta_i          = brier_market_i - brier_scorer_i     # positive = scorer beat the market that row
```

Across the n qualifying rows:

```
mean_delta = mean(delta_i)
se         = stdev(delta_i) / sqrt(n)          # sample standard deviation, n-1 denominator
ci_95_low  = mean_delta - 1.96 * se
ci_95_high = mean_delta + 1.96 * se
```

(1.96 for a 95% CI — the same z-value `core.report._wilson_ci()` already
uses elsewhere in this codebase, for consistency with the project's existing
statistical convention.)

This is a **paired** comparison (each row contributes one `delta_i`, not two
independent samples), which is the correct test here since both scores are
computed from the same row's outcome — a paired test has much more power
than comparing the two aggregate Brier scores as if they were independent,
which is what a naive "0.0578 vs 0.0022" headline comparison would do.

---

## Checkpoint and pass/fail criterion

**Checkpoint:** the first `python -m analysis.calibration` run (or
equivalent query) where the paired population above reaches **n ≥ 50**.

- **PASS (edge hypothesis provisionally supported):** `ci_95_low > 0` —
  the scorer beats the market-price baseline, and the improvement's 95%
  confidence interval does not include zero. Signal development continues.
- **FAIL (edge hypothesis falsified at this checkpoint):** `ci_95_low <= 0`
  — either the scorer is not better than the baseline on average
  (`mean_delta <= 0`), or it is better but not distinguishably so at 95%
  confidence. **Signal development halts** per the terms below.

A raw point-estimate comparison (`mean_delta > 0` alone, with no CI) is
explicitly **not** sufficient for PASS — at n=50, a positive point estimate
that could plausibly be zero is not evidence of edge, it's noise that
happened to land on the favorable side.

---

## What "signal development halts" means if FAIL

Halted: adding new heuristic categories, new confidence-scoring logic, new
scoring rubric dimensions, or any change to `core/scorer.py` intended to
increase edge (prompt tuning aimed at "better" estimates).

**Not halted:** infrastructure and validation work already in the backlog
that exists to investigate *why* — `price-blind-arm` (a scoring mode with
no market-price line at all, isolating whether the anchoring itself is the
problem), `replay-instrument-validation`, and the `preregistration`
document's own eventual amendment recording the result. Bug fixes,
reporting, and backtesting infrastructure are not "signal development" in
the sense this halts.

Halting is not permanent by default: the next step on FAIL is a written
post-mortem (appended to this document, dated) addressing whether
`price-blind-arm`'s result (once available) shows the anchored scorer adds
*any* measurable value over blind estimation, and whether a redesigned
scorer (not just retuned) is worth building. Resuming signal development
requires that post-mortem to exist first — it is not automatic just because
more data has accumulated since the FAIL checkpoint.

---

## Current state (context only — not a checkpoint result)

As of 2026-07-25: **paired n = 8** (well below the n=50 checkpoint), from
8 resolved paper signals accumulated between 2026-05-23 and 2026-06-19.
Current scorer Brier = 0.0578, market-baseline Brier = 0.0022 (both
EXCELLENT by the 0–0.25 scale, but the scorer is currently *worse* than the
baseline on this small sample — see `docs/PROGRESS.md` 2026-07-23). This
number is recorded here only to prove this document was written before
n=50 was reached, not as an early verdict — n=8 is far too small for
`ci_95_low` to be meaningful, and no checkpoint evaluation has occurred.

At the observed resolution rate (~8 signals over ~4 weeks, itself slow and
lumpy), reaching n=50 will take significantly longer at the current pace.
This is not a reason to lower the threshold — it is a reason this document
needed to be written now rather than closer to n=50.

---

## Amendment log

*(Append-only. Each checkpoint evaluation gets a new, dated entry below this
line. Nothing above this line is ever edited once committed.)*
