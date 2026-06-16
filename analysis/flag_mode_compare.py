"""
Compare all three flag modes against the most recent Kalshi market snapshot.

Runs filter_markets + score_markets under each mode (fully offline — no
network, no claude CLI) and writes reports/flag_modes.md.

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

SNAPSHOT_DIR = os.path.join(ROOT, "data", "snapshots")
CONFIG_PATH  = os.path.join(ROOT, "config.json")
REPORTS_DIR  = os.path.join(ROOT, "reports")

MODES = ["passthrough", "strict_anomaly_only", "strict_with_heuristic"]


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


# ── Runner ────────────────────────────────────────────────────────────────────

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

    return {
        "mode":      flag_mode,
        "survived":  len(filtered),
        "flagged":   len(flagged),
        "pct":       round(len(flagged) / len(filtered) * 100, 1) if filtered else 0.0,
        **path_counts,
    }


# ── Report ────────────────────────────────────────────────────────────────────

def write_report(results: list[dict], header: dict) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "flag_modes.md")

    prod = next(r for r in results if r["mode"] == "passthrough")
    strict = next(r for r in results if r["mode"] == "strict_anomaly_only")
    heuristic = next(r for r in results if r["mode"] == "strict_with_heuristic")

    lines = []
    lines.append("# Flag Mode Comparison — Leviathan v1")
    lines.append("")
    lines.append(f"**Snapshot:** {header.get('fetched_at', '?')}  ")
    lines.append(f"**Environment:** {header.get('environment', '?').upper()}  ")
    lines.append(f"**Total markets in snapshot:** {header.get('market_count', '?')}  ")
    lines.append(f"**Production thresholds:** edge=0.08, price=[0.05, 0.95], vol x1.0  ")
    lines.append("")
    lines.append("Filter stage is identical across all modes — only the flag condition differs.")
    lines.append(f"Markets surviving filter: **{prod['survived']}**")
    lines.append("")

    lines.append("## Results by Mode")
    lines.append("")
    lines.append("| Mode | Survived filter | Flagged | % flagged | EDGE | BR_NONE | DRIFT | HEURISTIC |")
    lines.append("|------|----------------|---------|-----------|------|---------|-------|-----------|")
    for r in results:
        lines.append(
            f"| `{r['mode']}` | {r['survived']} | {r['flagged']} | {r['pct']}% "
            f"| {r['EDGE']} | {r['BR_NONE']} | {r['DRIFT']} | {r['HEURISTIC']} |"
        )

    lines.append("")
    lines.append("## Mode Descriptions")
    lines.append("")
    lines.append("### `passthrough` (current production default)")
    lines.append(
        f"Flags **{prod['flagged']} of {prod['survived']} markets** ({prod['pct']}%). "
        f"Trigger: `raw_edge > threshold` OR `base_rate is None` OR `drift`. "
        f"The `BR_NONE` fallback ({prod['BR_NONE']} markets, "
        f"{round(prod['BR_NONE']/prod['flagged']*100) if prod['flagged'] else 0}%) "
        f"causes every market without a heuristic base-rate match to flag automatically. "
        f"This forwards the full filtered set to Claude — the pre-Claude funnel adds no "
        f"probability-based discrimination."
    )
    lines.append("")
    lines.append("### `strict_anomaly_only`")
    lines.append(
        f"Flags **{strict['flagged']} of {strict['survived']} markets** ({strict['pct']}%). "
        f"Trigger: `drift_flag` only (whale_detected would also trigger, but is not available "
        f"at score time — applied post-hoc by main.py). "
        f"Eliminates all base_rate-derived flagging. Only markets where the order-book mid "
        f"has measurably drifted from the last traded price are forwarded to Claude. "
    )
    if strict['flagged'] == 0:
        lines.append(
            f"  **Today's snapshot has 0 drift candidates.** This mode would send nothing "
            f"to Claude on this snapshot — too restrictive without whale data at score time."
        )
    lines.append("")
    lines.append("### `strict_with_heuristic`")
    lines.append(
        f"Flags **{heuristic['flagged']} of {heuristic['survived']} markets** ({heuristic['pct']}%). "
        f"Trigger: `drift_flag` OR `(base_rate is not None AND raw_edge > threshold)`. "
        f"Excludes pure BR_NONE markets (no heuristic match) while keeping markets where "
        f"a known heuristic base rate meaningfully disagrees with the current price. "
        f"This is the practical middle ground: it cuts the BR_NONE noise while preserving "
        f"cases where the scanner has a concrete probability estimate to compare against."
    )

    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(
        f"| Mode | Candidates forwarded to Claude | Assessment |"
    )
    lines.append("|------|-------------------------------|------------|")
    lines.append(
        f"| `passthrough` | {prod['flagged']} | Too many — {prod['BR_NONE']} are BR_NONE noise |"
    )
    lines.append(
        f"| `strict_anomaly_only` | {strict['flagged']} | "
        + ("Too few — 0 signals without drift or whale data" if strict['flagged'] == 0
           else f"{strict['flagged']} — narrow but high-quality")
        + " |"
    )
    lines.append(
        f"| `strict_with_heuristic` | {heuristic['flagged']} | "
        f"{'Recommended — heuristic-edge markets only, no BR_NONE noise' if heuristic['flagged'] > 0 else 'No heuristic matches today'} |"
    )
    lines.append("")

    if heuristic["flagged"] > 0:
        rec_mode = "strict_with_heuristic"
        rec_reason = (
            f"forwards only the {heuristic['flagged']} markets where a heuristic base rate "
            f"meaningfully disagrees with the market price, while silencing the "
            f"{prod['BR_NONE']} BR_NONE catch-all flags. Once whale/drift signals are more "
            f"common (more liquid markets, more intraday runs), strict_anomaly_only becomes "
            f"the cleaner long-term mode."
        )
    else:
        rec_mode = "passthrough"
        rec_reason = (
            f"today's snapshot has 0 drift signals and 0 heuristic edge signals, so "
            f"strict modes would send nothing to Claude. Keep passthrough until more "
            f"signal types are represented."
        )

    lines.append(f"**Recommended mode: `{rec_mode}`** — {rec_reason}")
    lines.append("")
    lines.append(
        "> **Note:** This comparison measures candidate *volume and selectivity* only. "
        "Signal *correctness* — whether the flagged markets are actually mispriced — "
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
        )

    report_path = write_report(results, header)
    print(f"\nReport written: {report_path}")
    return results


if __name__ == "__main__":
    main()
