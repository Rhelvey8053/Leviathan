# monday.com sync — Phase 0 discovery

**Status:** Phase 0 only. No writes were made to the monday board. This is a
gate deliverable — see "Open decisions for Reed" at the bottom before any
Phase 2 write happens.

Run date: 2026-08-17. Board queried live via the monday GraphQL API
(`MONDAY_API_TOKEN` in `.env`, not committed).

---

## 1. Source-of-truth flow (confirmed by reading the code)

Read `backlog/engine.py` and `backlog/checker.py` in full. The handoff's
description is correct, with one clarification worth stating explicitly:

- `backlog/backlog.json` is the system of record. `engine.py`'s
  `load_backlog`/`save_backlog`/`cmd_add` are the only writers. New items go
  in via `python -m backlog.engine add ...`.
- `checker.py.run()` reads `backlog.json`, computes live metrics from
  `data/leviathan.db` (read-only), evaluates triggers, and calls
  `compare_statuses()`, which flips `locked`/`blocked` → `ready` **and
  mutates `backlog['items']` in place**.
- **Clarification vs. the handoff doc's diagram:** this isn't only an
  in-memory computation for rendering purposes. `run()` calls
  `save_backlog(backlog_path, backlog)` immediately whenever
  `newly_unlocked` is non-empty — in *both* CLI and `--email` mode. So a
  gate-unlock transition is persisted into `backlog.json` itself the moment
  `checker.py` runs, not just reflected in that run's rendered output. This
  matters for the sync design: `backlog.json` on disk can change between
  monday_sync.py runs purely from `checker.py`'s own scheduled run, with no
  human action in between — the sync's diff logic needs to treat that as a
  normal source update, not something to alert on.
- `write_markdown()` is called unconditionally on every `run()`, so
  `BACKLOG.md` is fully re-derived from `backlog.json` every time — confirmed
  it is a generated artifact, not authoritative. **Do not sync from it.**
- `scripts/gate_notifier.py` writes `data/gate_state.json`. Live schema
  confirmed exactly as documented:
  `{ updated_at, gates: { <id>: { id, area, metric_name, op, threshold, status, value } } }`.
  Current file has 5 gate entries (`per-wallet-track-record`,
  `calibration-curve`, `edge-decay-analysis`, `skill-vs-luck-weighting`,
  `slippage-tracking`), `updated_at` 2026-08-17T14:59:12Z. Two entries show
  `"status": "unknown", "value": null` (the two `per-wallet`/`skill-vs-luck`
  gates, both driven by `resolved_count_per_wallet_max`) — worth noting since
  a naive display of `value` would need to handle `null` distinctly from `0`.

**Conclusion:** the source-of-truth design in the handoff (section 2) is
correct and matches the live code. No changes needed to that design.

---

## 2. backlog.json — current live state

```
updated: 2026-08-16
total: 55
ready: 4   locked: 5   blocked: 5   done: 41
```

This matches the handoff doc's stated baseline (section 1) exactly — no
drift on the `backlog.json` side since the doc was written.

