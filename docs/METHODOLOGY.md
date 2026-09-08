# Leviathan — Methodology

**Purpose of this document:** an honest, outside-readable account of what
Leviathan measures, a real methodological flaw it caught in itself, and the
falsification test it has pre-committed to. Written for review, not for
promotion — if the design is wrong, the fastest way to find out is to let
someone else look at it.

Last updated: 2026-09-04. Current numbers below are live at time of writing,
not cherry-picked for this document — see "Current state" for exactly how to
reproduce them.

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

## 5. Current state (not a checkpoint result)

As of this writing (2026-09-08): **38 resolved paper signals**, 12 away
from the n=50 checkpoint. These numbers are recorded for transparency, not
as a verdict — no PASS/FAIL evaluation has occurred, and none is claimed
here.

| Metric | Value |
|---|---|
| Total paper signals logged | 59 |
| Resolved | 38 |
| Win rate | 34% |
| Scorer Brier score | 0.2169 — "FAIR (near random)" |
| Market-baseline Brier score | 0.1152 — "GOOD" |
| Scorer vs. baseline | Scorer is **worse** than the market-price baseline (delta +0.1017) |
| Hypothetical P&L (paper only) | +$64.50 |

Reproduce with:

```bash
python -m analysis.calibration
```

The hypothetical P&L figure changed method as of 2026-09-08: it's now
confidence-weighted stake sizing ($50 base unit; 1.5x/1.0x/0.5x for
HIGH/MED/LOW confidence — see `core/sizing.py`) applied to every resolved
signal, past and future, rather than a flat $10-per-contract multiply. The
win rate and both Brier scores are sizing-independent and directly
comparable to the earlier n=25 snapshot below; the P&L figure alone is not,
since it's now measuring a different bet-sizing policy, not just a bigger
sample under the old one.

This is the same anchoring risk described in Section 2, still visible at a
larger (though still not-yet-checkpoint) sample: the scorer is currently
tracking the market price worse than the market price tracks itself. This
is recorded here, unedited, because a methodology document that only shows
favorable numbers isn't one.

Two things are true at once: (a) n=38 is still below the pre-registered
n=50 checkpoint — the checkpoint exists precisely because smaller samples
are noisy — and (b) the direction of the current evidence has not improved
since the n=25 snapshot below, and pretending otherwise would defeat the
purpose of publishing this.

**n=25 snapshot (2026-09-05, preserved for comparison, not current):**
total signals 47, resolved 25, win rate 32%, scorer Brier 0.2173 ("FAIR,
near random"), market-baseline Brier 0.0935 ("EXCELLENT"), hypothetical
P&L -$2.46 at the old flat $10/contract sizing.

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

Repository: `github.com/Rhelvey8053/Leviathan` — the current publication
venue and how to route feedback (issue, PR, direct message) is a decision
still open at the time of writing this document, not resolved here.
