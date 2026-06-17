"""
Threshold sweep over a saved Kalshi market snapshot.

Loads the most recent snapshot from data/snapshots/ (no network calls),
runs filter_markets + score_markets across a grid of threshold combinations,
and records how many markets survive and which flag path fired for each.

Flag paths:
  EDGE    — raw_edge > edge_threshold  (base_rate heuristic matched, edge big enough)
  BR_NONE — base_rate is None and mid_price is set  (dominant fallback)
  DRIFT   — drift_flag is True

Usage:
    python analysis/threshold_sweep.py
"""

import json
import os
import sys
from itertools import product

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scanner

SNAPSHOT_DIR = os.path.join(ROOT, "data", "snapshots")
REPORTS_DIR  = os.path.join(ROOT, "reports")


# ── Snapshot loader ───────────────────────────────────────────────────────────

def load_latest_snapshot() -> tuple[list[dict], dict]:
    """Returns (markets, header) from the most recent snapshot file."""
    files = sorted(
        [f for f in os.listdir(SNAPSHOT_DIR) if f.startswith("markets_") and f.endswith(".json")]
    )
    if not files:
        raise FileNotFoundError(f"No snapshot files found in {SNAPSHOT_DIR}")

    path = os.path.join(SNAPSHOT_DIR, files[-1])
    print(f"Loading snapshot: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    header  = data.get("header", {})
    markets = data.get("markets", [])
    print(f"  {len(markets)} markets, fetched {header.get('fetched_at', 'unknown')}")
    return markets, header


# ── Flag path classifier ──────────────────────────────────────────────────────

def classify_flag_path(scored_market: dict) -> str | None:
    """Returns the primary flag path that caused flag=True, or None if not flagged.

    score_market() already sets flag_path directly; this reads it and maps HEURISTIC
    to EDGE for backwards-compatible grid reporting (sweep uses passthrough mode).
    """
    if not scored_market.get("flag"):
        return None
    fp = scored_market.get("flag_path")
    if fp in ("EDGE", "HEURISTIC"):
        return "EDGE"
    if fp in ("BR_NONE", "DRIFT"):
        return fp
    return "OTHER"


# ── Grid definition ───────────────────────────────────────────────────────────

def build_grid() -> list[dict]:
    """
    Returns a list of config dicts to sweep.
    Grid spans edge_threshold, price bounds, and volume floors.
    Produces 3 × 3 × 3 = 27 combinations (≥ 25 required by spec).
    """
    edge_thresholds = [0.06, 0.08, 0.12]

    price_bounds = [
        (0.05, 0.95),   # current production
        (0.10, 0.90),   # tighter — cuts more tail-probability markets
        (0.15, 0.85),   # tightest — only markets near 50/50
    ]

    # Volume floor multipliers applied to the production bucket_min_volume
    # 1.0 = current production floors
    vol_multipliers = [0.5, 1.0, 2.0]

    BASE_BUCKET_VOL = {
        "INTRADAY":  50,
        "WEEKLY":    150,
        "MONTHLY":   400,
        "QUARTERLY": 250,
        "LONG":      100,
    }

    configs = []
    for edge, (min_p, max_p), vol_mult in product(edge_thresholds, price_bounds, vol_multipliers):
        bucket_vol = {k: int(v * vol_mult) for k, v in BASE_BUCKET_VOL.items()}
        configs.append({
            "label": (
                f"edge={edge:.2f} | price=[{min_p:.2f},{max_p:.2f}] | vol=×{vol_mult}"
            ),
            "edge_threshold":    edge,
            "min_market_price":  min_p,
            "max_market_price":  max_p,
            "vol_multiplier":    vol_mult,
            "markets": {
                "min_volume":              500,
                "max_volume_filter":       150_000,
                "min_market_price":        min_p,
                "max_market_price":        max_p,
                "min_days_to_close":       0,
                "max_days_to_close":       180,
                "edge_threshold":          edge,
                "bucket_min_volume":       bucket_vol,
                "efficient_market_keywords": [
                    "CPI", "Federal Reserve", "Fed rate", "nonfarm payroll",
                    "jobs report", "GDP", "inflation rate", "FOMC", "unemployment rate"
                ],
                "categories": [],
            },
        })

    return configs


# ── Sweep runner ──────────────────────────────────────────────────────────────

def run_sweep(markets: list[dict], grid: list[dict]) -> list[dict]:
    results = []
    for cfg in grid:
        filtered = scanner.filter_markets(markets, cfg)
        scored   = scanner.score_markets(filtered, cfg)

        flagged = [m for m in scored if m.get("flag")]
        path_counts = {"EDGE": 0, "BR_NONE": 0, "DRIFT": 0, "OTHER": 0}
        for m in flagged:
            path = classify_flag_path(m)
            if path:
                path_counts[path] += 1

        results.append({
            "label":      cfg["label"],
            "edge":       cfg["edge_threshold"],
            "min_price":  cfg["min_market_price"],
            "max_price":  cfg["max_market_price"],
            "vol_mult":   cfg["vol_multiplier"],
            "survived":   len(filtered),
            "flagged":    len(flagged),
            "pct_flagged": round(len(flagged) / len(filtered) * 100, 1) if filtered else 0,
            "EDGE":       path_counts["EDGE"],
            "BR_NONE":    path_counts["BR_NONE"],
            "DRIFT":      path_counts["DRIFT"],
            "OTHER":      path_counts["OTHER"],
        })

    return results


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(results: list[dict], header: dict) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "threshold_sweep.md")

    # Find production row (edge=0.08, price=[0.05,0.95], vol=×1.0)
    prod = next(
        (r for r in results
         if r["edge"] == 0.08 and r["min_price"] == 0.05 and r["vol_mult"] == 1.0),
        results[0]
    )

    # Find recommended config: tightest price bounds that still pass ≥8 markets,
    # with vol×1.0 or higher and edge=0.08
    candidates = [
        r for r in results
        if r["edge"] == 0.08 and r["vol_mult"] >= 1.0 and r["survived"] >= 8
    ]
    rec = min(candidates, key=lambda r: r["pct_flagged"]) if candidates else prod

    lines = []
    lines.append("# Threshold Sweep — Leviathan v1")
    lines.append("")
    lines.append(f"**Snapshot:** {header.get('fetched_at', 'unknown')}  ")
    lines.append(f"**Environment:** {header.get('environment', '?').upper()}  ")
    lines.append(f"**Total markets in snapshot:** {header.get('market_count', '?')}  ")
    lines.append(f"**Grid size:** {len(results)} combinations  ")
    lines.append("")

    # ── Grid table ──
    lines.append("## Grid Results")
    lines.append("")
    lines.append("| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |")
    lines.append("|----------|--------------|------------|----------|---------|-----------|------|---------|-------|")

    for r in results:
        tag = " <- prod" if (r["edge"] == 0.08 and r["min_price"] == 0.05 and r["vol_mult"] == 1.0) else ""
        tag = " <- **rec**" if r is rec else tag
        lines.append(
            f"| {r['edge']:.2f} | [{r['min_price']:.2f}, {r['max_price']:.2f}] "
            f"| ×{r['vol_mult']:.1f} "
            f"| {r['survived']} | {r['flagged']} | {r['pct_flagged']}% "
            f"| {r['EDGE']} | {r['BR_NONE']} | {r['DRIFT']} |{tag}"
        )

    # ── Verdict ──
    lines.append("")
    lines.append("## Verdict")
    lines.append("")

    br_none_pct = round(prod["BR_NONE"] / prod["flagged"] * 100) if prod["flagged"] else 0
    edge_pct    = round(prod["EDGE"]    / prod["flagged"] * 100) if prod["flagged"] else 0
    drift_pct   = round(prod["DRIFT"]   / prod["flagged"] * 100) if prod["flagged"] else 0

    lines.append(
        f"At **current production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0): "
        f"**{prod['survived']} markets** survive the filter and "
        f"**{prod['flagged']} are flagged** ({prod['pct_flagged']}% flag rate)."
    )
    lines.append("")
    lines.append("**Flag path breakdown:**")
    lines.append(f"- `BR_NONE` (base_rate=None fallback): **{prod['BR_NONE']}** markets ({br_none_pct}%)")
    lines.append(f"- `EDGE` (heuristic base rate + edge > threshold): **{prod['EDGE']}** markets ({edge_pct}%)")
    lines.append(f"- `DRIFT` (order-book mid vs last trade): **{prod['DRIFT']}** markets ({drift_pct}%)")
    lines.append("")

    if br_none_pct >= 80:
        lines.append(
            "**The `BR_NONE` fallback dominates.** Nearly every market that survives "
            "the filter gets flagged — not because a real mispricing signal fired, but "
            "because the scanner has no heuristic base rate for most market types. "
            "The filter is doing the real selection work; the flag step adds almost no "
            "discrimination. Tightening price bounds and volume floors is the highest-leverage "
            "knob available without changing the flag logic itself."
        )
    elif br_none_pct >= 50:
        lines.append(
            "**`BR_NONE` is the majority flag path.** The scanner's heuristic base rates "
            "cover only a small fraction of market types (rain, elections, IPOs, etc.). "
            "For everything else, any market with a mid-price gets flagged automatically. "
            "Tighter filters reduce noise without fixing the root cause."
        )
    else:
        lines.append(
            "**Flag quality looks reasonable.** The majority of flags come from real "
            "edge signals rather than the base_rate=None fallback."
        )

    lines.append("")
    lines.append("**Is the flag logic itself the problem?**  ")
    lines.append(
        "Yes, partially. The `score_market` flag fires on `base_rate is None and mid_price is not None` "
        "as a catch-all, meaning any market with a price and no heuristic match is automatically "
        "a candidate. This is intentional (send everything to Claude and let it filter), but it "
        "means the pre-Claude funnel is not doing meaningful probability-based selection. "
        "The fix is not a threshold change — it is adding more heuristic base rates, or replacing "
        "the `BR_NONE` trigger with a require-signal rule so only markets with a real anomaly "
        "(edge, drift, or whale) are flagged."
    )

    lines.append("")
    lines.append("## Recommendation")
    lines.append("")

    if rec is not prod:
        lines.append(
            f"**Recommended config:** edge={rec['edge']:.2f}, price=[{rec['min_price']:.2f}, "
            f"{rec['max_price']:.2f}], vol×{rec['vol_mult']:.1f}  "
        )
        lines.append(
            f"→ {rec['survived']} markets survive, {rec['flagged']} flagged ({rec['pct_flagged']}%).  "
        )
        lines.append(
            f"**Reasoning:** Tighter price bounds cut the long tail of near-certain and "
            f"tail-probability markets while preserving the contested 15–85% range where "
            f"genuine mispricing is plausible. Volume floor at ×{rec['vol_mult']:.1f} avoids "
            f"illiquid markets where the edge estimate is noise."
        )
    else:
        lines.append(
            f"**Stick with current production config** — it already produces a workable "
            f"candidate count. The real improvement is in flag logic, not thresholds."
        )

    lines.append("")
    lines.append(
        "> **Note:** This sweep measures candidate *volume* only — it cannot judge signal "
        "*correctness*. A market flagged here may or may not represent a real edge; "
        "that can only be measured once markets resolve."
    )

    report = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)

    return path


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    markets, header = load_latest_snapshot()

    print(f"\nBuilding sweep grid...")
    grid = build_grid()
    print(f"  {len(grid)} combinations")

    print("Running sweep (offline — no network)...")
    results = run_sweep(markets, grid)

    # Print summary table to stdout
    print(f"\n{'Edge':>6}  {'Price bounds':>16}  {'Vol':>5}  {'Surv':>5}  {'Flag':>5}  {'%Flag':>6}  {'EDGE':>5}  {'BR_NONE':>8}  {'DRIFT':>6}")
    print("-" * 85)
    for r in results:
        prod_marker = " <-- PROD" if (r["edge"] == 0.08 and r["min_price"] == 0.05 and r["vol_mult"] == 1.0) else ""
        print(
            f"{r['edge']:>6.2f}  [{r['min_price']:.2f},{r['max_price']:.2f}]{' ':>6}"
            f"  ×{r['vol_mult']:.1f}  {r['survived']:>5}  {r['flagged']:>5}"
            f"  {r['pct_flagged']:>5.1f}%  {r['EDGE']:>5}  {r['BR_NONE']:>8}  {r['DRIFT']:>6}{prod_marker}"
        )

    report_path = write_report(results, header)
    print(f"\nReport written: {report_path}")
    return results


if __name__ == "__main__":
    main()
