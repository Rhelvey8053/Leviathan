# Leviathan — IDEAS (append-only)

Parking lot for feature ideas raised but not yet scheduled.

**Rules for this file:**
- APPEND ONLY. New ideas go at the bottom. Nothing here is deleted; ideas graduate by being written into a goal file and moved to BACKLOG, or are explicitly marked "declined."
- This file is NEVER read by an agent for direction. It is not a work queue and not a backlog. It is triaged by a human, between goals, only. An idea being written here confers zero authorization to build it.
- Each entry states WHY it is parked and, where one exists, the rigorous/bounded version worth revisiting — so a good-but-premature idea isn't lost and isn't re-litigated from scratch.

---

## 2026-07-18 — Auto cash-out on adverse news

**Idea as raised:** if news or information becomes available that goes against an open position, automatically cash out to cut the loss.

**Status:** parked — not building now, and specifically not at current stage.

**Why parked:**
1. **Breaks clean resolution — contaminates the core metric.** Calibration (Brier, win rate, reliability curve) assumes every signal runs to settlement as a clean binary. An early manual exit produces a path-dependent trading result that is neither a clean WIN nor LOSS, silently corrupting the exact dataset the project exists to build. At low n this is disqualifying — a couple of manual exits would wreck the little resolved signal there is.
2. **The market reprices faster than a daily batch can act.** By the time a daily-cadence system detects the news, matches the market, judges direction, and exits, the price has already moved. You'd sell after the drop, locking in the loss you were trying to avoid.
3. **If entries are +EV, early exit destroys EV.** The correct action on a +EV position is to hold to resolution; that's how the edge is realized. Exiting overrides the (not-yet-validated) entry model with a second, unvalidated news-reaction model — and per (1) you can't even measure whether it helped.
4. **Autonomous news→trade is the highest-stakes version of the unbounded-agent trap.** Monitor → judge relevance → judge direction → judge materiality → execute a transaction: every step is soft judgment, and a wrong one locks in a real loss. Fuzzy LLM judgment triggering a financial action is the worst place to put autonomy.
5. **Identity drift.** This converts Leviathan from "estimates market probabilities and measures its own calibration" into "live trading bot with autonomous position management." That's a successor project, not an add-on — and it should only be built after the estimator is proven.

**Rigorous version worth revisiting (detection, not action):** re-score open markets when their inputs change and FLAG in the report when the live estimate diverges materially (> X) from the entry thesis. This is a *report surface, not a trade* — it touches no money and does not contaminate resolution. Detection is nearly free: the daily re-scan against current price already runs.

**Revisit gate:** only meaningful after the entry model is validated (calibration established, past the resolved-count gates) AND with volume. Even then, ship it as an analysis flag on open positions — never as an autonomous cash-out.
