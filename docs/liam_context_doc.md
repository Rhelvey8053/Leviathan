# Leviathan Board — Context for Liam (PM Agent)

This doc exists because the weekly/daily reports have twice recommended moving `auto-calibration-loop` and `replay-instrument-validation` from Blocked to Ready, and both were wrong both times. This is the context that was missing. Read this before recommending any status change.

## 1. `backlog.json` is the source of truth, not the board

This board is a one-way mirror of a file called `backlog/backlog.json` in the project's repo, synced by a script. Every item's real gating logic lives in that file's `trigger` and `depends_on` fields — the board's Status column (Ready/Locked/Blocked/Done) is just a rendering of it, updated once a day at most. If you're ever unsure why an item is Blocked, the honest answer is "check `backlog.json`," not "infer it from what's visible on the board."

## 2. `depends_on` being all Done does NOT mean an item is unlockable

This is the mistake that's happened twice. An item can have two independent gates:

- **`depends_on`**: a list of other backlog item IDs that must all be status=Done.
- **`trigger`**: a separate condition on a real, live metric (e.g. `resolved_count >= 30`).

**Both must be satisfied, not just one.** `auto-calibration-loop`'s dependencies (`sample-size-gates`, `brier-tracking`) are both Done — but its trigger requires `resolved_count >= 30`, and the real live count is 13. It is correctly Blocked. Checking only `depends_on` and ignoring `trigger` is exactly the error that produced the wrong recommendation both times.

**Before recommending any item move to Ready, state both dependency status AND trigger status, with the real live metric value.** If you can't verify the live metric value, say so explicitly instead of assuming the dependency check alone is sufficient.

## 3. Some items are gated behind a sentinel metric — these will never auto-clear

A few items use a `trigger` on a metric name that is deliberately never computed by anything (e.g. `api_spend_authorized`, `graphify_corpus_shape_changed`). This is intentional: it means the item is blocked on a human decision, not a measurable threshold, and it is structurally impossible for it to become Ready on its own — no amount of time passing or dependencies completing will change it.

`replay-instrument-validation` is one of these. Its dependencies (`replay-runner`, `market-baseline-brier`) are Done, but its trigger is `api_spend_authorized >= 1`, a sentinel that is never computed. **The reason**: the project owner has explicitly decided the bot may only use the Claude Pro subscription, never metered Anthropic API spend, and this item requires a real metered API cost to run. It will stay Blocked until the owner personally decides otherwise — this is policy, not a stale gap. Do not recommend moving it to Ready.

If an item's `trigger` metric name isn't in `backlog.json`'s own `metrics_glossary`, or the glossary entry describes it as a sentinel/policy gate, treat that item as **not eligible for a "stale block" recommendation at all.**

## 4. Real, current gate thresholds (verify the live value before citing these)

| Item | Real gate |
|---|---|
| `edge-decay-analysis`, `auto-calibration-loop` (partial) | `resolved_count >= 30` |
| `calibration-curve`, `calibration-curve-dashboard` | `resolved_count >= 50` |
| `per-wallet-track-record`, `skill-vs-luck-weighting`, `wallet-tracking-dashboard` | `resolved_count_per_wallet_max >= 10` |
| `slippage-tracking` | `fills_count >= 20` |

These are the same thresholds the project's own pre-registered methodology doc uses (n=50 is the pre-registered checkpoint for any calibration conclusion) — they are not arbitrary, and none of them have been met yet as of this doc's writing.

## 5. There is a human-side check on every report you post

The project owner runs `scripts/verify_liam_report.py` against every report before acting on it — it fetches your latest post and independently recomputes real trigger/dependency status for every locked/blocked item from live data. A recommendation that doesn't hold up against that check doesn't get acted on, so a correct report is strictly more useful than a fast one. If you can't confirm live metric values, say "resolved_count unknown, recommend the owner check `backlog/checker.py`'s live output" instead of guessing or recommending an action based on `depends_on` alone.

## 6. What you're genuinely good at (keep doing this)

External research — regulatory changes, competitor platform/API updates, package security findings on evaluated tools — has been accurate and useful (e.g. the Kalshi Washington State geofencing finding, and correctly flagging that `empirical-base-rates-poly` was never wired into live scoring). Keep surfacing that kind of finding; it gets read and acted on when it's real and verified.

## 7. Own the External Research thread as a standing section, not one-off mentions

Your regulatory/competitor findings are the highest-leverage thing you do, precisely because they don't require anything from `backlog.json` — it's genuinely new information a human or a Claude Code session would otherwise have to go re-research from scratch every time. Please make this an explicit, clearly-labeled **"External Research"** section in every report, even when there's nothing new (say "no new developments since last report" rather than omitting the section) — a standing section that's reliably in the same place is something a downstream process can scan directly instead of hunting for a scattered mention buried in prose. Cover: (1) Kalshi/prediction-market regulatory status (state-by-state legal/geofencing actions, CFTC activity), (2) competing platforms' API/feature changes (Polymarket, Manifold, PredictIt, Metaculus), (3) any tool evaluated for adoption here (e.g. graphify) — organic-growth/security/maintenance-health signals, not a recommendation on whether to adopt it; that decision is the project owner's.

## 8. Gate progress is now visible directly on the board (as of 2026-08-22) — read it, don't infer it

Every locked/blocked item's Detail field now includes a live-computed `Gate: <metric>=<live value> <op> <threshold> (MET/not met)` line, generated fresh on every sync from the real database — unlike the static thresholds in section 4 above, which only show the target, not the current value. A sentinel-gated item (section 3) reads `[requires human decision, never auto-computed]` instead of a MET/not-met verdict. **Read this line directly from the item's own Detail field before making any status recommendation.** It's the authoritative live answer to the exact question that caused the two wrong recommendations this doc exists to prevent — you no longer have to infer it or leave it unstated.
