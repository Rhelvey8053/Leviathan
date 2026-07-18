# Leviathan — Progress Log

---

## 2026-07-18 — Smart-Money Discovery Funnel Diagnostic

**Goal:** instrumentation only — figure out *why* the winning-trader discovery gate
(`sources/accounts.py: discover_winners -> _score_wallet -> _is_winner`) has
promoted zero wallets across multiple runs, without changing any threshold,
sample size, or gate. Added `sources.accounts.diagnose_discovery()` /
`format_diagnostic_report()` and `scripts/diagnose_discovery.py`. Extracted a
shared `_classify_wallet(stats, config) -> str` (first failing gate name, or
`"PASS"`) that both `_is_winner` and the diagnostic call, so the two can never
silently disagree — proven by a 13-case regression battery over
`_is_winner`'s current boundary behavior.

### Real run (2026-07-18, live Polymarket API, sample=1000 recent trades)

| Stage | Survivors | % of prior |
|---|---:|---:|
| 0. trades fetched | 1000 | — |
| 1. unique wallets | 524 | 52.4% |
| 2. positions returned | 495 | 94.5% |
| 3. scored | 495 | 100.0% |
| 4. resolved_count >= 1 | 166 | 33.5% |
| 5. gate resolved_count>=min (10) | 97 | 58.4% |
| 6. gate win_rate>=min (55.0) | 0 | 0.0% |
| 7. gate position_count>=min (5) | 0 | — |
| 8. gate pct_pnl>=min (10.0) | 0 | — |
| 9. gate cash_pnl>=min (100.0) == WINNERS | 0 | — |

**Biggest single drop-off, stated bluntly:** two-thirds of scored wallets (329/495,
66.5%) never reach `resolved_count >= 1` at all — their only visible positions are
open, or resolved-but-coinflip/sports (excluded from scoring by design). Of the
minority that clear that bar, another 41.6% die at `resolved_count>=10`. But the
single most dramatic number in this run is stage 6: **100% of the 97 wallets that
cleared `resolved_count>=10` still failed `win_rate>=55%` — every one of them.**

**Distribution at the gate where the mass dies:**
`resolved_count` among all 495 scored wallets — min 0, **median 0**, p90 48.6, max
489. Only 19.6% of scored wallets ever reach the resolved_count>=10 bar. Most
sampled wallets simply don't have enough visible resolved (non-coinflip/sports)
history to be evaluated on skill at all.

`win_rate` among the 97 wallets that reached that gate — min 0.00%, median 0.00%,
p90 0.00%, max 0.00%. Zero variance across 97 independent wallets is itself a
finding: manually inspecting six of these wallets' resolved positions (not a code
change, a diagnostic spot-check) found every one of them dominated by systematic
long-shot bucket bets — "will player X be top scorer" style questions where a
wallet buys the YES side of many mutually-exclusive single-outcome contracts
(election-candidate lists, chess/esports tournament-winner lists, exact-score
buckets, temperature/tweet-count range buckets). Percent PnL on these clusters
tightly around -100% by construction — only one bucket in a large N-way partition
can resolve YES — and this bet pattern is not currently caught by
`_is_coinflip`/`_is_sports_title`.

### Verdict (world we are in, not a fix)

**Both mechanisms are active, compounding in sequence.** The dominant failure is
sample mis-specification (world (a)): recent-trade sampling pulls in wallets with
too little visible resolved history to evaluate at all — median resolved_count
across every scored wallet is literally zero, and 80%+ never reach the
resolved_count>=10 bar. But the wallets that *do* clear that bar are not a random
subset of "experienced traders" — they disproportionately got there by placing a
high volume of structurally-near-guaranteed-loss long-shot bets across large
partitioned markets, a bet style the resolved_count gate rewards (it counts
resolved positions, not skill) but that is unrelated to forecasting skill and
happens not to be filtered the way coinflip/sports markets already are. Zero
winners in this run is not strong evidence that skill is rare in the broader
trading population (world (b), as originally framed) — it is better read as
"recent-trade sampling, once past the resolved-count floor, currently surfaces a
long-shot-bucket-betting subpopulation whose win rate is artificially near zero
by construction." Whether skill is *also* genuinely rare in the population this
sampling method misses is not answered by this run.

**No threshold, sample size, or gate was changed.** This run only measured the
existing gate as configured (`min_resolved_count=10`, `min_win_rate=55.0`,
`min_positions=5`, `min_pct_pnl=10.0`, `min_cash_pnl=100.0`).

### Top 3 next steps (decisions this unblocks, not taken here)

1. **Sourcing decision:** evaluate drawing candidate wallets from a
   resolved-history/leaderboard-style endpoint instead of (or in addition to)
   recent-trades sampling, vs. accepting the current recent-trades gate as-is
   knowing it structurally favors high-volume long-shot bettors past the
   resolved_count floor.
2. **Filtering decision (only after (1)):** whether the long-shot single-outcome
   large-N-partition bet pattern observed above is common enough across the
   broader wallet population to warrant its own exclusion category (alongside
   `_is_coinflip` / `_is_sports_title`) — not decided here; this run doesn't
   establish prevalence outside the 97-wallet spot-check.
3. Only after a non-empty verified winners list exists from either decision above:
   design smart-money-as-a-tagged-input to the scorer (input, never trigger —
   every signal records whether smart money touched it, so whale-confirmed vs.
   model-only can be compared at resolution). Separately: revisit whether this
   track is worth further investment at all if a corrected sourcing/filtering
   pass still yields zero or near-zero winners.

No threshold is recommended for adjustment — the numbers above point at sample
composition (who gets sampled, and what bet style survives resolved_count), not
at a mis-calibrated number.
