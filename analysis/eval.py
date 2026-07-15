"""
analysis/eval.py — Leviathan eval harness entry point (Goal 6, item 1).

Grades the scorer's original at-signal-time estimates against resolved
ground truth, alongside two baselines — the market price at signal time,
and a constant 0.5 — so "is the edge real" is a number instead of an
impression. Uses the frozen eval dataset (analysis/eval_dataset.py,
sourced through the MCP server's resolved-outcomes tool) and the
deterministic grader (analysis/eval_grader.py). No model calls, no
network, no cost.

For the separate re-scoring / prompt-reproducibility harness (which
does call the live API and costs money), see analysis/eval_rescore.py —
that is NOT run as part of this default entry point.

Usage:
    python analysis/eval.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis.eval_dataset import load_latest
from analysis import eval_grader

W = 70


def _rule(c="="):
    return c * W


def three_way_comparison(dataset: dict) -> dict:
    """Grades the scorer's estimates, the market price, and a constant-0.5 baseline."""
    rows = dataset["rows"]
    scorer_pairs   = [(r["our_estimate"], r["actual_outcome_binary"]) for r in rows]
    market_pairs   = [(r["market_price"], r["actual_outcome_binary"]) for r in rows]
    constant_pairs = [(0.5, r["actual_outcome_binary"]) for r in rows]

    return {
        "scorer":   eval_grader.grade(scorer_pairs),
        "market":   eval_grader.grade(market_pairs),
        "constant": eval_grader.grade(constant_pairs),
    }


def main():
    dataset = load_latest()
    n = dataset["n"]

    print()
    print(_rule())
    print("LEVIATHAN EVAL HARNESS")
    print(_rule("-"))
    print()
    print(f"Dataset version: {dataset['version']}  (n={n} resolved signals)")
    print(f"Source: {dataset['source']}")
    print()

    comparison = three_way_comparison(dataset)

    print("THREE-WAY BRIER COMPARISON")
    print(_rule("-"))
    print(f"  {'Baseline':<12}  {'Brier':>8}  {'Hit rate':>9}")
    print(f"  {'-'*12}  {'-'*8}  {'-'*9}")
    for label, key in (("Scorer", "scorer"), ("Market price", "market"), ("Constant 0.5", "constant")):
        r = comparison[key]
        brier_s = f"{r['brier']:.4f}" if r["brier"] is not None else "N/A"
        hit_s   = f"{r['hit_rate']*100:.0f}%" if r["hit_rate"] is not None else "N/A"
        print(f"  {label:<12}  {brier_s:>8}  {hit_s:>9}")
    print()

    scorer_brier = comparison["scorer"]["brier"]
    market_brier = comparison["market"]["brier"]
    if scorer_brier is not None and market_brier is not None:
        if scorer_brier < market_brier:
            verdict = "scorer beats market price"
        elif scorer_brier > market_brier:
            verdict = "market price beats scorer"
        else:
            verdict = "tied"
        print(f"  Verdict: {verdict} (scorer {scorer_brier:.4f} vs market {market_brier:.4f})")
    print()

    print("CALIBRATION BY ESTIMATE DECILE (scorer)")
    print(_rule("-"))
    deciles = comparison["scorer"]["calibration_by_decile"]
    if deciles:
        print(f"  {'Range':<10}  {'n':>3}  {'Mean est':>9}  {'Actual rate':>11}")
        print(f"  {'-'*10}  {'-'*3}  {'-'*9}  {'-'*11}")
        for b in deciles:
            print(f"  {b['range']:<10}  {b['n']:>3}  {b['mean_estimate']:>9.3f}  {b['actual_rate']:>11.3f}")
    else:
        print("  No resolved data yet.")
    print()

    print(_rule("="))
    if scorer_brier is not None:
        print(f"HEADLINE: scorer Brier = {scorer_brier:.4f}  (n={n})")
    else:
        print("HEADLINE: no resolved data yet")
    print(_rule("="))
    print()

    if n < 20:
        print(f"NOTE: n={n} is far below the n=20 calibration gate. This is an integrity")
        print("checkpoint, not a performance claim - read every number here with that caveat.")
        print()


if __name__ == "__main__":
    main()
