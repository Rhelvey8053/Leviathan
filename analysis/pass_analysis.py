"""
Scanner precision analysis — PASS rate by flag path, time horizon, and ticker.

Reads from leviathan.db. Shows where the scanner is wasting Claude's attention
on markets that consistently get PASS decisions. No network calls; no Claude CLI.

Usage:
    python analysis/pass_analysis.py
    python analysis/pass_analysis.py --days 30    # extend look-back window
"""

import os
import sys
import argparse
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import logger

W = 70


def _rule(c="="):
    return c * W


def _pct(num, den, default="—"):
    if den == 0:
        return default
    return f"{num / den * 100:.0f}%"


def main(days: int = 14):
    print()
    print(_rule())
    print("LEVIATHAN — SCANNER PASS-RATE ANALYSIS")
    print(f"(last {days} days)")
    print(_rule("-"))
    print()

    # Pull all paper rows (YES, NO, PASS) from the look-back window
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        with logger._db() as conn:
            rows = conn.execute(
                "SELECT ticker, direction, flag_path, time_horizon, heuristic_direction, "
                "       confidence, timestamp "
                "FROM signals "
                "WHERE (source = 'paper' OR source IS NULL) "
                "  AND timestamp >= ? "
                "ORDER BY timestamp DESC",
                (cutoff,),
            ).fetchall()
    except Exception as e:
        print(f"  ERROR reading DB: {e}")
        return

    rows = [dict(r) for r in rows]
    total_all   = len(rows)
    pass_rows   = [r for r in rows if r["direction"] == "PASS"]
    signal_rows = [r for r in rows if r["direction"] in ("YES", "NO")]

    print(f"  Total rows (paper, {days}d):  {total_all}")
    print(f"  Actionable signals (Y/N):     {len(signal_rows)}")
    print(f"  PASS decisions:               {len(pass_rows)}")
    overall_pass_rate = _pct(len(pass_rows), total_all)
    print(f"  Overall PASS rate:            {overall_pass_rate}")
    print()

    if not rows:
        print("  No data yet. Run main.py first to accumulate paper signals.")
        print()
        print(_rule())
        return

    # ── PASS rate by flag path ─────────────────────────────────────────────────
    print(_rule("="))
    print("PASS RATE BY FLAG PATH  (high = scanner wasting Claude's time)")
    print(_rule("-"))
    print()

    by_path: dict = defaultdict(lambda: {"total": 0, "pass": 0, "yes": 0, "no": 0})
    for r in rows:
        fp = r["flag_path"] or "UNKNOWN"
        by_path[fp]["total"] += 1
        d = r["direction"]
        if d == "PASS":
            by_path[fp]["pass"] += 1
        elif d == "YES":
            by_path[fp]["yes"] += 1
        elif d == "NO":
            by_path[fp]["no"] += 1

    print(f"  {'Flag Path':<16}  {'Total':>5}  {'Y/N':>5}  {'PASS':>5}  {'PASS%':>6}")
    print(f"  {'-'*16}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*6}")
    for fp, d in sorted(by_path.items(), key=lambda x: -(x[1]["pass"] / max(x[1]["total"], 1))):
        yn = d["yes"] + d["no"]
        print(f"  {fp:<16}  {d['total']:>5}  {yn:>5}  {d['pass']:>5}  "
              f"{_pct(d['pass'], d['total']):>6}")
    print()

    # ── PASS rate by time horizon ──────────────────────────────────────────────
    print(_rule("="))
    print("PASS RATE BY TIME HORIZON  (INTRADAY high = short-horizon gate working)")
    print(_rule("-"))
    print()

    by_horizon: dict = defaultdict(lambda: {"total": 0, "pass": 0})
    for r in rows:
        th = r["time_horizon"] or "UNKNOWN"
        by_horizon[th]["total"] += 1
        if r["direction"] == "PASS":
            by_horizon[th]["pass"] += 1

    bucket_order = ["INTRADAY", "WEEKLY", "MONTHLY", "QUARTERLY", "LONG", "UNKNOWN"]
    print(f"  {'Horizon':<12}  {'Total':>5}  {'PASS':>5}  {'PASS%':>6}")
    print(f"  {'-'*12}  {'-'*5}  {'-'*5}  {'-'*6}")
    for th in bucket_order:
        d = by_horizon.get(th)
        if not d or not d["total"]:
            continue
        print(f"  {th:<12}  {d['total']:>5}  {d['pass']:>5}  {_pct(d['pass'], d['total']):>6}")
    print()

    # ── Repeat-PASS tickers ────────────────────────────────────────────────────
    print(_rule("="))
    print("SYSTEMATIC PASS TICKERS  (≥2 PASSes — likely scanner false-positives)")
    print(_rule("-"))
    print()

    ticker_pass: dict = defaultdict(int)
    ticker_signal: dict = defaultdict(int)
    for r in pass_rows:
        ticker_pass[r["ticker"]] += 1
    for r in signal_rows:
        ticker_signal[r["ticker"]] += 1

    repeat_pass = {t: c for t, c in ticker_pass.items() if c >= 2}
    if repeat_pass:
        print(f"  {'Ticker':<32}  {'PASS':>4}  {'Y/N':>4}  Note")
        print(f"  {'-'*32}  {'-'*4}  {'-'*4}  ----")
        for ticker, pass_ct in sorted(repeat_pass.items(), key=lambda x: -x[1]):
            yn = ticker_signal.get(ticker, 0)
            note = "ALL PASS" if yn == 0 else (
                "mostly PASS" if pass_ct > yn else "mixed"
            )
            print(f"  {ticker:<32}  {pass_ct:>4}  {yn:>4}  {note}")
        print()
        print(f"  Total repeat-PASS tickers: {len(repeat_pass)}")
        print(f"  Consider raising scanner thresholds for their flag_path categories,")
        print(f"  or adding these tickers to a scanner exclusion list.")
    else:
        print("  No repeat-PASS tickers found in this window.")
    print()

    # ── Heuristic direction vs PASS ────────────────────────────────────────────
    by_hd: dict = defaultdict(lambda: {"total": 0, "pass": 0})
    for r in rows:
        hd = r["heuristic_direction"] or "NONE"
        by_hd[hd]["total"] += 1
        if r["direction"] == "PASS":
            by_hd[hd]["pass"] += 1

    any_hd = any(d["total"] > 0 for d in by_hd.values())
    if any_hd:
        print(_rule("="))
        print("PASS RATE BY HEURISTIC DIRECTION  (NEUTRAL high = heuristic misaligned)")
        print(_rule("-"))
        print()
        print(f"  {'Heuristic Dir':<14}  {'Total':>5}  {'PASS':>5}  {'PASS%':>6}")
        print(f"  {'-'*14}  {'-'*5}  {'-'*5}  {'-'*6}")
        for hd, d in sorted(by_hd.items(), key=lambda x: -x[1]["total"]):
            if not d["total"]:
                continue
            print(f"  {hd:<14}  {d['total']:>5}  {d['pass']:>5}  {_pct(d['pass'], d['total']):>6}")
        print()

    # ── Summary verdict ───────────────────────────────────────────────────────
    print(_rule("="))
    print("SCANNER PRECISION VERDICT")
    print(_rule("-"))
    print()
    if total_all == 0:
        print("  No data yet.")
    else:
        pass_pct = len(pass_rows) / total_all * 100
        if pass_pct < 30:
            verdict = "GOOD — scanner is fairly precise, Claude is mostly seeing real edge."
        elif pass_pct < 60:
            verdict = "FAIR — consider raising volume/drift/edge thresholds for high-PASS paths."
        else:
            verdict = "POOR — scanner over-flags. Raise thresholds or add flag-path filters."
        print(f"  Pass rate: {pass_pct:.0f}%  →  {verdict}")
        top_waste = sorted(by_path.items(), key=lambda x: -(x[1]["pass"]))[:2]
        if top_waste:
            paths = ", ".join(fp for fp, _ in top_waste)
            print(f"  Top wasted paths: {paths}")
    print()
    print(_rule())
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14,
                        help="Look-back window in days (default: 14)")
    args = parser.parse_args()
    main(days=args.days)
