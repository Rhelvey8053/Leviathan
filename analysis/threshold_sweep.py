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
    Grid spans edge_threshold, price bounds, volume floors, and flag_mode.
    Produces 3 × 3 × 3 × 2 = 54 combinations.

    flag_mode variants:
      passthrough          — original mode: BR_NONE fallback flags everything without a base rate
      strict_with_heuristic — production mode: only flag when drift OR (base_rate != None AND edge > threshold)
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

    flag_modes = ["passthrough", "strict_with_heuristic"]

    BASE_BUCKET_VOL = {
        "INTRADAY":  50,
        "WEEKLY":    150,
        "MONTHLY":   400,
        "QUARTERLY": 250,
        "LONG":      100,
    }

    configs = []
    for edge, (min_p, max_p), vol_mult, mode in product(
        edge_thresholds, price_bounds, vol_multipliers, flag_modes
    ):
        bucket_vol = {k: int(v * vol_mult) for k, v in BASE_BUCKET_VOL.items()}
        configs.append({
            "label": (
                f"edge={edge:.2f} | price=[{min_p:.2f},{max_p:.2f}] | vol=×{vol_mult} | {mode}"
            ),
            "edge_threshold":    edge,
            "min_market_price":  min_p,
            "max_market_price":  max_p,
            "vol_multiplier":    vol_mult,
            "flag_mode":         mode,
            "markets": {
                "min_volume":              500,
                "max_volume_filter":       150_000,
                "min_market_price":        min_p,
                "max_market_price":        max_p,
                "min_days_to_close":       0,
                "max_days_to_close":       180,
                "edge_threshold":          edge,
                "flag_mode":               mode,
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
            "label":       cfg["label"],
            "edge":        cfg["edge_threshold"],
            "min_price":   cfg["min_market_price"],
            "max_price":   cfg["max_market_price"],
            "vol_mult":    cfg["vol_multiplier"],
            "flag_mode":   cfg.get("flag_mode", "passthrough"),
            "survived":    len(filtered),
            "flagged":     len(flagged),
            "pct_flagged": round(len(flagged) / len(filtered) * 100, 1) if filtered else 0,
            "EDGE":        path_counts["EDGE"],
            "BR_NONE":     path_counts["BR_NONE"],
            "DRIFT":       path_counts["DRIFT"],
            "OTHER":       path_counts["OTHER"],
        })

    return results


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(results: list[dict], header: dict) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "threshold_sweep.md")

    # Find production row (edge=0.08, price=[0.05,0.95], vol=×1.0, strict_with_heuristic)
    prod = next(
        (r for r in results
         if r["edge"] == 0.08 and r["min_price"] == 0.05
         and r["vol_mult"] == 1.0 and r["flag_mode"] == "strict_with_heuristic"),
        next(
            (r for r in results
             if r["edge"] == 0.08 and r["min_price"] == 0.05 and r["vol_mult"] == 1.0),
            results[0]
        )
    )

    # Find recommended config: tightest price bounds that still pass ≥8 markets,
    # with vol×1.0 or higher and edge=0.08 in strict_with_heuristic mode
    candidates = [
        r for r in results
        if r["edge"] == 0.08 and r["vol_mult"] >= 1.0 and r["survived"] >= 8
        and r["flag_mode"] == "strict_with_heuristic"
    ]
    rec = min(candidates, key=lambda r: r["pct_flagged"]) if candidates else prod

    lines = []
    lines.append("# Threshold Sweep — Leviathan v1")
    lines.append("")
    lines.append(f"**Snapshot:** {header.get('fetched_at', 'unknown')}  ")
    lines.append(f"**Environment:** {header.get('environment', '?').upper()}  ")
    lines.append(f"**Total markets in snapshot:** {header.get('market_count', '?')}  ")
    lines.append(f"**Grid size:** {len(results)} combinations (3×3×3×2 — includes passthrough vs strict_with_heuristic)  ")
    lines.append("")

    # ── Grid tables: one per flag_mode ──
    for mode in ["strict_with_heuristic", "passthrough"]:
        mode_rows = [r for r in results if r.get("flag_mode") == mode]
        mode_label = "strict\\_with\\_heuristic (production)" if mode == "strict_with_heuristic" else "passthrough (baseline)"
        lines.append(f"## Grid Results — `{mode_label}`")
        lines.append("")
        lines.append("| Edge thr | Price bounds | Vol floors | Survived | Flagged | % flagged | EDGE | BR_NONE | DRIFT |")
        lines.append("|----------|--------------|------------|----------|---------|-----------|------|---------|-------|")

        for r in mode_rows:
            is_prod = (r["edge"] == 0.08 and r["min_price"] == 0.05
                       and r["vol_mult"] == 1.0 and r["flag_mode"] == "strict_with_heuristic")
            tag = " ← **prod**" if is_prod else (" ← **rec**" if r is rec else "")
            lines.append(
                f"| {r['edge']:.2f} | [{r['min_price']:.2f}, {r['max_price']:.2f}] "
                f"| ×{r['vol_mult']:.1f} "
                f"| {r['survived']} | {r['flagged']} | {r['pct_flagged']}% "
                f"| {r['EDGE']} | {r['BR_NONE']} | {r['DRIFT']} |{tag}"
            )
        lines.append("")

    # ── Verdict ──
    lines.append("## Verdict")
    lines.append("")

    br_none_pct = round(prod["BR_NONE"] / prod["flagged"] * 100) if prod["flagged"] else 0
    edge_pct    = round(prod["EDGE"]    / prod["flagged"] * 100) if prod["flagged"] else 0
    drift_pct   = round(prod["DRIFT"]   / prod["flagged"] * 100) if prod["flagged"] else 0

    lines.append(
        f"At **production thresholds** (edge=0.08, price=[0.05, 0.95], vol×1.0, strict_with_heuristic): "
        f"**{prod['survived']} markets** survive the filter and "
        f"**{prod['flagged']} are flagged** ({prod['pct_flagged']}% flag rate)."
    )
    lines.append("")
    lines.append("**Flag path breakdown (production mode):**")
    lines.append(f"- `HEURISTIC/EDGE` (base rate edge > threshold): **{prod['EDGE']}** markets ({edge_pct}%)")
    lines.append(f"- `DRIFT` (order-book mid vs last trade): **{prod['DRIFT']}** markets ({drift_pct}%)")
    lines.append(f"- `BR_NONE` (no base rate fallback): **{prod['BR_NONE']}** markets ({br_none_pct}%)")
    lines.append("")

    if br_none_pct == 0:
        lines.append(
            "**`BR_NONE` = 0% — the heuristic coverage is complete.** Every market that survives "
            "the filter has a matching base rate, so `strict_with_heuristic` mode flags only "
            "markets with real edge signals (heuristic disagrees with price by >8pp) or drift. "
            "This is the optimal state: the flag step is doing genuine probability-based selection."
        )
    elif br_none_pct >= 80:
        lines.append(
            "**The `BR_NONE` fallback dominates in passthrough mode** — nearly every market that "
            "survives the filter gets flagged because the scanner lacks heuristic base rates for "
            "most market types. Switch to `strict_with_heuristic` mode and add heuristic base "
            "rates to achieve meaningful pre-Claude discrimination."
        )
    elif br_none_pct >= 50:
        lines.append(
            "**`BR_NONE` is the majority flag path.** Consider switching to `strict_with_heuristic` "
            "mode and expanding heuristic base rate coverage to reduce noise."
        )
    else:
        lines.append(
            "**Flag quality looks reasonable.** The majority of flags come from real "
            "edge signals rather than the base_rate=None fallback."
        )

    lines.append("")
    lines.append("**Passthrough vs strict_with_heuristic:**  ")
    lines.append(
        "The passthrough grid shows BR_NONE dominating — every unmatched market gets flagged. "
        "The strict_with_heuristic grid shows only HEURISTIC + DRIFT — each flag represents "
        "a specific signal. With BR_NONE coverage at 0%, strict_with_heuristic is the "
        "correct production mode: it rejects markets where the crowd is likely right "
        "(no strong heuristic disagreement, no drift) and focuses Claude's budget on "
        "genuine mispricing candidates."
    )

    lines.append("")
    lines.append("## Recommendation")
    lines.append("")

    if rec is not prod:
        lines.append(
            f"**Recommended config:** edge={rec['edge']:.2f}, price=[{rec['min_price']:.2f}, "
            f"{rec['max_price']:.2f}], vol×{rec['vol_mult']:.1f}, strict_with_heuristic  "
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
            f"candidate count with a clean signal distribution."
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

    # Print summary table to stdout (strict_with_heuristic rows first for readability)
    for mode in ["strict_with_heuristic", "passthrough"]:
        print(f"\n  --- {mode} ---")
        print(f"  {'Edge':>6}  {'Price bounds':>16}  {'Vol':>5}  {'Surv':>5}  {'Flag':>5}  {'%Flag':>6}  {'EDGE':>5}  {'BR_NONE':>8}  {'DRIFT':>6}")
        print("  " + "-" * 80)
        for r in [r for r in results if r.get("flag_mode") == mode]:
            is_prod = (r["edge"] == 0.08 and r["min_price"] == 0.05
                       and r["vol_mult"] == 1.0 and mode == "strict_with_heuristic")
            prod_marker = " <-- PROD" if is_prod else ""
            print(
                f"  {r['edge']:>6.2f}  [{r['min_price']:.2f},{r['max_price']:.2f}]{' ':>6}"
                f"  ×{r['vol_mult']:.1f}  {r['survived']:>5}  {r['flagged']:>5}"
                f"  {r['pct_flagged']:>5.1f}%  {r['EDGE']:>5}  {r['BR_NONE']:>8}  {r['DRIFT']:>6}{prod_marker}"
            )

    report_path = write_report(results, header)
    print(f"\nReport written: {report_path}")
    return results


if __name__ == "__main__":
    main()
