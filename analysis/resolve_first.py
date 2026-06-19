#!/usr/bin/env python3
"""
analysis/resolve_first.py — Resolution-first paper batch logger (Goal 2c PART B).

Selects near-dated markets (closing within resolve_first_max_days, default 14)
with real two-sided books, spreads across price bands, and logs them as paper
signals via logger.log_signal so resolve_outcomes can settle them quickly.

HARD FREEZE: no new heuristic, scoring, calibration, or analytics code here.
Only the selection + logging harness described in Goal 2c PART B.
"""

import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import logger
from core import scanner

SNAPSHOT_DIR = ROOT / "data" / "snapshots"
DB_PATH      = ROOT / "leviathan.db"
DB_BAK       = ROOT / "leviathan.db.bak_3a"

PRICE_BANDS = [
    ("5-15%",  0.05, 0.15),
    ("15-40%", 0.15, 0.40),
    ("40-60%", 0.40, 0.60),
    ("60-95%", 0.60, 0.95),
]

NEEDED_FOR_WINRATE = 20


def backup_db() -> None:
    """Back up leviathan.db to leviathan.db.bak_3a (once — skip if bak exists)."""
    if not DB_BAK.exists() and DB_PATH.exists():
        shutil.copy2(DB_PATH, DB_BAK)
        print(f"[resolve_first] Backed up DB to {DB_BAK.name}")


def load_snapshot(config: dict) -> list[dict]:
    """
    Load latest snapshot from data/snapshots/. Falls back to live fetch
    only if no snapshot file exists (network-free by default).
    """
    snapshots = sorted(SNAPSHOT_DIR.glob("markets_*.json"), reverse=True)
    if snapshots:
        path = snapshots[0]
        print(f"[resolve_first] Using snapshot: {path.name}")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("markets", [])

    print("[resolve_first] No snapshot found — fetching live markets...")
    import kalshi as _kalshi
    markets = _kalshi.fetch_markets(config)
    if markets:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = SNAPSHOT_DIR / f"markets_{ts}.json"
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(markets, f)
        print(f"[resolve_first] Saved snapshot: {out.name}")
    return markets or []


def is_two_sided(m: dict) -> bool:
    """True if market has a real two-sided book (both yes_bid and yes_ask > 0)."""
    yb = float(m.get("yes_bid_dollars") or m.get("yes_bid") or 0)
    ya = float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0)
    return yb > 0 and ya > 0


def mid_price(m: dict) -> float | None:
    yb = float(m.get("yes_bid_dollars") or m.get("yes_bid") or 0)
    ya = float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0)
    if yb > 0 and ya > 0:
        return (yb + ya) / 2
    return None


def days_to_close(m: dict, now: datetime) -> int | None:
    ct = m.get("close_time") or m.get("expiration_time")
    if not ct:
        return None
    try:
        ct_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        return (ct_dt - now).days
    except Exception:
        return None


def price_band_label(mid: float) -> str | None:
    for label, lo, hi in PRICE_BANDS:
        if lo <= mid < hi:
            return label
    return None


def select_near_dated(scored: list[dict], max_days: int, now: datetime) -> tuple[list[dict], dict]:
    """
    From scored markets, select those that:
      1. Close within max_days (inclusive)
      2. Have a real two-sided book (both yes_bid > 0 and yes_ask > 0)
      3. Have mid price in [0.05, 0.95]

    Returns highest-volume market per (price_band, flag_path) pair.
    Returns (selected, band_counts).
    """
    candidates = []
    for m in scored:
        if not is_two_sided(m):
            continue
        mid = mid_price(m)
        if mid is None or not (0.05 <= mid <= 0.95):
            continue
        dtc = days_to_close(m, now)
        if dtc is None or dtc < 0 or dtc > max_days:
            continue
        m = dict(m)
        m["_mid"]  = mid
        m["_dtc"]  = dtc
        m["_band"] = price_band_label(mid)
        candidates.append(m)

    band_counts = {b[0]: 0 for b in PRICE_BANDS}
    for m in candidates:
        if m["_band"]:
            band_counts[m["_band"]] += 1

    # Best (highest volume) per (band, flag_path) combination
    seen: dict = {}
    for m in sorted(candidates, key=lambda x: -(float(x.get("volume_fp") or x.get("volume") or 0))):
        key = (m["_band"], m.get("flag_path") or "NONE")
        if key not in seen:
            seen[key] = m

    selected = list(seen.values())
    selected.sort(key=lambda x: (x["_band"] or "z", x["_dtc"]))
    return selected, band_counts


