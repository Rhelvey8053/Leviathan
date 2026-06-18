"""
Calibration analysis for Leviathan paper signals.

Reads from leviathan.db -- no network calls, no Claude CLI.
Shows win rate, P&L, and Brier score broken down by every dimension
that's tracked in the DB. Run after signals have resolved.

Usage:
    python analysis/calibration.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import logger

W = 70


def _rule(c="="):
    return c * W


def _pnl(v, per_contract=10.0):
    try:
        return f"${float(v) * per_contract:+.2f}"
    except Exception:
        return "--"


def _wr(v):
    return f"{float(v):.0f}%" if v is not None else "--"


def _edge(v):
    return f"{float(v)*100:.1f}pp" if v is not None else "--"


def _print_table(rows, key_label="Group", key_col="flag_path"):
    print(f"  {key_label:<18}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L ($10)':>10}  {'Avg Edge':>8}")
    print(f"  {'-'*18}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*10}  {'-'*8}")
    for r in rows:
        key   = str(r.get(key_col) or r.get("flag_path") or "?")
        total = r.get("total", 0)
        wins  = r.get("wins", 0)
        if not total:
            continue
        print(f"  {key:<18}  {total:>5}  {wins:>4}  {_wr(r.get('win_rate')):>6}"
              f"  {_pnl(r.get('total_pnl')):>10}  {_edge(r.get('avg_edge')):>8}")


def main():
    print()
    print(_rule())
    print("LEVIATHAN -- CALIBRATION ANALYSIS")
    print(_rule("-"))
    print()

    # Overall stats
    stats = logger.get_stats()
    brier = logger.get_brier_score()
    print(f"  Total paper signals:  {stats['total_calls']}")
    print(f"  Resolved:             {stats['resolved']}")
    print(f"  Win rate:             {_wr(stats['win_rate'])}")
    print(f"  Hypothetical P&L:     {_pnl(stats['total_hypothetical_pnl'])}")
    bs = brier.get("brier_score")
    bs_n = brier.get("n", 0)
    bs_label = brier.get("label", "PENDING")
    if bs is not None:
        print(f"  Brier Score:          {bs:.4f}  ({bs_label}, n={bs_n})")
        print(f"  [0=perfect, 0.25=random, >0.25=poor calibration]")
    else:
        print(f"  Brier Score:          PENDING -- no resolved signals yet")

    if stats["resolved"] == 0:
        print()
        print("  No resolved signals yet. Run again after markets settle.")
        print()
        print(_rule())
        return

    # Flag path breakdown
    print()
    print(_rule("="))
    print("BY FLAG PATH")
    print(_rule("-"))
    print()
    fp_rows = logger.get_stats_by_flag_path()
    if fp_rows:
        _print_table(fp_rows, key_label="Flag Path", key_col="flag_path")
    else:
        print("  No resolved data.")

    # Signal type breakdown
    print()
    print(_rule("="))
    print("BY SIGNAL TYPE (can overlap)")
    print(_rule("-"))
    print()
    sig = logger.get_stats_by_sig()
    sig_rows = [
        {"flag_path": "EDGE (sig_edge)",    **sig["sig_edge"]},
        {"flag_path": "DRIFT (sig_drift)",  **sig["sig_drift"]},
        {"flag_path": "BR_NONE (sig_br_none)", **sig["sig_br_none"]},
    ]
    _print_table([r for r in sig_rows if r.get("total", 0) > 0],
                 key_label="Signal Type", key_col="flag_path")

    # Confidence breakdown
    print()
    print(_rule("="))
    print("BY CONFIDENCE")
    print(_rule("-"))
    print()
    conf = logger.get_stats_by_confidence()
    conf_rows = [
        {"flag_path": lvl, **conf[lvl]}
        for lvl in ("HIGH", "MED", "LOW")
        if conf[lvl]["total"] > 0
    ]
    if conf_rows:
        _print_table(conf_rows, key_label="Confidence", key_col="flag_path")
    else:
        print("  No resolved data.")

    # Time horizon breakdown
    print()
    print(_rule("="))
    print("BY TIME HORIZON")
    print(_rule("-"))
    print()
    th = logger.get_stats_by_time_horizon()
    th_rows = [
        {"flag_path": bucket, **th[bucket]}
        for bucket in ("INTRADAY", "WEEKLY", "MONTHLY", "QUARTERLY", "LONG")
        if th[bucket]["total"] > 0
    ]
    if th_rows:
        _print_table(th_rows, key_label="Horizon", key_col="flag_path")
        # Highlight short vs long horizon aggregate
        short_total = th["INTRADAY"]["total"] + th["WEEKLY"]["total"]
        short_wins  = th["INTRADAY"]["wins"]  + th["WEEKLY"]["wins"]
        long_total  = sum(th[b]["total"] for b in ("MONTHLY", "QUARTERLY", "LONG"))
        long_wins   = sum(th[b]["wins"]  for b in ("MONTHLY", "QUARTERLY", "LONG"))
        if short_total and long_total:
            short_wr = short_wins / short_total * 100
            long_wr  = long_wins  / long_total  * 100
            delta    = short_wr - long_wr
            verdict  = "short outperforms" if delta > 5 else (
                       "long outperforms" if delta < -5 else "no meaningful difference")
            print(f"\n  Short (≤7d) vs Long (>7d): {short_wr:.0f}% vs {long_wr:.0f}%  → {verdict}")
    else:
        print("  No resolved data.")

    # Heuristic alignment breakdown
    print()
    print(_rule("="))
    print("BY HEURISTIC ALIGNMENT  (does Claude outperform when it overrides?)")
    print(_rule("-"))
    print()
    align = logger.get_stats_by_heuristic_alignment()
    align_rows = [
        {"flag_path": grp.replace("_", " ").title(), **align[grp]}
        for grp in ("aligned", "override", "no_heuristic")
        if align[grp]["total"] > 0
    ]
    if align_rows:
        _print_table(align_rows, key_label="Alignment Group", key_col="flag_path")
        ov = align["override"]
        al = align["aligned"]
        if ov["total"] and al["total"] and ov["win_rate"] is not None and al["win_rate"] is not None:
            delta   = ov["win_rate"] - al["win_rate"]
            verdict = "overrides outperform (justified)" if delta > 5 else (
                      "overrides underperform (revert to heuristic)" if delta < -5 else
                      "no meaningful difference")
            print(f"\n  Override vs Aligned win-rate delta: {delta:+.0f}pp  → {verdict}")
    else:
        print("  No resolved data.")

    # Brier score by confidence
    print()
    print(_rule("="))
    print("CALIBRATION NOTES")
    print(_rule("-"))
    print()
    if bs is not None and bs <= 0.10:
        print("  Calibration: EXCELLENT -- estimates closely track true probabilities.")
    elif bs is not None and bs <= 0.20:
        print("  Calibration: GOOD -- small systematic over/under-confidence to watch.")
    elif bs is not None:
        print("  Calibration: FAIR/POOR -- large forecast errors, review base rates.")
    else:
        print("  Calibration: PENDING -- no resolved signals.")
    print()
    print("  Key questions to investigate when data accumulates:")
    print("    1. Do HEURISTIC-flagged signals outperform DRIFT-flagged?")
    print("    2. Do Aligned signals beat Override signals?")
    print("    3. Do MONTHLY+ signals outperform WEEKLY/INTRADAY?")
    print("    4. Is HIGH confidence actually higher win-rate than MED?")
    print()
    print(_rule())
    print()


if __name__ == "__main__":
    main()
