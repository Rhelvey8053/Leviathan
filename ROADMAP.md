# Leviathan Roadmap
Last updated: 2026-08-22, reconciled against BACKLOG.md (76 items: 1 Ready, 5 Locked, 7 Blocked, 63 Done).

Governing principle, which the backlog already encodes through its gates:
**validate before you widen.** Every phase below either hardens the
measurement apparatus (scorer integrity, edge math) or is explicitly gated
on that apparatus producing enough validated volume before the surface
grows (more venues, more calibration curves, more automation).

Current sample: the backlog's own metrics header reads `resolved=14,
fills=7`. Most validation/calibration gates in the Locked section
(`resolved_count >= 20/30/50`, `fills_count >= 20`) are still not met.
**Volume is the binding constraint on this whole roadmap** — which is
exactly why Phase 2 is now the ONLY thing standing between here and
Phase 4; Phases 1 and 3 have both shipped since this was last updated.

## Phase 1 — Harden the scorer — SHIPPED
`citations-provenance-grounding` is Done: scorer rationale claims are now
grounded in machine-checkable cited-text spans via the Anthropic Citations
API. (`scorer-websearch-grounding` was proposed alongside this but
dropped — web search was already active on both scoring backends; see
Reconciliation below.)

## Phase 2 — Unblock the replay corpus (the real bottleneck, and a human decision, not a build)
Approve the metered API spend for `replay-instrument-validation` so
`backtesting/replay_runner.py` can actually populate `replay_signals`
(0 rows right now, against 12,600 available `settled_markets` — confirmed
live 2026-08-17). The runner forces the paid API backend at ~5 tickers per
invocation against the `daily_cost_ceiling_usd=$20/day` cap, so reaching
n>=300 means dozens of invocations spread over several days. Gated behind
`api_spend_authorized`, a sentinel metric `backlog/checker.py` deliberately
never computes — see CLAUDE.md's hard policy constraints and
`core.llm._check_api_spend_authorized()`. This is a real go/no-go decision
against the standing Pro-subscription-only policy, not a stale block.

Everything downstream depends on this volume: `calibration-curve`
(`resolved_count >= 50`), `edge-decay-analysis` (`>= 30`),
`skill-vs-luck-weighting` (`resolved_count_per_wallet_max >= 10`).
`walk-forward-validation` itself has since shipped (Done), but still has
nothing real to validate against without this corpus. Until this runs,
"validate before widen" has nothing to validate against. **Highest-leverage
unlock in the whole backlog, and now the sole blocker on Phase 4 too —
still Blocked, still just needs a go/no-go on spend.**

## Phase 3 — Fix the edge math — SHIPPED
`net-edge-fee-depth-model` is Done: fees are netted (`net_edge_after_fee`,
`ev_after_fee_per_contract`) and the order-book depth check now gates on
whether a stated stake is actually fillable at a price consistent with the
signal's own edge, closing the executable-depth gap this phase existed to
close.

## Phase 4 — Widen the surface
`cross-venue-expansion` — a normalized multi-venue aggregator layer,
distinct from the existing two-venue `CROSS_MARKET` (Kalshi/Polymarket)
corroboration proxy. Was blocked on both Phase 2 and Phase 3; with Phase 3
shipped, **Phase 2 alone is now the only thing standing in its way**.
Aggregator vendor landscape needs a fresh look before building against
any of them — Polymarket acquired the main independent aggregator, Dome,
in early 2026.

## Smart-money is not a phase
`per-wallet-track-record` and `skill-vs-luck-weighting` are data-gated on
`resolved_count_per_wallet_max >= 10`, not blocked on new build work — they
advance as volume accrues, which Phase 2 and continued live running drive
directly. `wallet-tracking-dashboard` is gated behind
`per-wallet-track-record` the same way.

## Reconciliation
Checked against the real BACKLOG.md (not assumed) before adding anything:

| Proposed | Outcome | Why |
|---|---|---|
| Backtest / replay corpus | Already exists | `backtest-harness`, `replay-asof-reconstruction`, `replay-settled-fetcher`, `replay-runner`, `walk-forward-validation`, `sample-size-gates`, `methodology-writeup` are all already in the backlog. `replay-instrument-validation` is Blocked (see Phase 2), built but empty. |
| Smart-money / whale tracking | Already exists | `per-wallet-track-record`, `skill-vs-luck-weighting` (Locked); `smart-money-drift-alerts`, `whale-actionability-scorecard`, `discovery-funnel-diagnostic` (Done); `wallet-tracking-dashboard` (Blocked). Data-gated, not new work. |
| Cross-venue corroboration | Partially exists | `CROSS_MARKET` flag path (Polymarket-corroboration proxy) and `empirical-base-rates-poly` already cover two-venue corroboration. A broad multi-venue expansion (`cross-venue-expansion`) is genuinely new — added as Blocked. |
| `citations-provenance-grounding` | **Added, Ready** | Confirmed new: no existing Citations API usage anywhere in `core/scorer.py`. |
| `scorer-websearch-grounding` | **Dropped — already done** | `core/llm.py` already calls `web_search_20250305` on the API backend; `core/scorer.py` already passes `--allowedTools WebSearch` on the CLI backend. Both scoring paths already have live web search. Nothing to add. |
| `net-edge-fee-depth-model` | **Added, Ready — narrowed** | Fees are already netted (`net_edge_after_fee`, `ev_after_fee_per_contract`, both Done). Order-book depth is computed (`compute_orderbook_signal`) but never combined with edge to gate on executability — that's the real, still-open gap. No overlap with `slippage-tracking` (that measures realized post-trade slippage on real fills, ex-post; this is a pre-trade liquidity check at scan time). |
| `cross-venue-expansion` | **Added, Blocked** | Filed with `depends_on: [net-edge-fee-depth-model, replay-instrument-validation]`, not Ready — matches the backlog engine's own `determine_status()` computation (verified: non-empty `depends_on` → `blocked`, confirmed programmatically before committing). |

No duplicate IDs: all four proposed IDs (`citations-provenance-grounding`,
`scorer-websearch-grounding`, `net-edge-fee-depth-model`,
`cross-venue-expansion`) checked against all 52 pre-existing backlog IDs
before adding anything — zero collisions.

One correction to the source handoff doc: it described the priority scale
as 1-6; the backlog engine's actual validation (`backlog/engine.py`)
allows 1-9. Doesn't change any of the proposed priority values (all
2-5), just the stated range.