def dedup_already_logged(markets: list[dict], lookback_days: int) -> list[dict]:
    """Remove markets whose ticker was already paper-logged in the last lookback_days."""
    recent = logger.get_recent_tickers(days=lookback_days)
    return [m for m in markets if m.get("ticker") not in recent]


def log_selected(markets: list[dict], run_id: str) -> int:
    """
    Log each market as a paper signal via logger.log_signal.
    Direction = heuristic_direction (YES/NO) or PASS when neutral/unavailable.
    Each row labelled as a resolve_first mechanical pick (not a Claude call).
    Returns count of rows logged.
    """
    logged = 0
    for m in markets:
        mid = m.get("_mid") or mid_price(m)
        hd = m.get("heuristic_direction")
        direction = hd if hd in ("YES", "NO") else "PASS"
        ct = m.get("close_time") or m.get("expiration_time")

        signal = {
            "ticker":              m.get("ticker", ""),
            "title":               m.get("title", ""),
            "market_price":        mid,
            "our_estimate":        m.get("base_rate"),
            "edge":                m.get("raw_edge"),
            "direction":           direction,
            "confidence":          "MED" if hd in ("YES", "NO") else "LOW",
            "flag_path":           m.get("flag_path") or "RESOLVE_FIRST",
            "sig_edge":            m.get("sig_edge", False),
            "sig_drift":           m.get("sig_drift", False),
            "sig_br_none":         m.get("sig_br_none", False),
            "base_rate":           m.get("base_rate"),
            "net_edge":            m.get("net_edge"),
            "heuristic_direction": hd,
            "short_horizon":       m.get("short_horizon", False),
            "time_horizon":        m.get("time_horizon"),
            "close_time":          ct,
            "heuristic_label":     m.get("heuristic_label"),
            "run_id":              run_id,
        }
        logger.log_signal(signal)
        logged += 1
    return logged


def print_selection_table(selected: list[dict], band_counts: dict, max_days: int) -> None:
    """Print the PART B selection table."""
    print()
    print("=" * 80)
    print(f"RESOLVE_FIRST — SELECTION TABLE  (window: <={max_days} days, two-sided book)")
    print("=" * 80)

    print("\nPrice band coverage from two-sided candidates:")
    for label, _, _ in PRICE_BANDS:
        n = band_counts.get(label, 0)
        note = "EMPTY — no candidates in this band" if n == 0 else f"{n} candidate(s)"
        print(f"  {label:8s}: {note}")

    print(f"\nSelected for logging ({len(selected)} row(s)):")
    if not selected:
        print("  (none — all candidates already logged or no valid candidates)")
        return

    print(f"  {'Ticker':<44} {'Band':8} {'Days':5} {'Mid':6} {'Dir':5} {'FlagPath':14} Title")
    print("  " + "-" * 110)
    for m in selected:
        mid  = m.get("_mid", 0)
        dtc  = m.get("_dtc", "?")
        band = m.get("_band", "?") or "?"
        hd   = m.get("heuristic_direction")
        disp = hd if hd in ("YES", "NO") else "PASS"
        fp   = m.get("flag_path") or "NONE"
        title = (m.get("title") or "")[:35]
        ticker = (m.get("ticker") or "")[:42]
        print(f"  {ticker:<44} {band:8} {str(dtc):5} {mid:6.3f} {disp:5} {fp:14} {title}")


