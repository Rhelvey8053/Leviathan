"""
analysis/auto_calibration_preview.py — SHADOW MODE preview of the
auto-calibration-loop backlog item ("adjust heuristic confidence weights
based on tracked Brier scores and category win rates").

Computes what per-heuristic_label confidence multipliers real tracked
performance WOULD suggest, and what hypothetical P&L delta applying them
retroactively would have produced -- without writing anything back to
live scoring. core/scanner.py's _HEURISTIC_RULES table and core/sizing.py's
confidence-tier multipliers are both untouched by this script; it is
read-only analysis, mirroring analysis/dynamic_sizing_preview.py's own
"preview, not the headline track record" discipline (same two-totals-
side-by-side shape, same "not eligible yet" honesty when the gate hasn't
cleared).

Per-label multiplier is centered on the Wilson 95% CI bound CLOSER to the
neutral 50% baseline (lower bound when win_rate>=50, upper bound when
win_rate<50) rather than the raw point estimate. This is a conservative
shrinkage estimator: at low n the interval is wide, so the chosen bound
sits close to 50% and the suggested multiplier is muted automatically --
no separate hand-tuned n-based damping rule is needed on top of it. Labels
below MIN_RESOLVED_PER_CATEGORY get no suggestion (multiplier 1.0) at all,
matching the same per-category floor auto-calibration-loop's own trigger
uses (core.sizing.MIN_RESOLVED_PER_CATEGORY).

This script writes nothing. It never touches core/scanner.py or
core/sizing.py. Promoting any of this to live scoring is a distinct,
separate, human-authorized decision -- not a side effect of running this.

Usage:
    python analysis/auto_calibration_preview.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import logger, sizing
from core.report import _wilson_interval
from backlog.checker import compute_metrics, DEFAULT_DB

W = 70

MULTIPLIER_FLOOR = 0.7
MULTIPLIER_CEIL = 1.3


def _load_config() -> dict:
    cfg_path = os.path.join(ROOT, "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def compute_calibration_weights(label_stats: list[dict]) -> list[dict]:
    """
    Pure function (no DB/network access) -- unit-testable.
    label_stats: from core.logger.get_stats_by_heuristic_label(), each with
    heuristic_label/total/wins/win_rate/total_pnl/avg_edge.
    Returns the same rows augmented with wilson_lower/wilson_upper/
    suggested_multiplier/note. Never mutates the input.
    """
    out = []
    for row in label_stats:
        total = row.get("total") or 0
        win_rate = row.get("win_rate")
        r = dict(row)
        if total < sizing.MIN_RESOLVED_PER_CATEGORY or win_rate is None:
            r.update(
                wilson_lower=None, wilson_upper=None, suggested_multiplier=1.0,
                note=f"n={total} below per-category floor "
                     f"({sizing.MIN_RESOLVED_PER_CATEGORY}) -- no adjustment",
            )
            out.append(r)
            continue
        lower, upper = _wilson_interval(win_rate, total)
        # Clamp the chosen bound at 0.5 so the multiplier can never land on
        # the wrong side of neutral -- a CI wide enough to straddle 50% (a
        # weak point estimate with an upper bound above half, or a strong
        # one with a lower bound below half) means "not enough confidence
        # to move either direction," not "move the opposite way."
        bound = max(lower, 0.5) if win_rate >= 50 else min(upper, 0.5)
        raw = 1.0 + (bound - 0.5)
        multiplier = max(MULTIPLIER_FLOOR, min(MULTIPLIER_CEIL, raw))
        r.update(
            wilson_lower=round(lower, 4), wilson_upper=round(upper, 4),
            suggested_multiplier=round(multiplier, 3),
            note=f"n={total}, win_rate={win_rate:.1f}%, 95% CI [{lower:.1%}, {upper:.1%}]",
        )
        out.append(r)
    return out


def compute_preview(rows: list[dict], weighted_labels: list[dict]) -> dict:
    """
    Pure function. rows: from core.logger.get_resolved_track_record() --
    must have heuristic_label, pnl_if_traded, stake_size_hypothetical.
    weighted_labels: from compute_calibration_weights().
    Returns baseline vs calibrated totals, applying each row's label
    multiplier on top of its EXISTING stake_size_hypothetical (so this
    composes with dynamic sizing rather than overriding it) -- unlabeled
    rows or rows whose label isn't in weighted_labels fall back to 1.0.
    """
    mult_by_label = {r["heuristic_label"]: r["suggested_multiplier"] for r in weighted_labels}
    baseline_total = 0.0
    calibrated_total = 0.0
    n = 0
    for row in rows:
        pnl = row.get("pnl_if_traded")
        if pnl is None:
            continue
        pnl = float(pnl)
        n += 1
        stake = row.get("stake_size_hypothetical")
        stake = float(stake) if stake is not None else 1.0
        baseline_total += pnl * stake
        mult = mult_by_label.get(row.get("heuristic_label"), 1.0)
        calibrated_total += pnl * stake * mult

    return {
        "n": n,
        "baseline_total": round(baseline_total, 2),
        "calibrated_total": round(calibrated_total, 2),
        "delta": round(calibrated_total - baseline_total, 2),
    }


def _live_metrics_gate_clear(db_path=DEFAULT_DB) -> bool:
    """
    Same resolved_count/per_category thresholds as auto-calibration-loop's
    own backlog trigger and core.sizing's MIN_RESOLVED_COUNT/
    MIN_RESOLVED_PER_CATEGORY -- deliberately NOT reusing
    sizing.is_dynamic_sizing_eligible(), which also checks
    config.betting.dynamic_sizing_enabled, a different feature's own
    separate human opt-in, not this one's.
    """
    metrics = compute_metrics(db_path=db_path)
    return (
        metrics.get("resolved_count", 0) >= sizing.MIN_RESOLVED_COUNT
        and metrics.get("resolved_count_per_category_max", 0) >= sizing.MIN_RESOLVED_PER_CATEGORY
    )


def main():
    label_stats = logger.get_stats_by_heuristic_label()
    weighted = compute_calibration_weights(label_stats)
    rows = logger.get_resolved_track_record()
    result = compute_preview(rows, weighted)
    gate_clear = _live_metrics_gate_clear()

    print("=" * W)
    print("AUTO-CALIBRATION PREVIEW -- shadow mode, nothing applied live")
    print("-" * W)
    print(f"  Resolved signals (n):         {result['n']}")
    print(f"  Live metrics gate clear:      {gate_clear}")
    print(f"  Gate: resolved_count >= {sizing.MIN_RESOLVED_COUNT} AND "
          f"resolved_count_per_category_max >= {sizing.MIN_RESOLVED_PER_CATEGORY}")
    print("-" * W)
    print("  Per-heuristic-label suggested multipliers:")
    for r in weighted:
        label = r.get("heuristic_label") or "UNKNOWN"
        print(f"    {label:<28} x{r['suggested_multiplier']:.3f}   {r['note']}")
    print("-" * W)
    print(f"  Baseline hypothetical P&L (existing sizing):  ${result['baseline_total']:+.2f}")
    print(f"  Calibrated hypothetical P&L (if applied):      ${result['calibrated_total']:+.2f}")
    print(f"  Delta:                                         ${result['delta']:+.2f}")
    if not gate_clear:
        print("-" * W)
        print("  Gate not clear yet -- suggestions above are informational only;")
        print("  none of this is wired into live scoring regardless of gate state.")
    print("=" * W)


if __name__ == "__main__":
    main()
