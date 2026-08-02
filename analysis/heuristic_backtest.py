"""
analysis/heuristic_backtest.py — free (no LLM cost) calibration study of
core.scanner's heuristic base-rate table against Kalshi's real settled-market
corpus (the `settled_markets` table, populated by backtesting/settled_fetcher.py
-- replay-settled-fetcher).

This is NOT the same thing as the (metered, real-money) replay pipeline
(replay-asof-reconstruction / replay-runner / replay-instrument-validation):
those reconstruct each market's *price* as of a historical date and re-score
it with Claude, to test how the live scanner+scorer would have performed.
This script tests something narrower and free: does core.scanner's own
title-keyword base-rate table (estimate_base_rate/get_heuristic_label -- the
first-pass heuristic Claude never even needs to be called for) actually match
reality, independent of any market price? It only needs `title` + the known
`result`, both already sitting in the DB from replay-settled-fetcher, so it
costs nothing to run and can be re-run any time the settled_markets corpus
grows.

Usage:
    python analysis/heuristic_backtest.py
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.logger import DB_PATH
from core.scanner import estimate_base_rate, get_heuristic_label

W = 78
REPORT_PATH = os.path.join(ROOT, "reports", "heuristic_backtest.md")


def _rule(c="="):
    return c * W


def load_settled_markets(db_path: "str | None" = None) -> list[dict]:
    """
    Every settled_markets row with a real binary result. Rows with any other
    result value (voided, multi-outcome, blank -- settled_fetcher stores
    whatever Kalshi returns) are excluded rather than guessed at.
    """
    if db_path is None:
        db_path = DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT ticker, title, category, result
            FROM settled_markets
            WHERE result IN ('YES', 'NO')
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def run_study(markets: list[dict]) -> list[dict]:
    """
    Runs the existing (unmodified) heuristic functions against each settled
    market's title and pairs the prediction with the real outcome. No network
    calls, no Claude -- estimate_base_rate/get_heuristic_label are pure
    title-keyword lookups.
    """
    results = []
    for m in markets:
        base_rate = estimate_base_rate(m)
        label = get_heuristic_label(m)
        actual = 1.0 if m["result"] == "YES" else 0.0
        results.append({
            "ticker":    m["ticker"],
            "category":  m.get("category") or "",
            "base_rate": base_rate,
            "label":     label,
            "actual":    actual,
        })
    return results


def _brier(rows: list[dict], pred_key: str = "base_rate") -> float | None:
    if not rows:
        return None
    return sum((r[pred_key] - r["actual"]) ** 2 for r in rows) / len(rows)


def _directional_accuracy(rows: list[dict]) -> tuple[float | None, int]:
    """
    Accuracy among predictions that actually lean a direction (base_rate !=
    0.5 -- an exact 0.5 heuristic is a coin-flip prior with nothing to grade
    directionally). Returns (accuracy, n_decided).
    """
    decided = [r for r in rows if r["base_rate"] != 0.5]
    if not decided:
        return None, 0
    correct = sum(
        1 for r in decided
        if (r["base_rate"] > 0.5) == (r["actual"] == 1.0)
    )
    return correct / len(decided), len(decided)


def _ece(rows: list[dict], n_bins: int = 10) -> tuple[float | None, list[dict]]:
    """
    Expected Calibration Error, bucketed by predicted probability across the
    WHOLE heuristic table (not per-label -- each label maps to one fixed flat
    rate, so every row within a label shares the identical base_rate and a
    per-label reliability breakdown would be degenerate, collapsing into a
    single bin). Bucketing across labels instead reveals whether predictions
    in the same probability band are collectively well-calibrated in
    aggregate, even when individual labels in that band are individually too
    small (n<10) to trust alone (idea borrowed from
    Quentin-Piot/prediction-market-backtester's reporting/calibration.py,
    2026-08-02 GitHub research -- adapted here since that repo's version
    assumes a continuous per-market model score, not a table of flat rates).

    Returns (ece, bins) where bins is a list of dicts (lo, hi, n,
    avg_predicted, actual_yes_rate) for bins with at least one row.
    ece = sum(n_i/N * |avg_predicted_i - actual_yes_rate_i|) across bins,
    the standard weighted-absolute-gap definition.
    """
    if not rows:
        return None, []
    width = 1.0 / n_bins
    buckets: dict[int, list[dict]] = {}
    for r in rows:
        idx = min(int(r["base_rate"] / width), n_bins - 1)  # base_rate==1.0 -> last bin
        buckets.setdefault(idx, []).append(r)

    total = len(rows)
    bin_rows = []
    ece = 0.0
    for idx in sorted(buckets):
        bucket = buckets[idx]
        n = len(bucket)
        avg_pred = sum(r["base_rate"] for r in bucket) / n
        actual_rate = sum(r["actual"] for r in bucket) / n
        gap = abs(avg_pred - actual_rate)
        ece += (n / total) * gap
        bin_rows.append({
            "lo":              idx * width,
            "hi":              (idx + 1) * width,
            "n":               n,
            "avg_predicted":   avg_pred,
            "actual_yes_rate": actual_rate,
            "gap":             avg_pred - actual_rate,
        })
    return ece, bin_rows


def summarize(results: list[dict]) -> dict:
    total = len(results)
    matched = [r for r in results if r["base_rate"] is not None]
    coverage = (len(matched) / total) if total else 0.0

    naive_yes_rate = (sum(r["actual"] for r in results) / total) if total else None
    naive_rows = [{"base_rate": naive_yes_rate, "actual": r["actual"]} for r in results] \
        if naive_yes_rate is not None else []

    brier = _brier(matched)
    naive_brier = _brier(naive_rows)
    directional_accuracy, n_decided = _directional_accuracy(matched)
    ece, ece_bins = _ece(matched)

    by_label: dict[str, list[dict]] = {}
    for r in matched:
        by_label.setdefault(r["label"] or "?", []).append(r)

    label_rows = []
    for label, rows in by_label.items():
        n = len(rows)
        avg_pred = sum(r["base_rate"] for r in rows) / n
        actual_rate = sum(r["actual"] for r in rows) / n
        label_rows.append({
            "label":           label,
            "n":               n,
            "avg_predicted":   avg_pred,
            "actual_yes_rate": actual_rate,
            "calibration_gap": avg_pred - actual_rate,
            "brier":           _brier(rows),
        })
    label_rows.sort(key=lambda x: -x["n"])

    return {
        "total":                 total,
        "matched":               len(matched),
        "coverage":              coverage,
        "naive_yes_rate":        naive_yes_rate,
        "brier":                 brier,
        "naive_brier":           naive_brier,
        "directional_accuracy":  directional_accuracy,
        "n_decided":             n_decided,
        "by_label":              label_rows,
        "ece":                   ece,
        "ece_bins":              ece_bins,
    }


def _fmt_pct(v) -> str:
    return f"{v * 100:.1f}%" if v is not None else "--"


def _fmt_brier(v) -> str:
    return f"{v:.4f}" if v is not None else "--"


def render_report(summary: dict) -> str:
    lines = []
    lines.append("# Heuristic Backtest — Leviathan v1")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ")
    lines.append(f"**Source:** settled_markets (real Kalshi resolutions, no LLM cost)  ")
    lines.append(f"**Total settled markets (binary result):** {summary['total']}  ")
    lines.append(f"**Heuristic coverage:** {summary['matched']} / {summary['total']} ({_fmt_pct(summary['coverage'])})  ")
    lines.append("")
    lines.append("## Overall calibration")
    lines.append("")
    lines.append(f"- Naive baseline (always predict the population YES-rate, {_fmt_pct(summary['naive_yes_rate'])}): "
                  f"Brier = {_fmt_brier(summary['naive_brier'])}")
    lines.append(f"- Heuristic table (only where a pattern matched): Brier = {_fmt_brier(summary['brier'])}")
    if summary["brier"] is not None and summary["naive_brier"] is not None:
        delta = summary["brier"] - summary["naive_brier"]
        verdict = "heuristics beat the naive baseline" if delta < -0.005 else (
                  "heuristics WORSE than just guessing the population rate" if delta > 0.005 else
                  "no meaningful lift over the naive baseline")
        lines.append(f"- Delta: {delta:+.4f} -> {verdict}")
    lines.append(f"- Directional accuracy (excludes exact-0.5 coin-flip predictions, n={summary['n_decided']}): "
                  f"{_fmt_pct(summary['directional_accuracy'])}")
    lines.append(f"- Expected Calibration Error (10 bins, across the whole table -- not per-label, "
                  f"see note below): {summary['ece']:.4f}" if summary["ece"] is not None else
                  "- Expected Calibration Error: --")
    lines.append("")
    lines.append("## Reliability by predicted-probability bin")
    lines.append("")
    lines.append("Bucketed by predicted probability across ALL labels together, not per-label -- each "
                  "label maps to one fixed flat rate, so every row within a label shares the identical "
                  "prediction and a per-label version of this table would be degenerate (one bin holding "
                  "100% of that label's rows). This view instead asks: of every row this heuristic table "
                  "assigned a ~35% chance to, regardless of which label, how often did YES actually happen?")
    lines.append("")
    lines.append("| Predicted range | n | Avg predicted | Actual YES rate | Gap |")
    lines.append("|---|---|---|---|---|")
    for b in summary["ece_bins"]:
        lines.append(
            f"| {b['lo']*100:.0f}-{b['hi']*100:.0f}% | {b['n']} | {_fmt_pct(b['avg_predicted'])} | "
            f"{_fmt_pct(b['actual_yes_rate'])} | {b['gap']:+.3f} |"
        )
    lines.append("")
    lines.append("## By heuristic label")
    lines.append("")
    lines.append("| Label | n | Avg predicted | Actual YES rate | Calibration gap | Brier |")
    lines.append("|---|---|---|---|---|---|")
    for r in summary["by_label"]:
        lines.append(
            f"| {r['label']} | {r['n']} | {_fmt_pct(r['avg_predicted'])} | "
            f"{_fmt_pct(r['actual_yes_rate'])} | {r['calibration_gap']:+.3f} | {_fmt_brier(r['brier'])} |"
        )
    lines.append("")
    lines.append("Calibration gap = avg predicted YES probability minus actual YES rate. "
                  "Positive = heuristic is overconfident on YES; negative = underconfident on YES "
                  "(overconfident on NO). Labels with few resolved rows (n<10) are noisy -- read "
                  "the gap as a lead, not a verdict, until more settled markets accumulate for that label.")
    lines.append("")
    return "\n".join(lines)


def main():
    print()
    print(_rule())
    print("LEVIATHAN -- HEURISTIC BACKTEST (free, no LLM cost)")
    print(_rule("-"))
    print()

    markets = load_settled_markets()
    if not markets:
        print("  No settled markets with a binary result found. Run")
        print("  backtesting/settled_fetcher.py first to build the corpus.")
        print()
        print(_rule())
        return

    results = run_study(markets)
    summary = summarize(results)

    print(f"  Settled markets (binary result): {summary['total']}")
    print(f"  Heuristic coverage:              {summary['matched']} ({_fmt_pct(summary['coverage'])})")
    print(f"  Naive baseline Brier:            {_fmt_brier(summary['naive_brier'])}  "
          f"(always predict population YES-rate, {_fmt_pct(summary['naive_yes_rate'])})")
    print(f"  Heuristic table Brier:           {_fmt_brier(summary['brier'])}")
    if summary["brier"] is not None and summary["naive_brier"] is not None:
        delta = summary["brier"] - summary["naive_brier"]
        verdict = "beats naive baseline" if delta < -0.005 else (
                  "WORSE than naive baseline" if delta > 0.005 else "no meaningful lift")
        print(f"  Delta vs naive:                  {delta:+.4f}  --> {verdict}")
    print(f"  Directional accuracy (n={summary['n_decided']}):          {_fmt_pct(summary['directional_accuracy'])}")
    if summary["ece"] is not None:
        print(f"  Expected Calibration Error (10 bins):    {summary['ece']:.4f}")
    print()
    print(_rule("="))
    print("RELIABILITY BY PREDICTED-PROBABILITY BIN (across all labels, see report for why)")
    print(_rule("-"))
    print()
    print(f"  {'Range':<10}  {'n':>5}  {'Avg pred':>9}  {'Actual YES':>10}  {'Gap':>7}")
    print(f"  {'-'*10}  {'-'*5}  {'-'*9}  {'-'*10}  {'-'*7}")
    for b in summary["ece_bins"]:
        print(f"  {b['lo']*100:>3.0f}-{b['hi']*100:<3.0f}%   {b['n']:>5}  {_fmt_pct(b['avg_predicted']):>9}  "
              f"{_fmt_pct(b['actual_yes_rate']):>10}  {b['gap']:>+7.3f}")
    print()
    print(_rule("="))
    print("BY HEURISTIC LABEL")
    print(_rule("-"))
    print()
    print(f"  {'Label':<30}  {'n':>5}  {'Avg pred':>9}  {'Actual YES':>10}  {'Gap':>7}  {'Brier':>7}")
    print(f"  {'-'*30}  {'-'*5}  {'-'*9}  {'-'*10}  {'-'*7}  {'-'*7}")
    for r in summary["by_label"]:
        print(f"  {r['label'][:30]:<30}  {r['n']:>5}  {_fmt_pct(r['avg_predicted']):>9}  "
              f"{_fmt_pct(r['actual_yes_rate']):>10}  {r['calibration_gap']:>+7.3f}  {_fmt_brier(r['brier']):>7}")
    print()
    print("  Calibration gap = avg predicted YES prob minus actual YES rate.")
    print("  Positive = overconfident on YES. Labels with n<10 are noisy -- a lead, not a verdict.")
    print()
    print(_rule())
    print()

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(render_report(summary))
    print(f"  Full report written -> {REPORT_PATH}")
    print()


if __name__ == "__main__":
    main()
