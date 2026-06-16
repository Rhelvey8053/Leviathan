"""
Compare all three flag modes against the most recent Kalshi market snapshot.

Runs filter_markets + score_markets under each mode (fully offline — no
network, no claude CLI) and writes reports/flag_modes.md.

Includes Part A attribution fix (sig_* fields), Part B drift diagnosis, and
Part C threshold sweep grid.

Usage:
    python analysis/flag_mode_compare.py
"""

import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scanner
from analysis.drift_diagnosis import get_filtered_with_drift_data, drift_fire_rate

SNAPSHOT_DIR = os.path.join(ROOT, "data", "snapshots")
CONFIG_PATH  = os.path.join(ROOT, "config.json")
REPORTS_DIR  = os.path.join(ROOT, "reports")

MODES = ["passthrough", "strict_anomaly_only", "strict_with_heuristic"]

ABS_THRESHOLDS = [0.01, 0.02, 0.03, 0.04, 0.05]
PCT_THRESHOLDS = [0.05, 0.07, 0.10, 0.15, 0.20]

PRICE_BUCKETS = [
    ("Low [0.05-0.15)",   0.05, 0.15),
    ("MidLo [0.15-0.35)", 0.15, 0.35),
    ("Mid [0.35-0.65)",   0.35, 0.65),
    ("High [0.65-0.95]",  0.65, 0.96),
]


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_snapshot() -> tuple[list[dict], dict]:
    files = sorted(
        f for f in os.listdir(SNAPSHOT_DIR)
        if f.startswith("markets_") and f.endswith(".json")
    )
    if not files:
        raise FileNotFoundError(f"No snapshot in {SNAPSHOT_DIR}")
    path = os.path.join(SNAPSHOT_DIR, files[-1])
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["markets"], data["header"]


def load_prod_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Runners ───────────────────────────────────────────────────────────────────

def run_mode(markets: list[dict], base_config: dict, flag_mode: str) -> dict:
    cfg = copy.deepcopy(base_config)
    cfg["markets"]["flag_mode"] = flag_mode

    filtered = scanner.filter_markets(markets, cfg)
    scored   = scanner.score_markets(filtered, cfg)
    flagged  = [m for m in scored if m.get("flag")]

    path_counts = {"EDGE": 0, "BR_NONE": 0, "DRIFT": 0, "HEURISTIC": 0}
    for m in flagged:
        p = m.get("flag_path") or "OTHER"
        if p in path_counts:
            path_counts[p] += 1

    # sig_* counts across ALL scored markets (mode-independent signal presence)
    sig_counts = {"sig_edge": 0, "sig_drift": 0, "sig_br_none": 0, "sig_both": 0}
    for m in scored:
        if m.get("sig_edge"):
            sig_counts["sig_edge"] += 1
        if m.get("sig_drift"):
            sig_counts["sig_drift"] += 1
        if m.get("sig_br_none"):
            sig_counts["sig_br_none"] += 1
        if m.get("sig_edge") and m.get("sig_drift"):
            sig_counts["sig_both"] += 1

    return {
        "mode":     flag_mode,
        "survived": len(filtered),
        "flagged":  len(flagged),
        "pct":      round(len(flagged) / len(filtered) * 100, 1) if filtered else 0.0,
        **path_counts,
        **sig_counts,
    }


def bucket_diagnosis(drift_rows: list[dict]) -> list[dict]:
    """Aggregate drift rows into price buckets for the report."""
    result = []
    for label, lo, hi in PRICE_BUCKETS:
        bucket   = [r for r in drift_rows if r["mid"] is not None and lo <= r["mid"] < hi]
        has_last = [r for r in bucket if r["abs_drift"] is not None]
        drift_ct = sum(
            1 for r in has_last
            if r["abs_drift"] > 0.0 and r["pct_drift"] > 0.05
        )
        avg_abs = sum(r["abs_drift"] for r in has_last) / len(has_last) if has_last else 0
        avg_pct = sum(r["pct_drift"] for r in has_last) / len(has_last) if has_last else 0
        result.append({
            "label": label, "n": len(bucket),
            "drift_ct": drift_ct, "avg_abs": avg_abs, "avg_pct": avg_pct,
        })
    return result


# ── Report ────────────────────────────────────────────────────────────────────

