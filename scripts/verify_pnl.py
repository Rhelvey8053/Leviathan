"""
scripts/verify_pnl.py — Read-only PnL integrity check (Goal 5b Part A).

Selects all resolved signals (outcome IS NOT NULL), recomputes pnl_if_traded
from stored fields using the current logger.py formula, and prints a diff table.

If any delta != 0: prints before/after, backs up leviathan.db to
leviathan.db.bak_pnlfix, and applies a one-time UPDATE inside a transaction.
If all deltas == 0: prints confirmation and exits without writing anything.

Usage:
    python scripts/verify_pnl.py [--apply] [--no-alert]

    --apply     Actually write the backfill if deltas found (default: dry-run).
                Without --apply, a non-zero delta set exits with code 1 so you
                can inspect before committing.
    --no-alert  Never send the alert email even if diffs are found -- for a
                manual/interactive run where you're already watching the
                output and don't want a duplicate notification.

Scheduled weekly via Windows Task Scheduler -- see
scripts/setup_verify_pnl_scheduler.ps1. Runs WITHOUT --apply (an unattended
job must never silently rewrite the DB; a human reviews and re-runs with
--apply by hand). If drift is found, sends one alert email (same
core.report.send_report path automation_health_check.py uses), deduped by
day via a state file so a still-unresolved problem doesn't re-alert on
every run -- a genuinely new day (or a newly-appearing diff) still sends.
Not previously on any schedule: this check only ever ran when someone
happened to invoke it by hand (2026-09-08 finding).
"""

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT    = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.report import send_report

DB_PATH      = ROOT / "data" / "leviathan.db"
BAK_PATH     = ROOT / "data" / "leviathan.db.bak_pnlfix"
DEFAULT_STATE = ROOT / "data" / "verify_pnl_alert_state.json"


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_config() -> dict:
    cfg_path = ROOT / "config.json"
    if not cfg_path.exists():
        cfg_path = ROOT / "config.example.json"
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def compose_alert(diffs: list[dict]) -> tuple[str, str]:
    lines = [f"{len(diffs)} resolved signal(s) with a pnl_if_traded mismatch:", ""]
    for d in diffs:
        lines.append(
            f"  {d['call_id']}  {d['ticker']}  {d['direction']}/{d['outcome']}  "
            f"stored={d['stored_pnl']}  recomputed={d['recomputed']}  delta={d['delta']:+.6f}"
        )
    lines.append("")
    lines.append("Run `python scripts/verify_pnl.py --apply` to backfill after reviewing.")
    body = "\n".join(lines)
    subject = f"Leviathan ALERT — PnL integrity: {len(diffs)} row(s) mismatched"
    return body, subject


def send_alert_if_needed(diffs: list[dict], config: dict, state_path: Path = DEFAULT_STATE) -> bool:
    """Returns True if an email was actually sent."""
    if not diffs:
        return False

    today = datetime.now(timezone.utc).date().isoformat()
    prior_alerts = load_state(state_path).get("alerted", {})
    signature = ",".join(sorted(d["call_id"] for d in diffs))
    if prior_alerts.get(signature) == today:
        return False

    body, subject = compose_alert(diffs)
    try:
        send_report(body, signals=[], whale_flags=0, config=config, subject_override=subject)
    except Exception as e:
        print(f"[verify_pnl] alert send FAILED — state NOT persisted, will retry next check: {e}")
        return False

    save_state(state_path, {"alerted": {signature: today}})
    print(f"[verify_pnl] ALERT sent: {len(diffs)} mismatched row(s)")
    return True


def _recompute_pnl(direction: str, price: float, entry_price, fill_count, fill_fee, outcome: str) -> float:
    """Recompute pnl_if_traded using the current logger.resolve_outcomes formula."""
    win = (outcome == direction)  # e.g. direction="YES", outcome="YES" → win
    if entry_price is not None:
        p            = float(entry_price)
        fill_count   = float(fill_count or 1)
        fee_per_unit = float(fill_fee or 0) / fill_count
    else:
        p            = float(price or 0)
        fee_per_unit = 0.0

    if direction == "YES":
        pnl = round(((1.0 - p) if win else -p) - fee_per_unit, 4)
    elif direction == "NO":
        pnl = round((p if win else -(1.0 - p)) - fee_per_unit, 4)
    else:
        pnl = 0.0
    return pnl


def run(apply: bool = False, alert: bool = True) -> int:
    if not DB_PATH.exists():
        print(f"ERROR: database not found at {DB_PATH}")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT call_id, ticker, direction, market_price, entry_price,
               fill_count, fill_fee, outcome, result, pnl_if_traded
        FROM signals
        WHERE outcome IS NOT NULL AND outcome != ''
    """).fetchall()

    print(f"Checking {len(rows)} resolved signal(s)...")

    diffs = []
    for row in rows:
        stored = row["pnl_if_traded"]
        if stored is None:
            stored = None
        recomputed = _recompute_pnl(
            direction   = row["direction"],
            price       = row["market_price"],
            entry_price = row["entry_price"],
            fill_count  = row["fill_count"],
            fill_fee    = row["fill_fee"],
            outcome     = row["outcome"],
        )
        delta = round((recomputed - (stored or 0)), 6)
        if abs(delta) > 1e-9:
            diffs.append({
                "call_id":      row["call_id"],
                "ticker":       row["ticker"],
                "direction":    row["direction"],
                "outcome":      row["outcome"],
                "stored_pnl":   stored,
                "recomputed":   recomputed,
                "delta":        delta,
            })

    if not diffs:
        print("OK: All pnl_if_traded values are correct -- no backfill needed.")
        conn.close()
        return 0

    # Print diff table
    print(f"\n{'call_id':<12}  {'ticker':<44}  {'dir':<4}  {'out':<4}  {'stored':>8}  {'recomputed':>10}  {'delta':>8}")
    print("-" * 100)
    for d in diffs:
        print(
            f"{d['call_id']:<12}  {d['ticker']:<44}  {d['direction']:<4}  {d['outcome']:<4}  "
            f"{str(d['stored_pnl']):>8}  {d['recomputed']:>10.4f}  {d['delta']:>+8.6f}"
        )
    print(f"\n{len(diffs)} row(s) with non-zero delta.")

    if alert:
        send_alert_if_needed(diffs, load_config())

    if not apply:
        print("\nDry-run mode. Run with --apply to write the backfill.")
        conn.close()
        return 1

    # Backup DB before writing. litestream-setup-2026-08: leviathan.db is now
    # in WAL mode -- checkpoint first so the copy is complete on its own,
    # not missing recent transactions still sitting in leviathan.db-wal.
    print(f"\nBacking up {DB_PATH.name} → {BAK_PATH.name}...")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(str(DB_PATH), str(BAK_PATH))
    print(f"Backup written: {BAK_PATH}")

    # Apply backfill in a single transaction
    print("Applying backfill...")
    with conn:
        for d in diffs:
            conn.execute(
                "UPDATE signals SET pnl_if_traded = ? WHERE call_id = ?",
                (d["recomputed"], d["call_id"]),
            )
    print(f"OK: Backfill applied -- {len(diffs)} row(s) updated.")
    conn.close()
    return 0


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    alert = "--no-alert" not in sys.argv
    sys.exit(run(apply=apply, alert=alert))
