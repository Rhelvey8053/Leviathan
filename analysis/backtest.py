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

import logger

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
            import kalshi as _kalshi
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

    # ── Combined summary ──────────────────────────────────────────────────────
    all_resolved = [r for r in all_rows if r.get("outcome")]
    all_pnl      = sum(float(r["pnl_if_traded"] or 0) for r in all_resolved
                       if r.get("pnl_if_traded") is not None)
    all_wins     = sum(1 for r in all_resolved if r.get("result") == "WIN")
    all_wr       = (all_wins / len(all_resolved) * 100) if all_resolved else None

    print()
    print(_rule("="))
    print("COMBINED SUMMARY")
    print(_rule("-"))
    print(f"  Total rows:   {len(all_rows)}")
    print(f"  Resolved:     {len(all_resolved)}")
    print(f"  Win rate:     {f'{all_wr:.1f}%' if all_wr is not None else '— (none resolved)'}")
    print(f"  Net PnL:      {_fmt_pnl(all_pnl)} (at $10/contract)")
    print()
    print(_rule())
    print()


if __name__ == "__main__":
    resolve = "--no-resolve" not in sys.argv
    main(resolve=resolve)
