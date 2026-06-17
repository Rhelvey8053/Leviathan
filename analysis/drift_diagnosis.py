"""
Diagnose the drift signal on the saved snapshot and sweep dual-threshold combinations.

Part B — shows drift magnitude distribution bucketed by price level to surface
the low-price percentage-inflation problem.

Part C — sweeps a 5x5 grid of (drift_min_abs x drift_min_pct) combinations and
reports the resulting drift-fire rate as % of filtered markets.

Usage:
    python analysis/drift_diagnosis.py
"""

import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scanner

SNAPSHOT_DIR = os.path.join(ROOT, "data", "snapshots")
CONFIG_PATH  = os.path.join(ROOT, "config.json")

ABS_THRESHOLDS = [0.01, 0.02, 0.03, 0.04, 0.05]
PCT_THRESHOLDS = [0.05, 0.07, 0.10, 0.15, 0.20]

PRICE_BUCKETS = [
    ("Low   [0.05–0.15)", 0.05, 0.15),
    ("MidLo [0.15–0.35)", 0.15, 0.35),
    ("Mid   [0.35–0.65)", 0.35, 0.65),
    ("High  [0.65–0.95]", 0.65, 0.96),
]


def load_snapshot():
    files = sorted(
        f for f in os.listdir(SNAPSHOT_DIR)
        if f.startswith("markets_") and f.endswith(".json")
    )
    if not files:
        raise FileNotFoundError(f"No snapshot in {SNAPSHOT_DIR}")
    with open(os.path.join(SNAPSHOT_DIR, files[-1]), encoding="utf-8") as f:
        data = json.load(f)
    return data["markets"], data["header"]


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_filtered_with_drift_data(markets, config):
    """Filter markets and annotate each with raw drift values (pre-threshold)."""
    filtered = scanner.filter_markets(markets, config)
    rows = []
    for m in filtered:
        bid  = float(m.get("yes_bid_dollars") or m.get("yes_bid") or 0)
        ask  = float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0)
        last = float(m.get("last_price_dollars") or 0)
        # Two-sided check: only use (bid+ask)/2 when both sides are posted
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        else:
            mid = last if last > 0 else None
        if mid is None or not last:
            abs_drift = pct_drift = None
        else:
            abs_drift = abs(mid - last)
            pct_drift = abs_drift / last
        rows.append({
            "ticker":    m.get("ticker", ""),
            "mid":       mid,
            "last":      last,
            "abs_drift": abs_drift,
            "pct_drift": pct_drift,
        })
    return rows


def drift_fire_rate(rows, drift_min_abs, drift_min_pct):
    """Count markets that pass both drift thresholds out of total filtered."""
    fired = sum(
        1 for r in rows
        if r["abs_drift"] is not None
        and r["abs_drift"] > drift_min_abs
        and r["pct_drift"] > drift_min_pct
    )
    return fired, len(rows)


def print_bucket_diagnosis(rows, drift_min_abs=0.03, drift_min_pct=0.07):
    print(f"\n=== Part B: Drift magnitude by price bucket (config: abs>{drift_min_abs}, pct>{drift_min_pct*100:.0f}%) ===\n")
    print(f"{'Bucket':<26} {'N':>3}  {'Drift%':>6}  {'Avg abs':>8}  {'Avg pct':>8}  {'Max abs':>8}  {'Max pct':>8}")
    print("-" * 78)
    for label, lo, hi in PRICE_BUCKETS:
        bucket = [r for r in rows if r["mid"] is not None and lo <= r["mid"] < hi]
        if not bucket:
            continue
        has_last = [r for r in bucket if r["abs_drift"] is not None]
        drift_flag_count = sum(
            1 for r in has_last
            if r["abs_drift"] > drift_min_abs and r["pct_drift"] > drift_min_pct
        )
        avg_abs = sum(r["abs_drift"] for r in has_last) / len(has_last) if has_last else 0
        avg_pct = sum(r["pct_drift"] for r in has_last) / len(has_last) if has_last else 0
        max_abs = max((r["abs_drift"] for r in has_last), default=0)
        max_pct = max((r["pct_drift"] for r in has_last), default=0)
        pct_drifting = drift_flag_count / len(bucket) * 100
        print(
            f"{label:<26} {len(bucket):>3}  {pct_drifting:>5.0f}%  "
            f"{avg_abs:>8.4f}  {avg_pct:>8.3f}  {max_abs:>8.4f}  {max_pct:>8.3f}"
        )

    print()
    print("Detail (all filtered markets):")
    print(f"  {'Ticker':<44} {'mid':>6}  {'last':>6}  {'abs':>7}  {'pct':>7}  drift?")
    print("  " + "-" * 80)
    for r in rows:
        if r["abs_drift"] is None:
            flag_str = "no-last"
            abs_str  = "   N/A"
            pct_str  = "   N/A"
        else:
            fires = r["abs_drift"] > drift_min_abs and r["pct_drift"] > drift_min_pct
            flag_str = "YES" if fires else "no"
            abs_str  = f"{r['abs_drift']:>7.4f}"
            pct_str  = f"{r['pct_drift']:>7.4f}"
        mid_str  = f"{r['mid']:>6.3f}" if r["mid"] is not None else "   N/A"
        last_str = f"{r['last']:>6.3f}" if r["last"] else "   N/A"
        print(f"  {r['ticker']:<44} {mid_str}  {last_str}  {abs_str}  {pct_str}  {flag_str}")


