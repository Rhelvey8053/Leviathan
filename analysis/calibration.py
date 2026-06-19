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

from core import logger

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

    # Net-of-spread edge breakdown
    print()
    print(_rule("="))
    print("BY NET EDGE  (does realizable edge predict win rate?)")
    print(_rule("-"))
    print()
    ne = logger.get_stats_by_net_edge()
    bucket_labels = {
        "spread_dominant": "spread > edge",
        "thin":            "0-5pp net edge",
        "good":            "5-10pp net edge",
        "strong":          ">10pp net edge",
        "no_data":         "no spread data",
    }
    ne_rows = [
        {"flag_path": bucket_labels[b], **ne[b]}
        for b in ("spread_dominant", "thin", "good", "strong", "no_data")
        if ne[b]["total"] > 0
    ]
    if ne_rows:
        _print_table(ne_rows, key_label="Net Edge Bucket", key_col="flag_path")
        thin_plus = {b: ne[b] for b in ("thin", "good", "strong")}
        sd = ne["spread_dominant"]
        tp_total = sum(d["total"] for d in thin_plus.values())
        tp_wins  = sum(d["wins"]  for d in thin_plus.values())
        if tp_total and sd["total"] and sd["win_rate"] is not None:
            tp_wr   = tp_wins / tp_total * 100
            sd_wr   = sd["win_rate"]
            delta   = tp_wr - sd_wr
            verdict = "positive net-edge wins more (spread filter justified)" if delta > 5 else (
                      "spread-dominant wins more (spread filter may be too harsh)" if delta < -5 else
                      "no meaningful difference yet")
            print(f"\n  Tradeable (net>0) vs Spread-dominant: {tp_wr:.0f}% vs {sd_wr:.0f}%  --> {verdict}")
    else:
        print("  No resolved data.")

    # Close-horizon breakdown
    print()
    print(_rule("="))
    print("BY ACTUAL DAYS-TO-CLOSE  (when did we signal relative to resolution?)")
    print(_rule("-"))
    print()
    ch = logger.get_stats_by_close_horizon()
    ch_labels = {
        "urgent":   "<1 day to close",
        "short":    "1-7 days to close",
        "medium":   "7-30 days to close",
        "long":     "30+ days to close",
        "no_close": "no close_time recorded",
    }
    ch_rows = [
        {"flag_path": ch_labels[b], **ch[b]}
        for b in ("urgent", "short", "medium", "long", "no_close")
        if ch[b]["total"] > 0
    ]
    if ch_rows:
        _print_table(ch_rows, key_label="Close Horizon", key_col="flag_path")
        print()
        print("  Key question #6: Do shorter-horizon signals (urgent/short) have")
        print("  higher win rates than long-horizon signals — or vice versa?")
    else:
        print("  No resolved data yet (close_time field added recently).")

    # Whale detection breakdown
    print()
    print(_rule("="))
    print("BY WHALE ACTIVITY  (does whale detection predict wins?)")
    print(_rule("-"))
    print()
    wh = logger.get_stats_by_whale()
    wh_rows = [
        {"flag_path": "Whale detected",  **wh["whale"]},
        {"flag_path": "No whale signal", **wh["no_whale"]},
    ]
    wh_rows = [r for r in wh_rows if r.get("total", 0) > 0]
    if wh_rows:
        _print_table(wh_rows, key_label="Whale Group", key_col="flag_path")
        w_wr  = wh["whale"]["win_rate"]
        nw_wr = wh["no_whale"]["win_rate"]
        if w_wr is not None and nw_wr is not None:
            delta   = w_wr - nw_wr
            verdict = "whale detection predictive (keep scan)" if delta > 5 else (
                      "no whale edge (scan may not be worth cost)" if delta < -5 else
                      "no meaningful difference yet")
            print(f"\n  Whale vs No-whale win-rate: {w_wr:.0f}% vs {nw_wr:.0f}%  --> {verdict}")
    else:
        print("  No resolved data.")

    # Watchlist / smart money breakdown
    print()
    print(_rule("="))
    print("BY WATCHLIST / SMART MONEY  (do top-trader positions predict wins?)")
    print(_rule("-"))
    print()
    wl = logger.get_stats_by_watchlist()
    wl_rows = [
        {"flag_path": "Watchlist aligned",    **wl["watchlist"]},
        {"flag_path": "No watchlist signal",  **wl["no_watchlist"]},
    ]
    wl_rows = [r for r in wl_rows if r.get("total", 0) > 0]
    if wl_rows:
        _print_table(wl_rows, key_label="Smart Money", key_col="flag_path")
        wl_wr  = wl["watchlist"]["win_rate"]
        nwl_wr = wl["no_watchlist"]["win_rate"]
        if wl_wr is not None and nwl_wr is not None:
            delta   = wl_wr - nwl_wr
            verdict = "smart money predictive (keep watchlist)" if delta > 5 else (
                      "watchlist underperforms (re-examine tracking criteria)" if delta < -5 else
                      "no meaningful difference yet")
            print(f"\n  Watchlist vs No-watchlist win-rate: {wl_wr:.0f}% vs {nwl_wr:.0f}%  --> {verdict}")
    else:
        print("  No resolved data.")

    # PASS rate by flag path (false-positive detector)
    print()
    print(_rule("="))
    print("PASS RATE BY FLAG PATH  (which scanner categories are false-positive factories?)")
    print(_rule("-"))
    print()
    pr = logger.get_pass_rate_by_flag_path()
    if pr:
        print(f"  {'Flag Path':<18}  {'Total':>5}  {'Passed':>6}  {'Acted':>5}  {'Pass%':>6}  {'Act%':>5}")
        print(f"  {'-'*18}  {'-'*5}  {'-'*6}  {'-'*5}  {'-'*6}  {'-'*5}")
        for r in pr:
            fp  = str(r.get("flag_path") or "?")
            tot = r.get("total", 0)
            pas = r.get("passed", 0)
            act = r.get("acted", 0)
            pct = r.get("pass_rate")
            apt = r.get("act_rate")
            pct_s = f"{pct:.0f}%" if pct is not None else "--"
            apt_s = f"{apt:.0f}%" if apt is not None else "--"
            print(f"  {fp:<18}  {tot:>5}  {pas:>6}  {act:>5}  {pct_s:>6}  {apt_s:>5}")
        print()
        print("  High PASS rate (>70%) = scanner is generating lots of noise in this category.")
        print("  If a category consistently has >80% PASS rate, consider tightening its filter.")
    else:
        print("  No signal data yet.")

    # Leviathan Score band breakdown
    print()
    print(_rule("="))
    print("BY LEVIATHAN SCORE BAND  (does A-grade beat D-grade?)")
    print(_rule("-"))
    print()
    lv = logger.get_stats_by_leviathan_score()
    lv_labels = {
        "A":        "A (score >=70)",
        "B":        "B (score 55-69)",
        "C":        "C (score 40-54)",
        "D":        "D (score <40)",
        "unscored": "pre-LV (no score)",
    }
    lv_rows = [
        {"flag_path": lv_labels[b], **lv[b]}
        for b in ("A", "B", "C", "D", "unscored")
        if lv[b]["total"] > 0
    ]
    if lv_rows:
        _print_table(lv_rows, key_label="LV Band", key_col="flag_path")
        a_wr = lv["A"]["win_rate"]
        d_wr = lv["D"]["win_rate"]
        if a_wr is not None and d_wr is not None:
            delta   = a_wr - d_wr
            verdict = "A-grade outperforms D (score validated)" if delta > 10 else (
                      "D-grade outperforms A (re-examine score rubric)" if delta < -10 else
                      "no meaningful difference yet")
            print(f"\n  Grade A vs D win-rate: {a_wr:.0f}% vs {d_wr:.0f}%  --> {verdict}")
    else:
        print("  No resolved data yet (leviathan_score field added recently).")

    # Heuristic label breakdown
    print()
    print(_rule("="))
    print("BY HEURISTIC LABEL  (which category has the best calibration?)")
    print(_rule("-"))
    print()
    hl = logger.get_stats_by_heuristic_label()
    if hl:
        print(f"  {'Heuristic Label':<30}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L ($10)':>10}  {'Avg Edge':>8}")
        print(f"  {'-'*30}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*10}  {'-'*8}")
        for r in hl:
            lbl   = str(r.get("heuristic_label") or "?")[:30]
            total = r.get("total", 0)
            wins  = r.get("wins", 0)
            if not total:
                continue
            print(f"  {lbl:<30}  {total:>5}  {wins:>4}  {_wr(r.get('win_rate')):>6}"
                  f"  {_pnl(r.get('total_pnl')):>10}  {_edge(r.get('avg_edge')):>8}")
        print()
        print("  Labels with >=3 resolved signals and win_rate >60% are well-calibrated categories.")
        print("  Labels with win_rate <40% suggest base rate may need adjustment for that category.")
    else:
        print("  No resolved data with heuristic labels yet.")

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
    print("    5. Do signals with positive net_edge (tradeable) outperform spread-dominant?")
    print("    6. Do urgent (<1d) or short (1-7d) signals have better win rates than long (30d+)?")
    print("       (Long-horizon mispricings may reflect structural anchoring vs. short-horizon is just noise)")
    print("    7. Do LV-band-A signals have higher win rates than band-D signals?")
    print("       (Validates composite scoring rubric — if not, reweight the rubric components)")
    print("    8. Does whale detection add win rate above the no-whale baseline?")
    print("       (If whale signals do NOT outperform, the whale scan cost is not justified)")
    print("    9. Does watchlist/smart money alignment predict wins above the base rate?")
    print("       (If no meaningful lift, reconsider watchlist tracking criteria)")
    print("   10. Which flag paths have the highest PASS rate?")
    print("       (PASS rate >80% in a category = scanner noise; consider tightening that filter)")
    print()
    print(_rule())
    print()


if __name__ == "__main__":
    main()