Note in passing (not a Phase 0 blocker): `backlog.json`'s `done` list
includes `kalshi-sdk-migration-implementation`, which an earlier session's
handoff explicitly held back — it's been completed since (commit `4f2adda
Migrate to kalshi-sdk`, 2026-08-1x). Not relevant to this sync work, just
confirming the local repo has moved since the doc's context was written.

---

## 3. Live board inventory vs. section 1's assumptions

Queried board `18426940027` live (`boards`, `columns`, `items_page` with
cursor pagination — 75 items total, confirmed against the doc's stated 75).

**Groups** — match the doc exactly:
`Blocked=group_mm6a2f`, `Locked=group_mm6att3n`, `Ready=group_mm6an4ta`,
`To-Do=new_group29179`, `Completed=new_group43041`.

**Columns** — the 7 documented columns all exist with the documented ids.
**Three additional columns exist that the doc doesn't mention:**

| id | title | type |
|---|---|---|
| `date_mm6agkax` | **Completed On** | date |
| `date_mm6avkdz` | Start Date | date |
| `timerange_mm6a8aaz` | Timeline | timeline |

This matters directly for Phase 1: the doc's plan is to *create* a new
`Completed date` column (section 7, Phase 1). A column called **"Completed
On"** of type `date` already exists and is currently unused (empty on every
item checked). Creating a second, differently-named date column for the same
purpose would be confusing on the board. **Recommend reusing the existing
`date_mm6agkax` ("Completed On") column instead of creating a new one** —
but this is a naming/reuse call, flagged for Reed rather than decided here.
`Start Date` and `Timeline` have no defined use in the sync spec; recommend
leaving them alone (v1 doesn't need them).

**Status labels** — confirmed via `settings_str` on `project_status`:
exactly the 7 documented labels (`Working on it`, `Done`, `Stuck`,
`Not Started`, `Ready`, `Locked`, `Blocked`). All 75 items on the board
currently use only 4 of the 7 (`Ready`/`Locked`/`Blocked`/`Done`) — none are
sitting in `Working on it`/`Stuck`/`Not Started`. Also confirmed every
item's board **group** matches its own **status label** with zero
exceptions (e.g. nothing is sitting in the `Ready` group with a `Blocked`
status label) — the board is internally self-consistent.

**To-Do group / "Set up PM" template item:** the doc's section 1 states
there is 1 non-managed template item (`Set up PM`) in `To-Do`, and that "the
sync must never touch items it does not own." Live check: **the `To-Do`
group currently has 0 items** — `Set up PM` is not present on the board
today (all 75 items are in `Ready`/`Locked`/`Blocked`/`Completed`; group
counts sum to exactly 75). Either it was already removed, or it never
existed as described. Not a blocker — just means the "never touch
unmanaged items" rule currently has nothing to apply to, until/unless
something new shows up in `To-Do` later.

---

## 4. Reconciliation: backlog.json (55) vs. board (75)

Matched 55/55 `backlog.json` items to a same-named board item — **0
backlog.json items are missing from the board.** Full coverage on that
direction.

### 4a. Board items with no backlog.json counterpart: 20

All 20 were checked against `backlog.json`'s `done` list for a
same-substance item under a different id — none found; this looks like a
genuinely disjoint set, not a renaming collision.

19 of the 20 sit in `Completed`/`Done`. Every one of them has real content
in the board's `Detail` (long_text) column — not blank placeholders — and
the text is recognizably the same rich, dated write-up style used in this
project's git history for Done entries (root cause, evidence, before/after
numbers). Spot-checked one against git log:
`down-ballot-election-recalibration` → commits `6321c5f` (the actual fix)
and `f33f9f7` (marked Done in `BACKLOG.md`), both real, both matching the
board's Detail text.

**Likely explanation** (worth stating plainly rather than leaving it a
mystery): `BACKLOG.md` used to be hand-maintained with this rich per-item
prose directly in its Done section. The monday board was seeded from that
version (doc section 1: "seeded earlier from BACKLOG.md (75 backlog
items)"). Since then, `checker.py`'s `write_markdown()` has taken over
`BACKLOG.md` generation completely and unconditionally — it renders *only*
from `backlog.json`'s `items` list, one terse table row each (id, action,
area — no rich prose). Any Done item that only ever existed in the old
hand-maintained `BACKLOG.md`, without a matching `backlog.json` entry, was
silently dropped from `BACKLOG.md` the next time `checker.py` ran, and never
had a `backlog.json` entry to begin with. Today's `BACKLOG.md` (93 lines, 55
rows total) confirms this — no rich prose survives there anymore. **The
monday board is currently the only place this history still exists in
readable form** (git diffs of old `BACKLOG.md` versions technically still
have it too, but not as a single readable per-item record).

Full list (19 Done + 1 Blocked):

| board item | group | has git history checked? |
|---|---|---|
| ci-kalshi-auth-env-2026-08 | Completed | matches known CI-fix work |
| db-audit-2026-08 | Completed | matches known audit work |
| down-ballot-election-recalibration | Completed | **verified**: `6321c5f`, `f33f9f7` |
| export-validation-pass-exclusion | Completed | plausible, not individually spot-checked |
| ext-signal-activation | Completed | plausible, not individually spot-checked |
| **graphify-skill-evaluation** | **Blocked** | plausible, not individually spot-checked |
| heuristic-backtest-tool | Completed | plausible, not individually spot-checked |
| heuristic_label-vs-base_rate-desync | Completed | plausible, not individually spot-checked |
| hurricane-recalibration | Completed | matches known recalibration work |
| log-pass-schema-parity | Completed | plausible, not individually spot-checked |
| near-dated-markets-supplement | Completed | plausible, not individually spot-checked |
| near-dated-window-chunking | Completed | matches known work |
| price-threshold-recalibration | Completed | matches known recalibration work |
| production-delivery-milestone-recalibration | Completed | matches known recalibration work |
| prop-market-skill-filter | Completed | plausible, not individually spot-checked |
| show-renewal-recalibration | Completed | plausible, not individually spot-checked |
| sports-award-recalibration | Completed | matches known recalibration work |
| subscriber-report-rework-2026-08 | Completed | matches known work |
| subscriber-report-wiring | Completed | plausible, not individually spot-checked |
| win-catchall-recalibration | Completed | plausible, not individually spot-checked |

Note `graphify-skill-evaluation` is the one non-Done item in this set — it
sits in `Blocked`, consistent with the same "evaluated, not building it yet"
item this session's own memory recalls from earlier `BACKLOG.md` content.

**Classification: legit-but-missing-from-json, all 20.** Nothing here looks
like stale/erroneous board cruft that should just be deleted. Per the
handoff's hard constraint #5, none of these have been touched.

### 4b. backlog.json items with a status/group mismatch on the board: 1

| backlog_id | backlog.json status | board group | board status label |
|---|---|---|---|
| `replay-instrument-validation` | `ready` | Blocked | Blocked |

This is different in kind from 4a — it's not an orphan, it's a real
`backlog.json` item whose board card is currently in the wrong place. This
is exactly what Phase 2's one-way push will silently correct on its first
live run (the engine is authoritative for gate status per the handoff's
design). Flagging it here so it isn't a surprise diff the first time
`--dry-run` is run — expect to see this item as a "would-move" +
"would-update status" in that output.

---

## 5. Recommended canonical item set

Per the handoff's own design principle (`backlog.json` stays the single
source of truth; monday is view + log only in v1): **the canonical set for
Phase 2 onward should be exactly `backlog.json`'s 55 items.** The 20 orphan
board items should not be force-migrated into `backlog.json` as part of
getting v1 shipped — that's real authoring work (each needs a valid `area`
from the enum, a `priority` 1-9, a `trigger`, etc.), not reconciliation, and
doing it under this handoff's own time pressure risks fabricating fields
(especially `priority` and `trigger`, which the old hand-written `BACKLOG.md`
entries didn't carry in a machine-parseable form) — which the handoff
explicitly prohibits ("Do not fabricate anything").

That leaves the 20 as **unmanaged-by-the-sync, left alone**, satisfying
constraint #5 by default (the sync only ever touches items matched by
`backlog_id`, and none of these 20 will get one). They stay visible on the
board as-is, with their real historical detail intact, just outside what
Phase 2+ actively manages.

---

## 6. Open decisions for Reed (Phase 0 gate — nothing below proceeds without sign-off)

1. **Confirm `backlog.json` as canonical**, and confirm the 20 orphan items
   should be left unmanaged on the board rather than backfilled into
   `backlog.json` now. (Recommended: yes to both — see section 5.) If you'd
   rather backfill some/all of them into `backlog.json` as real Done items
   first, that's a separate, bounded task I can scope before Phase 2 — just
   say which ones.
2. **`Completed date` column:** reuse the existing `Completed On`
   (`date_mm6agkax`) column, or create a new, separately-named one? Reusing
   avoids two near-duplicate date columns on the board.
3. **`Start Date` and `Timeline` columns:** confirmed unused by anything in
   this spec — leave alone, no action needed, just flagging that they exist
   so nothing later confuses them for a sync target.
4. Nothing needs a decision for the `replay-instrument-validation` mismatch
   (section 4b) — it will self-correct on the first live Phase 2 run. Listed
   for visibility only.

No monday writes have been made. `.env` now has `MONDAY_API_TOKEN` (not
committed — confirmed `.gitignore` covers `.env`, confirmed `git status`
shows no tracked change).