def print_sweep_grid(rows):
    print("\n=== Part C: 5x5 drift-fire rate grid (% of filtered markets flagged) ===\n")
    print("     drift_min_pct ->")
    header = f"  {'drift_min_abs':>14} |" + "".join(f" {p*100:>5.0f}%" for p in PCT_THRESHOLDS)
    print(header)
    print("  " + "-" * (16 + 8 * len(PCT_THRESHOLDS)))
    for a in ABS_THRESHOLDS:
        row_str = f"  abs > {a:.2f}       |"
        for p in PCT_THRESHOLDS:
            fired, total = drift_fire_rate(rows, a, p)
            pct = fired / total * 100 if total else 0
            row_str += f" {pct:>5.0f}%"
        print(row_str)
    print()
    # Report current config thresholds as baseline
    mkt_cfg = load_config().get("markets", {})
    cfg_abs = mkt_cfg.get("drift_min_abs", 0.03)
    cfg_pct = mkt_cfg.get("drift_min_pct", 0.07)
    fired_base, total = drift_fire_rate(rows, cfg_abs, cfg_pct)
    print(f"Config baseline (abs>{cfg_abs}, pct>{cfg_pct*100:.0f}%): {fired_base}/{total} = {fired_base/total*100:.0f}% of filtered markets flag as drift")


def build_results(rows):
    """Return the grid and bucket data as dicts for the report writer."""
    grid = {}
    for a in ABS_THRESHOLDS:
        for p in PCT_THRESHOLDS:
            fired, total = drift_fire_rate(rows, a, p)
            grid[(a, p)] = (fired, total)

    buckets = []
    for label, lo, hi in PRICE_BUCKETS:
        bucket = [r for r in rows if r["mid"] is not None and lo <= r["mid"] < hi]
        has_last = [r for r in bucket if r["abs_drift"] is not None]
        drift_flag_count = sum(
            1 for r in has_last if r["abs_drift"] > 0.0 and r["pct_drift"] > 0.05
        )
        avg_abs = sum(r["abs_drift"] for r in has_last) / len(has_last) if has_last else 0
        avg_pct = sum(r["pct_drift"] for r in has_last) / len(has_last) if has_last else 0
        buckets.append({
            "label": label,
            "n": len(bucket),
            "drift_count": drift_flag_count,
            "avg_abs": avg_abs,
            "avg_pct": avg_pct,
        })

    mkt_cfg = load_config().get("markets", {})
    cfg_abs = mkt_cfg.get("drift_min_abs", 0.03)
    cfg_pct = mkt_cfg.get("drift_min_pct", 0.07)
    baseline_fired, total = drift_fire_rate(rows, cfg_abs, cfg_pct)
    return grid, buckets, baseline_fired, total


def main():
    markets, header = load_snapshot()
    config = load_config()

    mkt_cfg = config.get("markets", {})
    cfg_abs = mkt_cfg.get("drift_min_abs", 0.03)
    cfg_pct = mkt_cfg.get("drift_min_pct", 0.07)

    print(f"Snapshot: {header.get('fetched_at')} ({header.get('market_count')} markets)")
    rows = get_filtered_with_drift_data(markets, config)
    print(f"Filtered markets: {len(rows)}")

    print_bucket_diagnosis(rows, drift_min_abs=cfg_abs, drift_min_pct=cfg_pct)
    print_sweep_grid(rows)

    return build_results(rows)


if __name__ == "__main__":
    main()
