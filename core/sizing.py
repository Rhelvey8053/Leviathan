"""
core/sizing.py — confidence-weighted hypothetical stake sizing.

Built in response to "bet more on markets we score higher" (2026-07-27).
A real counterfactual run against the 8 resolved paper signals at the time
showed edge-magnitude sizing would have made hypothetical P&L roughly 4x
WORSE (the single largest-edge call was also the single worst miss), and
confidence-tier sizing only looked better because n=2 HIGH-confidence
signals both happened to win -- not a real sample. There's already a
backlog item for this class of change, auto-calibration-loop ("adjust
heuristic confidence weights based on tracked Brier scores and category
win rates"), deliberately `blocked` until resolved_count>=30 AND
resolved_count_per_category_max>=15 -- the same numbers are mirrored here
in MIN_RESOLVED_COUNT/MIN_RESOLVED_PER_CATEGORY (update both places
together if that item's trigger ever changes).

This module is infrastructure only: wired up and correct, but structurally
unable to change any real number until the live DB metrics clear the same
gate, regardless of config. is_dynamic_sizing_eligible() re-checks the
live DB on every call rather than caching, so it can never report stale
eligibility from an earlier, thinner dataset.

compute_stake_size() is called from core.logger.resolve_outcomes() and
persisted to signals.stake_size_hypothetical -- a NEW, SEPARATE column,
never used in place of the existing pnl_if_traded/unit_size-based
headline P&L anywhere. Comparing what P&L would have been under dynamic
sizing is analysis/dynamic_sizing_preview.py's job, kept deliberately
apart from analysis/calibration.py and the README's headline figures --
same "separate table/column, never pooled" discipline used elsewhere in
this codebase (replay_signals vs. signals, blind_scores vs. signals).
"""

from backlog.checker import compute_metrics, DEFAULT_DB

# Mirrors backlog/backlog.json's auto-calibration-loop trigger exactly --
# this is the same class of change (weighting behavior by tracked
# confidence/category performance), so it needs the same sample-size floor
# before it's safe, not a separately-chosen number.
MIN_RESOLVED_COUNT        = 30
MIN_RESOLVED_PER_CATEGORY = 15

# Config-driven so it can be tuned later without a code change (same
# pattern as betting.min_ev_pct_of_unit) -- NOT reverse-engineered to fit
# any particular historical sample; a defensible starting point only,
# meant to be revisited once real data actually supports validating it.
DEFAULT_CONFIDENCE_MULTIPLIERS = {"HIGH": 1.5, "MED": 1.0, "LOW": 0.5}


def is_dynamic_sizing_eligible(config: dict, db_path=DEFAULT_DB) -> bool:
    """
    True only if BOTH the live DB metrics clear the same threshold as
    auto-calibration-loop AND the human has separately opted in via
    config.betting.dynamic_sizing_enabled. Re-reads the DB every call --
    never cached -- so eligibility can't go stale as more signals resolve,
    and a human flipping the config flag early can't activate this before
    the data actually supports it.
    """
    if not config.get("betting", {}).get("dynamic_sizing_enabled", False):
        return False
    metrics = compute_metrics(db_path=db_path)
    return (
        metrics.get("resolved_count", 0) >= MIN_RESOLVED_COUNT
        and metrics.get("resolved_count_per_category_max", 0) >= MIN_RESOLVED_PER_CATEGORY
    )


def compute_stake_multiplier(confidence: str, config: dict) -> float:
    """
    Multiplier on config.betting.unit_size for a given confidence tier.
    Falls back to 1.0 (flat) for an unrecognized/blank confidence value --
    never silently reduces or inflates a stake it doesn't understand.
    """
    table = config.get("betting", {}).get(
        "confidence_stake_multipliers", DEFAULT_CONFIDENCE_MULTIPLIERS
    )
    return float(table.get((confidence or "").upper(), 1.0))


def compute_stake_size(signal: dict, config: dict, db_path=DEFAULT_DB) -> float:
    """
    Hypothetical dollar stake for one resolved signal. Returns the flat
    config.betting.unit_size unless is_dynamic_sizing_eligible() is True,
    in which case it scales by compute_stake_multiplier(). This is the
    single call site resolve_outcomes() uses to populate
    stake_size_hypothetical -- callers never need to check eligibility
    themselves.
    """
    unit_size = float(config.get("betting", {}).get("unit_size", 10))
    if not is_dynamic_sizing_eligible(config, db_path=db_path):
        return unit_size
    return round(unit_size * compute_stake_multiplier(signal.get("confidence", ""), config), 4)
