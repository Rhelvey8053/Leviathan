"""
backtesting/base_rates.py - Empirical base rates module for Leviathan.

Scaffold for replacing heuristic prior rates with empirical rates derived
from Polymarket historical outcomes. No live data fetch — receives data
from the backtest harness once it produces results.

Usage:
  python backtesting/base_rates.py --empirical empirical_rates.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


# Heuristic prior base rates keyed by flag_path / heuristic category.
# Values sourced from scanner.py estimate_base_rate() table.
BASE_RATES: dict[str, float] = {
    "EDGE":           0.52,
    "DRIFT":          0.54,
    "HEURISTIC":      0.52,
    "BR_NONE":        0.50,
    "UNKNOWN":        0.50,
    "SPORTS_WIN":     0.50,
    "ELECTION_WIN":   0.52,
    "REELECTION":     0.52,
    "PRIMARY_CHAL":   0.30,
    "SCOTUS":         0.40,
    "CONFLICT":       0.35,
    "ECON_INDICATOR": 0.50,
    "CELEBRITY":      0.40,
    "LEGISLATION":    0.35,
    "IMPEACHMENT":    0.15,
    "MEDIA_RELEASE":  0.65,
    "FED_POLICY":     0.50,
    "INTL_RELATIONS": 0.35,
}


def load_empirical_rates(path: str) -> tuple[dict[str, float], dict[str, int]]:
    """
    Reads a CSV with columns: category, resolved, hits.
    Returns (rates_dict, ns_dict) where rates = hits/resolved per category.
    Rows with resolved=0 are skipped.
    """
    rates: dict[str, float] = {}
    ns:    dict[str, int]   = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cat = row["category"].strip()
            try:
                n = int(row["resolved"])
                h = int(row["hits"])
            except (ValueError, KeyError):
                continue
            if n > 0:
                rates[cat] = h / n
                ns[cat]    = n
    return rates, ns


def merge_rates(
    priors:   dict[str, float],
    empirical: dict[str, float],
    min_n:    int = 15,
    empirical_ns: dict[str, int] | None = None,
) -> dict[str, float]:
    """
    Merge prior rates with empirical rates using shrinkage-lite:
    replace a prior only when the empirical sample meets min_n.
    If empirical_ns is None, assumes all empirical entries satisfy min_n.
    """
    merged = dict(priors)
    for cat, rate in empirical.items():
        n = empirical_ns.get(cat, min_n) if empirical_ns else min_n
        if n >= min_n:
            merged[cat] = rate
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Leviathan empirical base rates")
    parser.add_argument("--empirical", required=True, metavar="PATH",
                        help="CSV with columns: category, resolved, hits")
    args = parser.parse_args()

    emp_rates, emp_ns = load_empirical_rates(args.empirical)
    merged = merge_rates(BASE_RATES, emp_rates, min_n=15, empirical_ns=emp_ns)

    all_cats = sorted(set(BASE_RATES) | set(emp_rates))
    header = f"{'Category':<20}  {'Prior':>6}  {'Empirical':>9}  {'N':>4}  {'Merged':>6}  Used"
    print(header)
    print("-" * len(header))
    for cat in all_cats:
        prior = BASE_RATES.get(cat)
        emp   = emp_rates.get(cat)
        n     = emp_ns.get(cat, 0)
        mrg   = merged.get(cat)
        used  = "empirical" if emp is not None and n >= 15 else "prior"
        prior_s = f"{prior:.0%}" if prior is not None else "—"
        emp_s   = f"{emp:.0%}"   if emp   is not None else "—"
        mrg_s   = f"{mrg:.0%}"   if mrg   is not None else "—"
        print(f"{cat:<20}  {prior_s:>6}  {emp_s:>9}  {n:>4}  {mrg_s:>6}  {used}")


if __name__ == "__main__":
    main()
