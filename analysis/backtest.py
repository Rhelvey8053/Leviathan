"""
Track record and P&L summary for Leviathan signals.

Runs resolve_outcomes against Kalshi for any unresolved rows, then prints
a full breakdown: paper signals, research probes, and real fills.

Usage:
    python analysis/backtest.py          # resolve + report
    python analysis/backtest.py --no-resolve   # skip API, report only
"""

import os
import sys
import json
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import logger

W = 70


def _rule(c="="):
    return c * W


def _pct(v, default="—"):
    try:
        return f"{float(v)*100:.1f}%"
    except Exception:
        return default


def _fmt_pnl(v, per_contract=10.0):
    """
    Convert per-$1 PnL fraction to dollar-based P&L assuming $10/contract.
    """
    try:
        return f"${float(v) * per_contract:+.2f}"
    except Exception:
        return "—"


def _print_section(title: str, rows: list[dict]) -> None:
    if not rows:
        print(f"\n  (no {title.lower()} rows)")
        return

    resolved   = [r for r in rows if r.get("outcome")]
    unresolved = [r for r in rows if not r.get("outcome")]
    wins       = [r for r in resolved if r.get("result") == "WIN"]
    losses     = [r for r in resolved if r.get("result") == "LOSS"]
    total_pnl  = sum(float(r["pnl_if_traded"] or 0) for r in resolved if r.get("pnl_if_traded") is not None)
    win_rate   = (len(wins) / len(resolved) * 100) if resolved else None

    print(f"\n  Total:       {len(rows)}")
    print(f"  Resolved:    {len(resolved)}  ({len(unresolved)} pending)")
    print(f"  Wins:        {len(wins)}")
    print(f"  Losses:      {len(losses)}")
    print(f"  Win rate:    {f'{win_rate:.1f}%' if win_rate is not None else '— (none resolved)'}")
    print(f"  Net PnL:     {_fmt_pnl(total_pnl)} (at $10/contract)")

    if resolved:
        print()
        print(f"  {'Ticker':<30}  {'Dir':<3}  {'Price':>6}  {'Out':<3}  {'Result':<4}  {'PnL':>8}  Conf")
        print(f"  {'-'*30}  {'-'*3}  {'-'*6}  {'-'*3}  {'-'*4}  {'-'*8}  ----")
        for r in sorted(resolved, key=lambda x: x.get("timestamp", ""), reverse=True):
            ticker  = (r.get("ticker") or "")[:30]
            dir_    = (r.get("direction") or "?")[:3]
            price   = _pct(r.get("market_price") or r.get("market_price_at_probe"), "—")
            out     = (r.get("outcome") or "?")[:3]
            result  = (r.get("result") or "?")[:4]
            pnl     = _fmt_pnl(r.get("pnl_if_traded"))
            conf    = (r.get("confidence") or "")[:4]
            print(f"  {ticker:<30}  {dir_:<3}  {price:>6}  {out:<3}  {result:<4}  {pnl:>8}  {conf}")


def _probe_breakdown(rows: list[dict]) -> None:
    """Extra breakdown for research probes — divergence analysis."""
    resolved = [r for r in rows if r.get("outcome") and r.get("divergence") is not None]
    if not resolved:
        return

    # Sort by divergence magnitude
    by_div = sorted(resolved, key=lambda r: abs(float(r.get("divergence") or 0)), reverse=True)

    print()
    print("  High-divergence probes (|div| >= 0.10):")
    hi_div = [r for r in by_div if abs(float(r.get("divergence") or 0)) >= 0.10]
    if hi_div:
        for r in hi_div:
            div   = float(r.get("divergence") or 0)
            est   = float(r.get("claude_estimate") or 0)
            price = float(r.get("market_price_at_probe") or 0)
            result = r.get("result", "?")
            ticker = (r.get("ticker") or "")[:35]
            print(f"    {ticker:<35}  div={div:+.3f}  est={_pct(est)}  mkt={_pct(price)}  -> {result}")
    else:
        print("    None resolved yet.")

    wins_hi = [r for r in hi_div if r.get("result") == "WIN"]
    if hi_div:
        print(f"\n  High-divergence hit rate: {len(wins_hi)}/{len(hi_div)} = {len(wins_hi)/len(hi_div)*100:.0f}%")
    print()
    avg_div = sum(abs(float(r.get("divergence") or 0)) for r in by_div) / len(by_div) if by_div else 0
    print(f"  Avg |divergence| on resolved probes: {avg_div:.3f}")


