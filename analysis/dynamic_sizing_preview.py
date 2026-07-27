"""
analysis/dynamic_sizing_preview.py — preview of confidence-weighted sizing.

NOT the headline track record. This compares the existing flat-$unit_size
hypothetical P&L (the only figures cited anywhere as the real track record
— see README) against what P&L WOULD have been under core.sizing's
confidence-weighted stake sizing, using the same resolved signals. Kept
deliberately separate from analysis/calibration.py, the same "never pool
a different measurement with the headline figure" discipline this
codebase uses elsewhere (replay_signals vs. signals, blind_scores vs.
signals).

core.sizing.is_dynamic_sizing_eligible() gates the real
stake_size_hypothetical column itself (see core/logger.py resolve_outcomes)
on live DB metrics matching backlog.json's auto-calibration-loop trigger
(resolved_count>=30 AND resolved_count_per_category_max>=15) AND a human
opt-in (config.betting.dynamic_sizing_enabled). Below that gate,
stake_size_hypothetical already equals the flat unit_size for every row,
so this script's two totals will be identical -- that's not a bug, it's
confirmation nothing has silently activated early.

Usage:
    python analysis/dynamic_sizing_preview.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import logger, sizing

W = 70


def _load_config() -> dict:
    cfg_path = os.path.join(ROOT, "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def compute_preview(rows: list[dict], config: dict) -> dict:
    """
    Pure function (no DB/network access) so it's directly unit-testable.
    rows: from core.logger.get_resolved_track_record() -- must have
    pnl_if_traded, stake_size_hypothetical, confidence, result.
    Returns totals and per-row deltas for both sizing schemes.
    """
    unit_size = float(config.get("betting", {}).get("unit_size", 10))
    flat_total = 0.0
    dynamic_total = 0.0
    n = 0
    for r in rows:
        pnl = r.get("pnl_if_traded")
        if pnl is None:
            continue
        pnl = float(pnl)
        n += 1
        flat_total += pnl * unit_size
        stake = r.get("stake_size_hypothetical")
        stake = float(stake) if stake is not None else unit_size
        dynamic_total += pnl * stake

    return {
        "n": n,
        "flat_total": round(flat_total, 2),
        "dynamic_total": round(dynamic_total, 2),
        "delta": round(dynamic_total - flat_total, 2),
        "unit_size": unit_size,
    }


def main():
    config = _load_config()
    rows = logger.get_resolved_track_record()
    result = compute_preview(rows, config)
    eligible = sizing.is_dynamic_sizing_eligible(config)

    print("=" * W)
    print("DYNAMIC SIZING PREVIEW -- not the headline track record")
    print("-" * W)
    print(f"  Resolved signals (n):        {result['n']}")
    print(f"  Eligible (live gate + opt-in): {eligible}")
    print(f"  Gate: resolved_count >= {sizing.MIN_RESOLVED_COUNT} AND "
          f"resolved_count_per_category_max >= {sizing.MIN_RESOLVED_PER_CATEGORY}")
    print(f"  Flat-${result['unit_size']:.0f} P&L (headline figure):  ${result['flat_total']:+.2f}")
    print(f"  Confidence-weighted P&L:              ${result['dynamic_total']:+.2f}")
    print(f"  Delta:                                ${result['delta']:+.2f}")
    if not eligible:
        print("-" * W)
        print("  Not eligible yet -- the two totals above are identical by")
        print("  construction (stake_size_hypothetical falls back to the flat")
        print("  unit_size until the gate clears). This is expected, not a bug.")
    print("=" * W)


if __name__ == "__main__":
    main()