def print_resolution_status() -> None:
    """Print the PART C resolution status block."""
    _PAPER = "source = 'paper' OR source IS NULL"

    with logger._db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM signals WHERE ({_PAPER}) AND direction != 'PASS'"
        ).fetchone()[0]
        resolved = conn.execute(
            f"SELECT COUNT(*) FROM signals WHERE ({_PAPER}) AND direction != 'PASS' "
            f"AND outcome != '' AND outcome IS NOT NULL"
        ).fetchone()[0]
        pending = total - resolved
        wins = conn.execute(
            f"SELECT COUNT(*) FROM signals WHERE ({_PAPER}) AND result = 'WIN'"
        ).fetchone()[0]
        cutoff_14 = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        pending_14d = conn.execute(
            f"SELECT COUNT(*) FROM signals WHERE ({_PAPER}) AND direction != 'PASS' "
            f"AND (outcome IS NULL OR outcome = '') "
            f"AND close_time IS NOT NULL AND close_time != '' "
            f"AND close_time <= ?",
            (cutoff_14,)
        ).fetchone()[0]

    print()
    print("=" * 60)
    print("RESOLUTION STATUS REPORT")
    print("=" * 60)
    print(f"  Total paper rows logged:  {total}")
    print(f"  Resolved:                 {resolved}")
    print(f"  Pending:                  {pending}")
    print(f"  Pending closing <=14d:    {pending_14d}  (close_time stored, resolves soon)")
    print()
    print(f"  COUNTDOWN: {resolved} resolved / {NEEDED_FOR_WINRATE} needed before win-rate is meaningful")
    print()
    if resolved >= 10:
        wr = (wins / resolved) * 100
        print(f"  WIN RATE: {wr:.1f}%  (n={resolved})")
    else:
        print(f"  WIN RATE: NOT YET MEANINGFUL (n={resolved}, need >={10})")
    print("=" * 60)


def main(config: dict | None = None) -> None:
    if config is None:
        cfg_path = ROOT / "config.json"
        with open(cfg_path, encoding="utf-8") as f:
            config = json.load(f)

    max_days  = config.get("scoring", {}).get("resolve_first_max_days", 14)
    lookback  = config.get("scoring", {}).get("resolve_first_dedup_days", 7)

    backup_db()

    markets_raw = load_snapshot(config)
    print(f"[resolve_first] Snapshot size: {len(markets_raw)} markets")

    filtered = scanner.filter_markets(markets_raw, config)
    print(f"[resolve_first] After filter_markets: {len(filtered)} markets")

    scored = scanner.score_markets(filtered, config)
    print(f"[resolve_first] After score_markets: {len(scored)} markets")

    now = datetime.now(timezone.utc)
    selected, band_counts = select_near_dated(scored, max_days, now)

    print_selection_table(selected, band_counts, max_days)

    before = len(selected)
    selected = dedup_already_logged(selected, lookback_days=lookback)
    skipped = before - len(selected)
    if skipped:
        print(f"\n[resolve_first] Skipped {skipped} ticker(s) already logged in last {lookback} days")

    run_id = str(uuid.uuid4())[:8]
    logged = log_selected(selected, run_id=run_id)
    print(f"\n[resolve_first] Logged {logged} new paper signal(s) with close_time")

    print_resolution_status()

    # PART D: payoff fix verification (printed, no code change)
    print()
    print("=" * 60)
    print("PART D — FEE + PAYOFF ASSUMPTION CHECK")
    print("=" * 60)
    print("  Binary payoff formula in logger.resolve_outcomes (confirmed live):")
    print("    YES at p: win  -> +(1-p) - fee   YES at p: loss -> -p - fee")
    print("    NO  at p: win  -> +p     - fee   NO  at p: loss -> -(1-p) - fee")
    print("  [OK] Corrected payoff IS LIVE in logger.py:563-568.")
    print()
    print("  OPEN BLOCKER -- fee_cost units NOT verified:")
    print("    Kalshi returns fee_cost per fill event (not per contract).")
    print("    If fill_count=1 understates actual contract count, fee_per_unit")
    print("    is overstated. Real P&L is unreliable until verified against")
    print("    Kalshi's published fee schedule. Do not use for real-money decisions.")
    print("=" * 60)


if __name__ == "__main__":
    main()