def main(resolve: bool = True):
    print()
    print(_rule())
    print("LEVIATHAN — TRACK RECORD & P&L SUMMARY")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"{ts}")
    print(_rule())

    config = {}
    cfg_path = os.path.join(ROOT, "config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            config = json.load(f)

    # Resolve outcomes via Kalshi API
    if resolve:
        print("\n[1] Resolving outcomes via Kalshi API...")
        try:
            from core import kalshi as _kalshi
            _kalshi.authenticate(config)
            resolved = logger.resolve_outcomes(config)
            print(f"    {resolved} row(s) newly resolved")
        except Exception as e:
            print(f"    WARNING: resolve_outcomes failed ({e})")
            print("    Proceeding with existing data...")
    else:
        print("\n[1] Skipping resolve (--no-resolve)")

    # Pull all rows from DB
    try:
        with logger._db() as conn:
            all_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM signals ORDER BY timestamp DESC"
            ).fetchall()]
    except Exception as e:
        print(f"FATAL: Cannot read DB: {e}")
        return

    paper_rows  = [r for r in all_rows if (r.get("source") or "paper") in ("paper", None)]
    probe_rows  = [r for r in all_rows if r.get("source") == "research_probe"]
    fill_rows   = [r for r in all_rows if r.get("source") == "real_fill"]

    # ── Paper signals ─────────────────────────────────────────────────────────
    print()
    print(_rule("="))
    print("PAPER SIGNALS (simulated — main.py runs)")
    print(_rule("-"))
    _print_section("paper signals", paper_rows)

    # ── Research probes ───────────────────────────────────────────────────────
    print()
    print(_rule("="))
    print("RESEARCH PROBES (analysis/research_probe.py runs)")
    print(_rule("-"))
    _print_section("probe", probe_rows)
    _probe_breakdown(probe_rows)

    # ── Real fills ────────────────────────────────────────────────────────────
    print()
    print(_rule("="))
    print("REAL FILLS (actual Kalshi positions)")
    print(_rule("-"))
    _print_section("real fills", fill_rows)

    # ── Signal type breakdown ─────────────────────────────────────────────────
    flag_stats  = logger.get_stats_by_flag_path()
    sig_stats   = logger.get_stats_by_sig()
    conf_stats  = logger.get_stats_by_confidence()

    # ── Confidence breakdown ───────────────────────────────────────────────────
    any_conf = any(conf_stats[lvl]["total"] > 0 for lvl in ("HIGH", "MED", "LOW"))
    if any_conf:
        print()
        print(_rule("="))
        print("CONFIDENCE BREAKDOWN  (paper signals, resolved only)")
        print(_rule("-"))
        print()
        print(f"  {'Level':<6}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L ($10)':>10}")
        print(f"  {'-'*6}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*10}")
        for lvl in ("HIGH", "MED", "LOW"):
            d = conf_stats[lvl]
            if not d["total"]:
                continue
            wr_s  = f"{d['win_rate']:.0f}%" if d["win_rate"] is not None else "—"
            pnl_s = _fmt_pnl(d["total_pnl"]) if d["total_pnl"] is not None else "—"
            print(f"  {lvl:<6}  {d['total']:>5}  {d['wins']:>4}  {wr_s:>6}  {pnl_s:>10}")

    if flag_stats or sig_stats:
        print()
        print(_rule("="))
        print("SIGNAL TYPE ANALYSIS  (paper signals, resolved only)")
        print(_rule("-"))

        if flag_stats:
            resolved_fp = [r for r in flag_stats if r.get("total", 0) > 0]
            if resolved_fp:
                print()
                print("  By flag path:")
                print(f"  {'Path':<16}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L ($10)':>10}")
                print(f"  {'-'*16}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*10}")
                for r in resolved_fp:
                    wr_s  = f"{r['win_rate']:.0f}%" if r["win_rate"] is not None else "—"
                    pnl_s = _fmt_pnl(r["total_pnl"]) if r["total_pnl"] is not None else "—"
                    print(f"  {r['flag_path']:<16}  {r['total']:>5}  {r['wins']:>4}  {wr_s:>6}  {pnl_s:>10}")

        if sig_stats:
            any_sig = any(v.get("total", 0) > 0 for v in sig_stats.values())
            if any_sig:
                print()
                print("  By signal presence (markets can have multiple):")
                print(f"  {'Signal':<16}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L ($10)':>10}")
                print(f"  {'-'*16}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*10}")
                labels = {
                    "sig_edge":    "EDGE",
                    "sig_drift":   "DRIFT",
                    "sig_br_none": "BR_NONE",
                }
                for key, label in labels.items():
                    r = sig_stats.get(key, {})
                    if not r.get("total"):
                        continue
                    wr_s  = f"{r['win_rate']:.0f}%" if r["win_rate"] is not None else "—"
                    pnl_s = _fmt_pnl(r["total_pnl"]) if r["total_pnl"] is not None else "—"
                    print(f"  {label:<16}  {r['total']:>5}  {r['wins']:>4}  {wr_s:>6}  {pnl_s:>10}")

    # ── Time-horizon breakdown ────────────────────────────────────────────────
    horizon_stats = logger.get_stats_by_time_horizon()
    any_horizon = any(horizon_stats[b]["total"] > 0 for b in ("INTRADAY", "WEEKLY", "MONTHLY", "QUARTERLY", "LONG"))
    if any_horizon:
        print()
        print(_rule("="))
        print("TIME HORIZON BREAKDOWN  (paper signals, resolved only)")
        print(_rule("-"))
        print()
        print(f"  {'Horizon':<12}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L ($10)':>10}  {'Avg Edge':>8}")
        print(f"  {'-'*12}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*10}  {'-'*8}")
        for bucket in ("INTRADAY", "WEEKLY", "MONTHLY", "QUARTERLY", "LONG"):
            d = horizon_stats[bucket]
            if not d["total"]:
                continue
            wr_s   = f"{d['win_rate']:.0f}%"   if d["win_rate"]  is not None else "—"
            pnl_s  = _fmt_pnl(d["total_pnl"])  if d["total_pnl"] is not None else "—"
            edge_s = f"{d['avg_edge']*100:.1f}pp" if d["avg_edge"] is not None else "—"
            print(f"  {bucket:<12}  {d['total']:>5}  {d['wins']:>4}  {wr_s:>6}  {pnl_s:>10}  {edge_s:>8}")

    # ── Heuristic alignment breakdown ────────────────────────────────────────
    align_stats = logger.get_stats_by_heuristic_alignment()
    any_align = any(align_stats[k]["total"] > 0 for k in ("aligned", "override"))
    if any_align:
        print()
        print(_rule("="))
        print("HEURISTIC ALIGNMENT  (paper signals, resolved only)")
        print(_rule("-"))
        print()
        print("  Does Claude's direction agree with the heuristic base-rate lean?")
        print()
        print(f"  {'Group':<16}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L ($10)':>10}  {'Avg Edge':>8}")
        print(f"  {'-'*16}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*10}  {'-'*8}")
        labels = {
            "aligned":      "Aligned",
            "override":     "Override",
            "no_heuristic": "No heuristic",
        }
        for key, label in labels.items():
            d = align_stats[key]
            if not d["total"]:
                continue
            wr_s   = f"{d['win_rate']:.0f}%"   if d["win_rate"]  is not None else "—"
            pnl_s  = _fmt_pnl(d["total_pnl"])  if d["total_pnl"] is not None else "—"
            edge_s = f"{d['avg_edge']*100:.1f}pp" if d["avg_edge"] is not None else "—"
            print(f"  {label:<16}  {d['total']:>5}  {d['wins']:>4}  {wr_s:>6}  {pnl_s:>10}  {edge_s:>8}")
        if align_stats["override"]["total"] > 0 and align_stats["aligned"]["total"] > 0:
            ov_wr = align_stats["override"]["win_rate"] or 0
            al_wr = align_stats["aligned"]["win_rate"]  or 0
            delta = ov_wr - al_wr
            verdict = "overrides outperform" if delta > 5 else (
                "overrides underperform" if delta < -5 else "no meaningful difference")
            print(f"\n  Override vs Aligned: {delta:+.0f}pp  → {verdict}")

    # ── Net-of-spread edge breakdown ──────────────────────────────────────────
    ne_stats = logger.get_stats_by_net_edge()
    ne_any   = any(ne_stats[b]["total"] for b in ne_stats)
    if ne_any:
        print()
        print(_rule("="))
        print("NET-OF-SPREAD EDGE BREAKDOWN  (paper signals, resolved only)")
        print(_rule("-"))
        print()
        print("  Does realizable edge (after bid-ask spread) predict win rate?")
        print()
        bucket_labels = {
            "spread_dominant": "spread > edge",
            "thin":            "0-5pp net edge",
            "good":            "5-10pp net edge",
            "strong":          ">10pp net edge",
            "no_data":         "no spread data",
        }
        print(f"  {'Bucket':<18}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L ($10)':>10}  {'Avg Edge':>8}")
        print(f"  {'-'*18}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*10}  {'-'*8}")
        for b in ("spread_dominant", "thin", "good", "strong", "no_data"):
            d = ne_stats[b]
            if not d["total"]:
                continue
            label  = bucket_labels[b]
            wr_s   = f"{d['win_rate']:.0f}%"      if d["win_rate"]  is not None else "--"
            pnl_s  = _fmt_pnl(d["total_pnl"])     if d["total_pnl"] is not None else "--"
            edge_s = f"{d['avg_edge']*100:.1f}pp"  if d["avg_edge"]  is not None else "--"
            print(f"  {label:<18}  {d['total']:>5}  {d['wins']:>4}  {wr_s:>6}  {pnl_s:>10}  {edge_s:>8}")
        thin_plus = sum(ne_stats[b]["total"] for b in ("thin", "good", "strong"))
        thin_wins = sum(ne_stats[b]["wins"]  for b in ("thin", "good", "strong"))
        sd = ne_stats["spread_dominant"]
        if thin_plus and sd["total"] and sd["win_rate"] is not None:
            tp_wr   = thin_wins / thin_plus * 100
            delta   = tp_wr - sd["win_rate"]
            verdict = "tradeable edge outperforms" if delta > 5 else (
                      "no meaningful difference" if abs(delta) <= 5 else
                      "spread-dominant outperforms (unexpected)")
            print(f"\n  Tradeable (net>0) vs Spread-dominant: {tp_wr:.0f}% vs {sd['win_rate']:.0f}%  --> {verdict}")

    # ── Combined summary ──────────────────────────────────────────────────────
    all_resolved = [r for r in all_rows if r.get("outcome")]
    all_pnl      = sum(float(r["pnl_if_traded"] or 0) for r in all_resolved
                       if r.get("pnl_if_traded") is not None)
    all_wins     = sum(1 for r in all_resolved if r.get("result") == "WIN")
    all_wr       = (all_wins / len(all_resolved) * 100) if all_resolved else None

    # Brier score — calibration metric
    brier = logger.get_brier_score()

    print()
    print(_rule("="))
    print("COMBINED SUMMARY")
    print(_rule("-"))
    print(f"  Total rows:   {len(all_rows)}")
    print(f"  Resolved:     {len(all_resolved)}")
    print(f"  Win rate:     {f'{all_wr:.1f}%' if all_wr is not None else '— (none resolved)'}")
    print(f"  Net PnL:      {_fmt_pnl(all_pnl)} (at $10/contract)")
    bs = brier.get("brier_score")
    bs_n = brier.get("n", 0)
    bs_label = brier.get("label", "")
    if bs is not None:
        print(f"  Brier Score:  {bs:.4f}  ({bs_label}, n={bs_n})")
        print(f"                [0=perfect, 0.25=random, >0.25=poor]")
    else:
        print(f"  Brier Score:  PENDING — no resolved paper signals yet")
    print()
    print(_rule())
    print()


if __name__ == "__main__":
    resolve = "--no-resolve" not in sys.argv
    main(resolve=resolve)