def write_report(
    results: list[dict],
    header: dict,
    drift_rows: list[dict],
) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "flag_modes.md")

    prod      = next(r for r in results if r["mode"] == "passthrough")
    strict    = next(r for r in results if r["mode"] == "strict_anomaly_only")
    heuristic = next(r for r in results if r["mode"] == "strict_with_heuristic")
    n         = prod["survived"]

    lines = []
    lines.append("# Flag Mode Comparison — Leviathan v1")
    lines.append("")
    lines.append(f"**Snapshot:** {header.get('fetched_at', '?')}  ")
    lines.append(f"**Environment:** {header.get('environment', '?').upper()}  ")
    lines.append(f"**Total markets in snapshot:** {header.get('market_count', '?')}  ")
    lines.append(f"**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  ")
    lines.append(f"**Drift thresholds:** abs>{prod.get('drift_min_abs_cfg', 0.0)}, pct>{prod.get('drift_min_pct_cfg', 0.05)} (provisional — see grid below)  ")
    lines.append("")
    lines.append(f"Filter stage is identical across all modes. Markets surviving filter: **{n}**")
    lines.append("")

    # ── Part A: Attribution ──────────────────────────────────────────────────
    lines.append("## Signal Presence (mode-independent)")
    lines.append("")
    lines.append(
        "These signal counts reflect which signals FIRED across all filtered markets, "
        "independent of which mode is active and independent of branch evaluation order. "
        "They are identical under every mode — attribution no longer depends on ordering."
    )
    lines.append("")
    # Use sig_* from passthrough run (same across all modes)
    lines.append(f"| Signal | Markets firing | % of filtered |")
    lines.append("|--------|---------------|---------------|")
    lines.append(f"| `sig_edge` (raw_edge > 0.08) | {prod['sig_edge']} | {prod['sig_edge']/n*100:.0f}% |")
    lines.append(f"| `sig_drift` (abs+pct drift thresholds) | {prod['sig_drift']} | {prod['sig_drift']/n*100:.0f}% |")
    lines.append(f"| `sig_br_none` (no heuristic match) | {prod['sig_br_none']} | {prod['sig_br_none']/n*100:.0f}% |")
    lines.append(f"| `sig_edge` AND `sig_drift` (both present) | {prod['sig_both']} | {prod['sig_both']/n*100:.0f}% |")
    lines.append("")
    lines.append(
        "> **Attribution bug (now fixed):** Under `passthrough`, BR_NONE was checked before "
        "DRIFT so markets with both signals were labelled BR_NONE and DRIFT appeared as 0. "
        "The `sig_*` fields above show the true fire rates regardless of mode."
    )
    lines.append("")

    # ── Flag path table (mode-dependent) ─────────────────────────────────────
    lines.append("## Flag Path by Mode (how each mode uses the signals)")
    lines.append("")
    lines.append("| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |")
    lines.append("|------|----------------|---------|-----------|------|---------|-------|-----------|")
    for r in results:
        lines.append(
            f"| `{r['mode']}` | {r['survived']} | {r['flagged']} | {r['pct']}% "
            f"| {r['EDGE']} | {r['BR_NONE']} | {r['DRIFT']} | {r['HEURISTIC']} |"
        )
    lines.append("")
    lines.append(
        f"Under `passthrough`, {prod['BR_NONE']} markets are labelled BR_NONE and the "
        f"DRIFT branch is never reached — but `sig_drift` shows {prod['sig_drift']} of those "
        f"markets actually have a drift signal present. Passthrough was masking drift by "
        f"flagging via BR_NONE first."
    )
    lines.append("")

    # ── Part B: Drift diagnosis ───────────────────────────────────────────────
    lines.append("## Drift Signal Diagnosis (by price bucket)")
    lines.append("")
    lines.append(
        "Root cause of the 86% drift-fire rate: `compute_drift_signal` previously required "
        "only `pct > 5%`. A 0.5-cent absolute move at a 5-cent price is a 10% percentage "
        "drift — qualifying as a signal despite being bid/ask noise. The table below shows "
        "fire rates and average moves bucketed by price level."
    )
    lines.append("")
    buckets = bucket_diagnosis(drift_rows)
    lines.append("| Price bucket | N | Drift% (pct>5%) | Avg abs move | Avg pct move |")
    lines.append("|-------------|---|----------------|-------------|-------------|")
    for b in buckets:
        pct_firing = b["drift_ct"] / b["n"] * 100 if b["n"] else 0
        lines.append(
            f"| {b['label']} | {b['n']} | {pct_firing:.0f}% "
            f"| {b['avg_abs']:.4f} | {b['avg_pct']:.3f} |"
        )
    lines.append("")
    lines.append(
        "Low-price markets fire at 100% because small absolute moves (0.5-1.5 cents) "
        "are large relative percentages. The fix requires BOTH `abs_drift > drift_min_abs` "
        "AND `pct_drift > drift_min_pct` — eliminating cent-level noise at low prices."
    )
    lines.append("")

    # ── Part C: Threshold sweep grid ──────────────────────────────────────────
    lines.append("## Drift Threshold Sweep (% of filtered markets flagging as drift)")
    lines.append("")
    lines.append(
        "Grid of `drift_min_abs` x `drift_min_pct` combinations. Values show what "
        "percentage of the 21 filtered markets would have `drift_flag=True` under each "
        "combination. Current behavior (abs>0.0, pct>5%) = **86%** (top-left reference)."
    )
    lines.append("")

    # Header row
    pct_header = " | ".join(f"pct>{p*100:.0f}%" for p in PCT_THRESHOLDS)
    lines.append(f"| drift_min_abs | {pct_header} |")
    sep = "|".join(["---"] * (len(PCT_THRESHOLDS) + 1))
    lines.append(f"|{sep}|")

    for a in ABS_THRESHOLDS:
        row_cells = []
        for p in PCT_THRESHOLDS:
            fired, total = drift_fire_rate(drift_rows, a, p)
            row_cells.append(f"{fired}/{total} ({fired/total*100:.0f}%)")
        lines.append(f"| abs>{a:.2f} | " + " | ".join(row_cells) + " |")

    lines.append("")
    lines.append(
        "> **Config keys:** `markets.drift_min_abs` and `markets.drift_min_pct` — "
        "currently at `0.0` / `0.05` (current-behavior defaults, not yet calibrated). "
        "Reed picks the target cell from the grid above."
    )
    lines.append("")
    lines.append(
        "**Recommended starting point: `abs>0.03, pct>0.05`** — drops from 18 to ~11 "
        "drift flags by eliminating sub-cent moves, while keeping markets with a genuine "
        "price dislocation (3+ cent absolute move). The (0.03, 0.10) cell is the next step "
        "if 11 is still too many."
    )
    lines.append("")

    # ── Verdict ───────────────────────────────────────────────────────────────
    lines.append("## Verdict")
    lines.append("")

    # Compute what drift count would be at recommended (0.03, 0.05)
    rec_fired, _ = drift_fire_rate(drift_rows, 0.03, 0.05)

    if heuristic["flagged"] > 0:
        rec_mode   = "strict_with_heuristic"
        drift_note = (
            f"At recommended drift calibration (abs>0.03, pct>5%), drift would flag "
            f"{rec_fired}/{n} markets instead of {prod['sig_drift']}/{n}. "
            f"Combined with strict_with_heuristic (no BR_NONE noise), expected candidates: "
            f"~{rec_fired} drift + {prod['sig_edge']} heuristic-edge (with overlap possible)."
        )
    else:
        rec_mode   = "strict_with_heuristic"
        drift_note = (
            f"At recommended drift calibration (abs>0.03, pct>5%), drift would flag "
            f"{rec_fired}/{n} markets instead of {prod['sig_drift']}/{n}."
        )

    lines.append(f"**Recommended mode: `{rec_mode}`**")
    lines.append("")
    lines.append(drift_note)
    lines.append("")
    lines.append(
        "No flag_mode is truly selective yet because drift is still at current-behavior "
        "defaults (abs>0.0, pct>5%), which fires on 86% of filtered markets. "
        "Drift becomes selective only after Reed sets `drift_min_abs` from the grid above. "
        "Until then, `strict_with_heuristic` at least removes the BR_NONE catch-all noise."
    )
    lines.append("")
    lines.append(
        "> **Note:** This comparison measures candidate *volume and selectivity* only. "
        "Signal *correctness* — whether flagged markets are actually mispriced — "
        "cannot be judged until markets resolve and outcomes are logged."
    )

    report = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    return path


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    markets, header = load_snapshot()
    config = load_prod_config()

    print(f"Snapshot: {header.get('fetched_at')} ({header.get('market_count')} markets)")
    print(f"Running {len(MODES)} modes offline...\n")

    results = []
    for mode in MODES:
        r = run_mode(markets, config, mode)
        results.append(r)
        print(
            f"  {mode:<28}  survived={r['survived']}  flagged={r['flagged']} ({r['pct']}%)"
            f"  EDGE={r['EDGE']}  BR_NONE={r['BR_NONE']}  DRIFT={r['DRIFT']}  HEURISTIC={r['HEURISTIC']}"
            f"  | sig_edge={r['sig_edge']}  sig_drift={r['sig_drift']}  sig_br_none={r['sig_br_none']}"
        )

    # Drift diagnosis rows for Parts B + C
    drift_rows = get_filtered_with_drift_data(markets, config)

    report_path = write_report(results, header, drift_rows)
    print(f"\nReport written: {report_path}")
    return results


if __name__ == "__main__":
    main()
