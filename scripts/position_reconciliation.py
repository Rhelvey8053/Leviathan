"""
scripts/position_reconciliation.py - Daily position reconciliation.

Compares open paper signals in leviathan.db against actual Kalshi positions
to confirm every called signal has (or doesn't have) a matching real trade.

Output:
  - Console report
  - data/reconciliation/YYYY-MM-DD.json  (historical record)
  - --email flag: compact block for weekly digest

Usage:
  python scripts/position_reconciliation.py
  python scripts/position_reconciliation.py --email
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _open_paper_signals(db_path: str | Path) -> dict[str, str]:
    """
    Returns {ticker: direction} for open paper signals (no resolved result).
    When a ticker appears multiple times, keeps the most recent direction.
    Only YES/NO directions are included (PASS rows excluded).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT ticker, direction, MAX(timestamp) AS ts
            FROM signals
            WHERE (source = 'paper' OR source IS NULL)
              AND (result IS NULL OR result = '')
              AND direction IN ('YES', 'NO')
            GROUP BY ticker
        """).fetchall()
    finally:
        conn.close()
    return {r["ticker"]: r["direction"] for r in rows if r["ticker"]}


# ── Position parsing ──────────────────────────────────────────────────────────

def _parse_positions(raw_positions: list[dict]) -> dict[str, str]:
    """
    Parse Kalshi /portfolio/positions response into {ticker: direction}.

    Kalshi returns `position` as net YES contracts held:
      position > 0  →  long YES
      position < 0  →  long NO  (equivalent to short YES)
      position == 0 →  flat; skip

    Falls back to `yes_position` / `no_position` if `position` is absent.
    """
    result: dict[str, str] = {}
    for p in raw_positions:
        ticker = (p.get("ticker") or p.get("market_ticker") or "").strip()
        if not ticker:
            continue

        pos = p.get("position")
        if pos is not None:
            try:
                pos = int(pos)
            except (TypeError, ValueError):
                pos = None

        if pos is None:
            # Fallback: explicit yes_position / no_position counts
            yes_ct = int(p.get("yes_position", 0) or 0)
            no_ct  = int(p.get("no_position",  0) or 0)
            net    = yes_ct - no_ct
            if net > 0:
                result[ticker] = "YES"
            elif net < 0:
                result[ticker] = "NO"
        else:
            if pos > 0:
                result[ticker] = "YES"
            elif pos < 0:
                result[ticker] = "NO"
            # pos == 0 → flat, omit

    return result


# ── Core logic ────────────────────────────────────────────────────────────────

def reconcile_data(paper: dict[str, str], positions: dict[str, str]) -> dict:
    """
    Pure reconciliation: classifies tickers into four buckets.

    aligned    - signal + position, same direction (expected normal state)
    misaligned - signal + position, opposing direction (rare; investigate)
    unplaced   - signal exists, no Kalshi position (bet not placed)
    unexpected - Kalshi position, no paper signal (manual / unlogged trade)
    """
    all_tickers = sorted(set(paper) | set(positions))

    aligned    = []
    misaligned = []
    unplaced   = []
    unexpected = []

    for ticker in all_tickers:
        sig_dir = paper.get(ticker)
        pos_dir = positions.get(ticker)

        if sig_dir and pos_dir:
            if sig_dir == pos_dir:
                aligned.append({"ticker": ticker, "direction": sig_dir})
            else:
                misaligned.append({
                    "ticker":    ticker,
                    "signal":    sig_dir,
                    "position":  pos_dir,
                })
        elif sig_dir:
            unplaced.append({"ticker": ticker, "direction": sig_dir})
        else:
            unexpected.append({"ticker": ticker, "direction": pos_dir})

    return {
        "aligned":    aligned,
        "misaligned": misaligned,
        "unplaced":   unplaced,
        "unexpected": unexpected,
    }


def reconcile(
    config: dict,
    db_path: str | Path,
    *,
    _fetch_fn=None,
) -> dict:
    """
    Full reconciliation: reads DB + calls Kalshi API, returns result dict.
    _fetch_fn: override for testing (replaces kalshi.fetch_positions).
    """
    paper = _open_paper_signals(db_path)

    if _fetch_fn is not None:
        fetch = _fetch_fn
    else:
        from core import kalshi as _kalshi
        fetch = _kalshi.fetch_positions

    try:
        raw       = fetch(config)
        positions = _parse_positions(raw)
    except Exception as exc:
        return {
            "run_at":            datetime.now(timezone.utc).isoformat(),
            "paper_open":        len(paper),
            "positions_fetched": 0,
            "error":             str(exc),
            "aligned":           [],
            "misaligned":        [],
            "unplaced":          [],
            "unexpected":        [],
        }

    buckets = reconcile_data(paper, positions)
    return {
        "run_at":            datetime.now(timezone.utc).isoformat(),
        "paper_open":        len(paper),
        "positions_fetched": len(positions),
        **buckets,
    }


# ── Formatting ────────────────────────────────────────────────────────────────

def format_report(result: dict, *, compact: bool = False) -> str:
    rule  = "=" * 60
    lines = [rule, "POSITION RECONCILIATION"]

    ts = result.get("run_at", "")[:19].replace("T", " ")
    lines.append(f"  Run at:              {ts} UTC")
    lines.append(f"  Open paper signals:  {result.get('paper_open', 0)}")
    lines.append(f"  Kalshi positions:    {result.get('positions_fetched', 0)}")

    if "error" in result:
        lines.append(f"  ERROR: {result['error']}")
        lines.append(rule)
        return "\n".join(lines)

    aligned    = result.get("aligned",    [])
    misaligned = result.get("misaligned", [])
    unplaced   = result.get("unplaced",   [])
    unexpected = result.get("unexpected", [])

    lines.append("")

    # Misaligned first — highest priority
    if misaligned:
        lines.append(f"  [!] MISALIGNED ({len(misaligned)}) — signal and position point opposite directions:")
        for r in misaligned:
            t = r["ticker"][:38]
            lines.append(f"      !! {t:<38}  signal={r['signal']}  pos={r['position']}")

    if aligned:
        lines.append(f"  Aligned ({len(aligned)}):")
        for r in aligned:
            lines.append(f"      OK {r['ticker'][:38]:<38}  {r['direction']}")

    if unplaced:
        lines.append(f"  Unplaced ({len(unplaced)}) — signal called, no open Kalshi position:")
        for r in unplaced:
            lines.append(f"      -- {r['ticker'][:38]:<38}  {r['direction']}")

    if unexpected:
        lines.append(f"  Unexpected ({len(unexpected)}) — Kalshi position held with no paper signal:")
        for r in unexpected:
            lines.append(f"      ?? {r['ticker'][:38]:<38}  {r['direction']}")

    if not any([aligned, misaligned, unplaced, unexpected]):
        lines.append("  No open signals and no open positions.")

    lines.append(rule)
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Reconcile paper signals vs Kalshi positions")
    parser.add_argument("--email", action="store_true", help="Compact email-block output only")
    parser.add_argument("--db",    default=str(ROOT / "data" / "leviathan.db"), help="DB path")
    args = parser.parse_args()

    with open(ROOT / "config.json") as f:
        config = json.load(f)

    result = reconcile(config, args.db)

    print(format_report(result, compact=args.email))

    # Save historical record
    out_dir = ROOT / "data" / "reconciliation"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = out_dir / f"{date_str}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"\n[reconciliation] saved → {out_path}")

    # Exit non-zero if any misalignment detected
    if result.get("misaligned"):
        sys.exit(1)


if __name__ == "__main__":
    main()
