# Leviathan — Methodology

**Purpose of this document:** an honest, outside-readable account of what
Leviathan measures, a real methodological flaw it caught in itself, and the
falsification test it has pre-committed to. Written for review, not for
promotion — if the design is wrong, the fastest way to find out is to let
someone else look at it.

Last updated: 2026-09-14. Current numbers below are live at time of writing,
not cherry-picked for this document — see "Current state" for exactly how to
reproduce them.

**2026-09-14 update: the pre-registered n≥50 checkpoint (Section 4) has been
reached and evaluated. Result: FAIL.** See the rewritten Section 5 below and
`docs/PREREGISTRATION.md`'s Amendment Log for the full computation. This is
not a preliminary or softened result — the entire 95% confidence interval is
negative, not merely failing to clear zero.

---

## 1. What Leviathan does

Leviathan is a signal-detection system for [Kalshi](https://kalshi.com), a
regulated US exchange trading contracts on the probability of real-world
events. Each day it:

1. Downloads the open market catalog (2,000-3,000+ contracts).
2. Filters to markets that are structurally interesting — thin books, price
   drift, or a heuristic base rate that disagrees with the current price.
3. Cross-references the same question across four other platforms
   (Polymarket, Manifold, PredictIt, Metaculus) and flags large individual
   trades or order-book imbalances that may indicate informed positioning.
4. Scores the flagged markets with an LLM (Claude), given live web search and
   47 calibration rules, producing a probability estimate, a direction
   (YES/NO/PASS), and a confidence tier.
5. Logs every signal — market price at the time of the call, the model's
   estimate, and eventually the real outcome — to SQLite.
6. Emails a daily report.

Everything is **paper only.** No order is ever placed, amended, or
cancelled. The entire point of the project so far has been building the
measurement apparatus before asking whether the thing being measured is any
good.

---

## 2. The market-price anchoring problem

This is the methodological issue this document exists to surface, because it
is exactly the class of problem an outside reader catches faster than the
person who built the system.

`core/scorer.py` shows the model the market's current price as part of every
scoring prompt, and explicitly instructs it to move its estimate toward that
price absent strong contrary evidence (an "anchoring guard," there to stop
the model from making wild contrarian calls on thin evidence). That's a
reasonable design choice on its own — but it has a direct consequence for
measurement: **a scorer that simply echoed the market price back would also
score well on a naive accuracy metric.** A low Brier score, by itself, is not
evidence of real edge if the scorer is just reading the price it was shown.

This was not caught by design — it was caught by building an independent
baseline and comparing against it (`market-baseline-brier`, shipped
2026-07-23): for every resolved signal, we compute the Brier score of *the
market price alone* (as if the market price were the forecast), using the
identical scoring formula as the model's own Brier score. If the model can't
beat that baseline, its apparent accuracy is anchoring, not skill.

The first real comparison was unflattering: at n=8 resolved signals
(2026-07-23), the market-price baseline scored 0.0022 (Brier) against the
scorer's 0.0578 — the market beat the model. Both scores look "excellent" in
isolation on the standard 0-0.25 Brier scale; only the *comparison* reveals
that one of them is doing no better than reading the price off the screen.

---

## 3. The baseline comparison, precisely

Both figures are computed by the same function
(`core.logger.brier_component()`) over the same rows, differing only in
which value is scored — the model's `our_estimate` or the market's
`market_price` at scan time — against the same real outcome. This guarantees
the two numbers can never disagree due to a formula difference; any gap
between them is a real gap in what's being predicted.

The comparison that matters is **paired**, not two independent aggregates:

```
delta_i = brier_market_i - brier_scorer_i      # positive = model beat the market, that row
mean_delta = mean(delta_i across all qualifying rows)
```

A paired test has substantially more statistical power than comparing two
aggregate Brier scores as if they came from independent samples — which is
what a naive "0.0578 vs 0.0022" headline comparison would be doing. A row
only qualifies if it has a resolved outcome, a valid direction, and **both**
`our_estimate` and `market_price` populated — no missing value is ever
imputed as 0.5 or any other default in either direction.

A second, complementary instrument — the **price-blind scoring arm**
(`core/blind_scorer.py`, shipped 2026-07-26) — scores a subset of markets
with no market price shown at all and none of the price-anchoring
calibration rules, as a direct counterfactual: does the model still produce
a reasonable estimate with the anchor removed? This is built, tested, and
wired in, but **has not yet been run for real** — it forces the metered
Anthropic API path rather than the Pro-subscription CLI path the rest of the
pipeline uses, and turning it on is a deliberate, not-yet-made decision
given the project's policy of not spending metered API budget without
explicit, per-instance authorization.

---

## 4. Pre-registered kill criterion

Full text: [`docs/PREREGISTRATION.md`](PREREGISTRATION.md) (dated
2026-07-25, append-only from that date — nothing above the amendment log in
that file has been or will be edited after the fact).

In short: the project committed, **before** the checkpoint's data existed,
to a specific falsification test rather than an open-ended "we'll know it
when we see it" bar.

- **Checkpoint:** the first point at which the paired population (both
  `our_estimate` and `market_price` present, resolved, valid direction)
  reaches **n ≥ 50**.
- **Metric:** mean paired Brier delta (model minus market baseline) with a
  95% confidence interval.
- **Pass:** the CI's lower bound is above zero — the model beats the market
  baseline, and the margin isn't plausibly zero.
- **Fail:** the lower bound is at or below zero — either the model isn't
  better on average, or it's better but not distinguishably so at 95%
  confidence. A positive point estimate alone, without a CI clearing zero,
  is explicitly **not** sufficient to pass.
- **On fail:** new heuristics, new scoring logic, and prompt tuning aimed at
  "better" estimates halt. Validation infrastructure, bug fixes, and the
  price-blind arm's own result are explicitly *not* halted — the next step
  is a required, dated post-mortem before signal development resumes.

The document is append-only specifically so a threshold can't be quietly
loosened after seeing an unfavorable result.

---

## 5. Current state — checkpoint reached and evaluated: FAIL

As of this writing (2026-09-14): **54 resolved paper signals**, past the
n=50 checkpoint registered in Section 4. The checkpoint was evaluated the
same day the paired population crossed 50, using the exact pre-committed
formula — no threshold was adjusted after seeing the result, which is the
entire point of pre-registering it.

| Metric | Value |
|---|---|
| Total paper signals logged | 80 |
| Resolved | 54 |
| Win rate | 35% |
| Scorer Brier score | 0.2374 — "FAIR (near random)" |
| Market-baseline Brier score | 0.1299 — "GOOD" |
| Scorer vs. baseline (aggregate) | Scorer is **worse** than the market-price baseline (delta +0.1075) |
| Hypothetical P&L (paper only) | +$111.00 |

Reproduce with:

```bash
python -m analysis.calibration
```

**Checkpoint result (paired, per Section 3/`docs/PREREGISTRATION.md`):**

```
paired n     = 54
mean_delta   = -0.107466      (brier_market - brier_scorer; negative = scorer worse)
se           = 0.034478
ci_95        = [-0.175044, -0.039888]
```

`ci_95_low = -0.175 <= 0` → **FAIL** per the pre-registered criterion
(Section 4). This is not a borderline call: the entire 95% CI sits below
zero, not merely failing to clear it — a confident result that the scorer
underperforms the market-price baseline at this checkpoint, not an
inconclusive one. Full computation in `docs/PREREGISTRATION.md`'s Amendment
Log, dated 2026-09-14.

**What this means going forward, per Section 4's own pre-committed terms:**
new heuristic categories, new confidence-scoring logic, new scoring rubric
dimensions, and any `core/scorer.py` change aimed at improving edge are
halted. Infrastructure, validation, bug fixes, and reporting are not.
Resuming requires a written post-mortem addressing whether the price-blind
scoring arm (Section 3) — not yet run, since it requires metered API spend
not yet authorized — shows the anchored scorer adds any measurable value
over blind estimation. That post-mortem does not exist yet; this entry is
the checkpoint result, not the post-mortem.

A same-day, independently-computed decile calibration curve (buckets every
resolved bet by predicted win probability, checks actual win rate per
bucket) found Expected Calibration Error 22.4pp, "POOR," with the
miscalibration specifically *systematic overconfidence* — 8 of 9 populated
buckets underperformed their own predicted rate — rather than scattered
noise. This is corroborating evidence from a different instrument on the
same underlying population, not a restatement of the same number.

**Prior snapshots (preserved for comparison, not current):**
- n=38 (2026-09-08): 59 signals, 38 resolved, win rate 34%, scorer Brier
  0.2169, market-baseline Brier 0.1152, delta +0.1017, P&L +$64.50 —
  below the checkpoint, evidence already pointing the same direction.
- n=25 (2026-09-05): 47 signals, 25 resolved, win rate 32%, scorer Brier
  0.2173 ("FAIR"), market-baseline Brier 0.0935 ("EXCELLENT"), P&L -$2.46
  at the old flat $10/contract sizing.

The direction of the evidence has been consistent since the first n=8
comparison in Section 2 — the market-price baseline has out-forecast the
scorer at every measured point, not just at this checkpoint.

---

## 6. What outside review is being asked to check

This document is being shared for scrutiny, specifically on:

- **Is the paired-Brier-delta test the right instrument** for distinguishing
  edge from anchoring, or is there a cleaner statistical design?
- **Is n=50 a defensible checkpoint size**, or too small/large given the
  effect size this is trying to detect?
- **Population selection** (Section 3) — is requiring both values
  non-null, with no imputation, the right call, or does it introduce a
  selection bias worth naming?
- **Anything else structurally wrong** with the pipeline (Section 1) that
  would invalidate a result before the statistics even matter — the kind of
  thing that "survived months of solo review" the first time, by
  construction, since one person checking their own work has a blind spot
  for exactly the mistakes they'd naturally make.
- **Now that the checkpoint has actually failed:** is the halt scope in
  `docs/PREREGISTRATION.md` ("What signal development halts means if FAIL")
  the right boundary, or does it stop too little / too much? Is a written
  post-mortem gated on the still-unrun price-blind arm the right resumption
  condition, or is there a faster way to distinguish "anchoring artifact"
  from "the scorer genuinely isn't adding value" without spending the
  metered API budget that arm requires?

Repository: `github.com/Rhelvey8053/Leviathan` (public). Route feedback via
a GitHub issue or PR on that repository.
