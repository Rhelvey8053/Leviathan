"""
tests/test_backlog.py - Offline tests for backlog/backlog.json and backlog/engine.py.

All tests operate on a temp copy of backlog.json; the real file is never mutated.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
BACKLOG_JSON = ROOT / "backlog" / "backlog.json"
BACKLOG_PY = ROOT / "backlog" / "engine.py"

sys.path.insert(0, str(ROOT))
from backlog.engine import (
    determine_status,
    load_backlog,
    parse_trigger,
    save_backlog,
    validate_item,
)


@pytest.fixture()
def backlog_data():
    return load_backlog(BACKLOG_JSON)


@pytest.fixture()
def tmp_backlog(tmp_path):
    dest = tmp_path / "backlog.json"
    shutil.copy(BACKLOG_JSON, dest)
    return dest


# ---------------------------------------------------------------------------
# backlog.json structure
# ---------------------------------------------------------------------------

def test_parses_and_96_items(backlog_data):
    """
    95, not 94: smart-money-fills-persistence-build added 2026-08-26,
    split out of smart-money-fills-table-missing (flipped to done the
    same day). That item only fixed the SILENT-FAILURE visibility problem
    (backlog.checker.compute_metrics() now flags a missing table via a
    new _data_gaps list instead of a bare misleading 0) -- it never built
    the actual smart_money_fills persistence table or a fills-writing
    pipeline, which is a genuinely separate, larger feature. Split out
    rather than left open indefinitely on the original item. Priority 4,
    deliberately not urgent -- the three items it would unblock are all
    still separately far from their own resolved_count_per_wallet_max>=10
    threshold regardless of whether this table exists.

    Still 94 (no new item) as of 2026-08-26: empirical-base-rates-poly
    reopened done -> ready. Marked done with no verification trail, and
    Liam's 2026-08-20 report independently raised the same doubt ("confirm
    this is wired into live scoring, not just a research artifact").
    Checked directly: backtesting/base_rates.py is an explicitly-labeled
    scaffold ("No live data fetch -- receives data from the backtest
    harness"), imported ONLY by its own test file -- core/scanner.py's
    live estimate_base_rate() still runs the original static heuristic
    table this item's action text says it replaces. A real status-tracking
    mistake, not just a stale finding.

    94, not 93: signal-category-mostly-blank-despite-real-data added
    2026-08-25, found while adding a win-rate-by-category chart to the
    dashboard. Only 4/51 real bets have a populated category despite
    Kalshi actually having the data -- confirmed live by re-fetching a
    blank row's event (KXAPRPOTUS-26AUG21-39.2) directly via
    fetch_event_detail() and getting category='Politics' back with no
    error. Two concrete gaps found (main.py's /markets fallback path
    attaches no category at all; fetch_near_dated_markets's per-event
    backfill silently swallows failures with no logging), not fully
    root-caused to one cause. Historical blanks can't be backfilled --
    Kalshi 404s expired events -- so this is about stopping new bets
    from landing blank going forward, not fixing the past.

    93, not 92: metaculus-community-prediction-inaccessible added
    2026-08-25 (done). Set up the previously-dormant Metaculus
    integration (missing token) at the owner's request -- got a real
    free token, confirmed auth works, but the probability data
    _extract_probability() needs doesn't exist under the field name the
    code looks for, and even the new nested field
    (aggregations.unweighted.latest) came back null on every one of 50
    tested questions. Likely gated behind a higher access tier than a
    free personal token grants. Owner decision: leave as a documented
    dead end (token/config stay in place, code untouched) rather than
    patch to a field that's null anyway, pursue research-partner access,
    or rip the integration out.

    92, not 91: cftc-rule-40-11-event-contract-rulemaking added 2026-08-25,
    found via direct research (CFTC.gov, Federal Register, legal-firm
    summary of the actual NPRM -- not secondary news) prompted by
    expanding Liam's regulatory-research scope the same day. CFTC issued
    an NPRM 2026-06-10 substantially revising Rule 40.11's "contrary to
    public interest" test for event contracts; the 45-day comment period
    already closed 2026-07-27 with no final rule yet. Distinct from
    cross-venue-expansion's WA-geofencing finding -- this is federal and
    category-wide (affects which markets can exist at all), not a single
    state's jurisdiction-specific action against one operator.

    91, not 90: subscriber-report-removed-2026-08 added 2026-08-25 (done),
    recording the day's removal of the entire subscriber-report feature --
    triggered by the user asking why send picks to subscribers instead of
    trading a proven strategy directly. Investigation found neither usual
    justification held: no bankroll/position-limit config exists anywhere
    in the codebase, subscribers.json never existed (zero real
    subscribers), and Leviathan-SubscriberReport only ever rendered a
    local HTML preview -- no email was ever sent externally. Also flips
    subscriber-hosting-billing-decision to done (superseded, not answered
    -- the product itself was removed rather than given a hosting/billing
    path).

    90, not 89: dailyrun-logontype-interactive added 2026-08-25, found
    while re-registering Leviathan-DailyRun for a RestartCount change --
    it's still LogonType Interactive (can't run if the account is fully
    logged off, only if merely asleep/locked), unlike every other
    Leviathan-* task, which is S4U. SubscriberReport and WeeklyAudit had
    this exact gap flagged in a 2026-08-17 audit and were since fixed;
    DailyRun is the one that didn't get swept up in that fix. Priority 3,
    ready -- deliberately not auto-fixed inline since a logon-type change
    affects credential/session behavior and warrants its own go-ahead.

    89, not 88: smart-money-fills-table-missing added 2026-08-25, found by
    weekly_code_audit.py's live verification run -- resolved_count_per_
    wallet_max silently pins at 0 because the smart_money_fills table it
    queries doesn't exist in the live DB, so any item gated on that metric
    can never unlock. fix-weekly-code-audit-timeout flipped to done the
    same day: its TimeoutExpired crash was root-caused and fixed (commit
    1f7f78e), and a second bug the fix's own verification run surfaced
    (Write(reports/code_audits/*.md) silently denied by the permission
    system) was fixed alongside it (commit a3383f9).

    88, not 82: six items added 2026-08-24 from the solo-operator
    automation-research pass. Four shipped-and-done: automation-health-
    monitoring, daily-operations-digest, dependabot-setup, and
    wake-triggered-task-catchup (the last built directly off a real
    same-day incident -- the machine slept through Leviathan-DailyRun's
    6am trigger and Task Scheduler never caught it up despite
    StartWhenAvailable=True). task-scheduler-manual-trigger-stuck-queued
    stays ready: manually-triggered Start-ScheduledTask calls getting
    stuck "Queued" forever on this machine, reproduced on both a
    newly-registered task and the long-established Leviathan-Heartbeat
    task -- root cause not yet identified).

    82, not 81: polymarket-data-api-rate-limit-pacing added 2026-08-23
    (priority 2). User shared Polymarket's own API docs; live-fetching the
    linked rate-limits page revealed a real root cause for a pattern seen
    in every pipeline run this session -- dozens of wallets excluded with
    "no positions returned from API" and data/winning_accounts.json stuck
    at 0 cached winners. sources/accounts.py's _get() has a bare
    "except Exception: return None" indistinguishable from a genuinely
    empty result, and zero pacing between calls; discover_winners() and
    analysis/smart_money_scan.py's watchlist scan both hammer the Data
    API's /positions endpoint (documented cap: 150 req/10s, IP-based,
    over-limit requests throttled/queued not rejected) with no delay,
    plausibly timing out past _get()'s timeout=12 and getting silently
    swallowed. Fix scoped but not yet built: pacing + real-error
    visibility, not implemented this session (user was near usage limit).

    81, not 76: five items added 2026-08-22 at the end of the same session,
    turning open threads from the day's work into tracked, "ready" backlog
    items rather than letting them live only in conversation history --
    kalshi-wa-geofencing-exposure-check (priority 2, time-sensitive:
    confirmed via live web search that Kalshi's Washington geofencing
    deadline is 2026-09-02), market-price-divergence-tracking (priority 3,
    instrumentation follow-up on the finding that market_baseline_brier
    beat the scorer's own brier_score over the 14 resolved signals),
    rolled-market-repeat-detection (priority 4, the KXCABLEAVE
    same-story-three-expiry-windows finding), verify-liam-post-context-doc-
    alignment (priority 4, checks whether Liam's next report still makes
    the depends_on/trigger mistake now that live gate data is on the
    board), and subscriber-hosting-billing-decision (priority 5, the
    build-vs-buy fork the user explicitly sidelined this session in favor
    of the token-usage-reduction work).

    76, not 75: smart-money-discovery-dashboard added 2026-08-22, in
    response to the user asking whether any dashboard insight for
    winning-trader/whale detection was possible despite the project's low
    resolved-bet sample size. Status "ready" (no trigger, no depends_on) --
    unlike wallet-tracking-dashboard (blocked on
    resolved_count_per_wallet_max >= 10), this surfaces two data sources
    that were already fully computed but never wired into the dashboard:
    sources/accounts.py's diagnose_discovery() funnel diagnostic over live
    Polymarket wallet data, and core/whales.py's whale-direction streak
    tracker (data/whale_history/streak.json) -- both independent of
    Leviathan's own resolved_count.

    75, not 74: graphify-skill-evaluation re-added 2026-08-19, restoring the
    item the 74-count paragraph below describes dropping on 2026-08-17. The
    schema gap that caused that drop (no way to encode "blocked on an
    external/subjective condition" without an empty depends_on/trigger
    vacuously satisfying evaluate_triggers() and auto-promoting it to
    ready) is now closed the same way it was closed for
    replay-instrument-validation this session: a permanent sentinel
    trigger metric (graphify_corpus_shape_changed) that
    backlog.checker.compute_metrics() never populates, so the exact
    auto-promotion bug that forced the original drop can't recur. This is
    a fix to the schema gap, not a reversal of the original decision on
    the item's merits -- action text is the full, untruncated evaluation
    recovered from commit 1a683a236 (the monday board's own copy has been
    truncated mid-sentence since it was first seeded), and status is
    restored to "blocked", matching both the item's original Phase 0
    backfill and its current, independently-unchanged board state.

    74, not 75: 19 of the 20 items backfilled during the monday.com sync
    Phase 0 discovery (2026-08-17, docs/monday_sync_discovery.md) stuck --
    graphify-skill-evaluation did not. It was backfilled with status
    "blocked" but depends_on=[] (blocked on an external condition -- the
    project's shape changing to include non-code files -- not on another
    backlog item, which the schema has no field for). backlog/checker.py's
    own evaluate_triggers()/compare_statuses() logic treats "blocked" as
    meaning "depends_on is non-empty"; with it empty, trigger_ok and
    deps_ok both come back vacuously True and the very next checker.py run
    auto-promoted it to "ready" -- caught live during the monday-sync
    Phase 2 dry-run-then-live workflow (the promotion actually happened
    against the real backlog.json before this was caught). Per Reed's
    explicit decision once this schema gap was surfaced: dropped back to
    unmanaged rather than mis-representing it as "ready" (it was
    evaluated and declined, not actionable) or inventing a fake
    depends_on. Its board card already correctly shows Blocked from the
    original one-time seed and is simply left alone by the sync (no
    backlog_id match) -- restores the same state graphify-skill-evaluation
    was recommended for in the original Phase 0 discovery doc, before the
    all-20 backfill decision.

    75, not 55: the monday.com sync Phase 0 discovery (2026-08-17,
    docs/monday_sync_discovery.md) found the monday board -- seeded early
    on from a version of BACKLOG.md that still carried rich hand-written
    Done-entry prose directly -- had 20 items with no backlog.json
    counterpart at all (ci-kalshi-auth-env-2026-08, db-audit-2026-08,
    down-ballot-election-recalibration, export-validation-pass-exclusion,
    ext-signal-activation, graphify-skill-evaluation, heuristic-backtest-tool,
    heuristic_label-vs-base_rate-desync, hurricane-recalibration,
    log-pass-schema-parity, near-dated-markets-supplement,
    near-dated-window-chunking, price-threshold-recalibration,
    production-delivery-milestone-recalibration, prop-market-skill-filter,
    show-renewal-recalibration, sports-award-recalibration,
    subscriber-report-rework-2026-08, subscriber-report-wiring,
    win-catchall-recalibration) -- the same gap the 43->55 fix (below)
    closed for 2 items, just 20 more of them, orphaned once
    backlog/checker.py's generate_markdown() took over rendering BACKLOG.md
    purely from backlog.json and stopped preserving any hand-written entry
    that never had a matching json item. Backfilled from git history (each
    item's original BACKLOG.md-addition commit, not the monday board's own
    Detail column, which turned out to already be truncated to ~450 chars
    at seed time) rather than fabricated -- every action/area/priority
    value traces to a real commit, cross-checked against what the board
    had independently captured at seed time (matched on all 20). Per Reed's
    explicit decision on the monday-sync handoff (backfill now, not leave
    unmanaged), gated per that doc's own Phase 0 sign-off requirement.

    55, not the original 43: kalshi-sdk-migration-implementation and
    kalshi-sdk-evaluation-2026-08 (2026-08-04) were both real, completed
    Done items that had only ever been hand-added to BACKLOG.md directly,
    never to backlog.json -- added here to close that gap so a future
    backlog/checker.py regenerate of BACKLOG.md doesn't silently delete
    their writeups (see also the brier-tracking/confluence-detection/
    multi-sample-scoring status reconciliation in the same commit).
    whale-flag-lv-guarantee, whale-only-none-direction-crash,
    resolved-count-metric-desync, whale-actionability-scorecard, and
    signal-csv-strategy-review-2026-08 (2026-08-04 through 2026-08-16,
    same-project follow-ups) were each added to both files together from
    the start, avoiding a repeat of the same gap. per-heuristic-scorecard
    and heuristic-sunsetting flipped locked->ready in the same pass
    (resolved_count_per_category_max cleared 15) without changing the
    total item count. streamlit-dashboard-2026-08 and
    signal-scan-log-split-2026-08 (2026-08-16) shipped as commits without
    a backlog entry at the time -- found and closed during a full-project
    audit the next day (2026-08-17), same gap pattern as the original 43->50 fix.
    citations-provenance-grounding, net-edge-fee-depth-model, and
    cross-venue-expansion (2026-08-17) came from a roadmap-reconciliation
    handoff -- two of four originally-proposed items were added (one
    narrowed after verifying net_edge's real fee handling first), one was
    dropped as already-implemented (scorer-websearch-grounding: web search
    is already live on both scoring backends), matching this file's own
    "verify before adding" precedent.

    96, not 95: resolved-count-per-category-max-wrong-column (2026-08-27)
    added after discovering resolved_count_per_category_max is computed by
    `GROUP BY flag_path` (~5 coarse buckets) despite its own glossary entry
    and its two done-item consumers (per-heuristic-scorecard,
    heuristic-sunsetting -- see the note two paragraphs up: both unlocked
    specifically because this metric cleared 15) meaning the much finer
    per-heuristic_label grouping. Real per-label max checked live the same
    day via core.logger.get_stats_by_heuristic_label(): 2, not 86. Filed
    rather than fixed immediately since it touches two already-done items'
    validity, not just a live gate.

    97, not 96: replay-runner-crash-on-malformed-cli-response (2026-08-28)
    added and immediately closed done -- a real corpus-build batch crash
    (core.scorer._score_via_cli returning the wrong type on a malformed
    CLI response, and backtesting.replay_runner.run_replay()'s loop having
    no isolation against it) found, fixed, and tested the same day.

    100, not 97: three items added 2026-08-30 investigating that day's
    silently-missed DailyRun. dailyrun-missed-run-2026-08-30-silent-
    failure-gaps and weekly-code-audit-exit-code-not-proof-of-report were
    both found and fixed the same day (main.py's silent auth-failure path,
    schedule_setup.ps1's missing output capture/buffering, and
    weekly_code_audit.py's untrusted exit-0 assumption). windows-defender-
    cpu-contention-2026-08-30 was filed but NOT fixed -- a real system-
    resource finding (Defender + Docker consuming heavy CPU, likely
    explaining the same day's garbled CodeAudit output and WeeklyAudit's
    own reported slowness) that's outside what should be changed
    autonomously; left ready for the user's own decision.

    101, not 100: monday-com-retired-backlog-dashboard-page (2026-08-30)
    added and closed done the same day -- the user's monday.com trial
    expired, so backlog browsing moved to a new dashboard/pages/5_Backlog.py
    page (reads backlog.json live, no sync step) and scripts/monday_sync.py
    was marked retired in its own docstring.

    103, not 101: resolve-first-top-n-per-bucket and
    near-dated-fetch-headroom-increase (2026-08-31) added and closed done
    the same day -- user asked to expand scope on active bets to collect
    resolved_count data faster without breaking core practices.
    select_near_dated() gained a picks_per_bucket parameter (default 1,
    unchanged prior behavior) live-verified to raise daily picks 5->13
    against real data; the shared near-dated fetch's target_count/max_pages
    were raised 200/30->300/40, live-verified at 1.6s for a full 300-market
    fetch. Both evidence-gated per this project's own discipline, not
    vibes-based config bumps.

    105, not 103: wire-llm-model-cli-flag and trial-stronger-model-main-scoring
    (2026-08-31) -- the "use higher models" half of the same user request.
    Fixed core.scorer._score_via_cli() never passing --model to the claude
    CLI subprocess (config.llm.model was dead for the live pipeline); added
    a new llm.cli_model_override key (default null, no behavior change) as
    a bug fix. Actually trialing a stronger model is logged as a separate,
    ready-not-executed item -- a heavier model consumes more Pro-plan
    usage/session budget per call, a real trade-off against "faster" that
    needed its own explicit decision point rather than a bundled default.
    """
    assert len(backlog_data["items"]) == 105


def test_all_ids_unique(backlog_data):
    ids = [i["id"] for i in backlog_data["items"]]
    assert len(ids) == len(set(ids))


def test_every_item_valid(backlog_data):
    items = backlog_data["items"]
    glossary = backlog_data["metrics_glossary"]
    for item in items:
        others = [i for i in items if i["id"] != item["id"]]
        errs = validate_item(item, others, glossary)
        assert errs == [], f"Item {item['id']!r} failed: {errs}"


def test_every_depends_on_references_real_id(backlog_data):
    ids = {i["id"] for i in backlog_data["items"]}
    for item in backlog_data["items"]:
        for dep in item.get("depends_on", []):
            assert dep in ids, f"{item['id']} depends_on unknown id {dep!r}"


def test_every_trigger_metric_in_glossary(backlog_data):
    glossary = backlog_data["metrics_glossary"]
    for item in backlog_data["items"]:
        for cond in item.get("trigger", {}).get("all", []):
            assert cond["metric"] in glossary, (
                f"{item['id']} uses unknown metric {cond['metric']!r}"
            )


def test_item21_has_2_triggers_and_2_depends(backlog_data):
    item21 = next(i for i in backlog_data["items"] if i["id"] == "auto-calibration-loop")
    assert len(item21["trigger"]["all"]) == 2
    assert len(item21["depends_on"]) == 2


# ---------------------------------------------------------------------------
# parse_trigger
# ---------------------------------------------------------------------------

def test_parse_trigger_manual():
    assert parse_trigger("manual") == {"all": []}


def test_parse_trigger_empty_string():
    assert parse_trigger("") == {"all": []}


def test_parse_trigger_two_conditions():
    result = parse_trigger("resolved_count>=30,resolved_count_per_category_max>=15")
    conds = result["all"]
    assert len(conds) == 2
    assert conds[0] == {"metric": "resolved_count", "op": ">=", "value": 30}
    assert conds[1] == {"metric": "resolved_count_per_category_max", "op": ">=", "value": 15}
    assert isinstance(conds[0]["value"], int)
    assert isinstance(conds[1]["value"], int)


def test_parse_trigger_invalid_raises():
    with pytest.raises(ValueError):
        parse_trigger("bad condition")


# ---------------------------------------------------------------------------
# determine_status
# ---------------------------------------------------------------------------

def test_determine_status_ready():
    assert determine_status({"all": []}, []) == "ready"


def test_determine_status_locked():
    trigger = parse_trigger("resolved_count>=25")
    assert determine_status(trigger, []) == "locked"


def test_determine_status_blocked_any_deps():
    assert determine_status({"all": []}, ["some-id"]) == "blocked"


def test_determine_status_blocked_overrides_trigger():
    trigger = parse_trigger("resolved_count>=25")
    assert determine_status(trigger, ["some-id"]) == "blocked"


# ---------------------------------------------------------------------------
# add subcommand via tmp file
# ---------------------------------------------------------------------------

def _run_add(tmp_backlog, extra_args):
    return subprocess.run(
        [sys.executable, str(BACKLOG_PY), "--file", str(tmp_backlog), "add"] + extra_args,
        capture_output=True, text=True,
    )


def test_add_valid_manual_item(tmp_backlog):
    before = load_backlog(tmp_backlog)
    count_before = len(before["items"])

    result = _run_add(tmp_backlog, [
        "--id", "new-manual-item",
        "--title", "New Manual",
        "--area", "validation",
        "--priority", "3",
        "--action", "Do something manually.",
    ])
    assert result.returncode == 0, result.stderr

    after = load_backlog(tmp_backlog)
    assert len(after["items"]) == count_before + 1
    added = next(i for i in after["items"] if i["id"] == "new-manual-item")
    assert added["status"] == "ready"


def test_add_data_trigger_gives_locked(tmp_backlog):
    result = _run_add(tmp_backlog, [
        "--id", "trigger-item",
        "--title", "Triggered",
        "--area", "calibration",
        "--priority", "4",
        "--action", "Run after threshold.",
        "--trigger", "resolved_count>=40",
    ])
    assert result.returncode == 0, result.stderr
    after = load_backlog(tmp_backlog)
    added = next(i for i in after["items"] if i["id"] == "trigger-item")
    assert added["status"] == "locked"


def test_add_depends_on_real_id_gives_blocked(tmp_backlog):
    result = _run_add(tmp_backlog, [
        "--id", "dep-item",
        "--title", "Dependent",
        "--area", "backtesting",
        "--priority", "5",
        "--action", "Runs after backtest-harness.",
        "--depends-on", "backtest-harness",
    ])
    assert result.returncode == 0, result.stderr
    after = load_backlog(tmp_backlog)
    added = next(i for i in after["items"] if i["id"] == "dep-item")
    assert added["status"] == "blocked"


def test_add_duplicate_id_rejected(tmp_backlog):
    before_count = len(load_backlog(tmp_backlog)["items"])
    result = _run_add(tmp_backlog, [
        "--id", "trade-reconciliation",
        "--title", "Dup",
        "--area", "execution",
        "--priority", "1",
        "--action", "Duplicate.",
    ])
    assert result.returncode != 0
    assert len(load_backlog(tmp_backlog)["items"]) == before_count


def test_add_bad_area_rejected(tmp_backlog):
    before_count = len(load_backlog(tmp_backlog)["items"])
    result = _run_add(tmp_backlog, [
        "--id", "bad-area-item",
        "--title", "Bad Area",
        "--area", "nonexistent-area",
        "--priority", "3",
        "--action", "Something.",
    ])
    assert result.returncode != 0
    assert len(load_backlog(tmp_backlog)["items"]) == before_count


def test_add_trigger_metric_not_in_glossary_rejected(tmp_backlog):
    before_count = len(load_backlog(tmp_backlog)["items"])
    result = _run_add(tmp_backlog, [
        "--id", "bad-metric-item",
        "--title", "Bad Metric",
        "--area", "validation",
        "--priority", "3",
        "--action", "Something.",
        "--trigger", "unknown_metric>=10",
    ])
    assert result.returncode != 0
    assert len(load_backlog(tmp_backlog)["items"]) == before_count


def test_add_depends_on_missing_id_rejected(tmp_backlog):
    before_count = len(load_backlog(tmp_backlog)["items"])
    result = _run_add(tmp_backlog, [
        "--id", "missing-dep-item",
        "--title", "Missing Dep",
        "--area", "execution",
        "--priority", "3",
        "--action", "Something.",
        "--depends-on", "nonexistent-id",
    ])
    assert result.returncode != 0
    assert len(load_backlog(tmp_backlog)["items"]) == before_count


# ---------------------------------------------------------------------------
# status subcommand
# ---------------------------------------------------------------------------

def test_status_exits_zero():
    result = subprocess.run(
        [sys.executable, str(BACKLOG_PY), "status"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
