"""
Daily smart money scan runner.
Runs the watchlist scan, saves a dated markdown report, and commits+pushes.

Scheduled via Windows Task Scheduler — see scripts/setup_scheduler.ps1.
"""

import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis.smart_money_scan import load_config, run_smart_money_scan, save_report, save_signals_cache
from analysis.snapshot_markets import fetch_snapshot, save_snapshot


def git(args: list[str]) -> tuple[int, str]:
    r = subprocess.run(["git"] + args, cwd=ROOT, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def main():
    print(f"\n[daily_smart_money] {datetime.now(timezone.utc).isoformat()}")

    cfg = load_config()

    # Refresh Kalshi snapshot so cross-reference uses fresh market titles
    try:
        import kalshi as _kalshi
        _kalshi.authenticate(cfg)
        markets, event_count = fetch_snapshot(cfg)
        snap_path = save_snapshot(markets, event_count, cfg)
        print(f"  Snapshot refreshed: {len(markets)} markets -> {snap_path}")
    except Exception as e:
        print(f"  [warn] Snapshot refresh failed: {e} — using cached snapshot")

    result = run_smart_money_scan(cfg, force_refresh=True)
    path   = save_report(result)
    save_signals_cache(result)
    print(f"Report saved: {path}")

    rel_path = os.path.relpath(path, ROOT).replace("\\", "/")

    signals  = result.get("kalshi_signals", [])
    n_pos    = result.get("positions_total", 0)
    n_sig    = len(signals)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    code, out = git(["add", rel_path])
    if code != 0:
        print(f"  git add failed: {out}")
        sys.exit(1)

    # Check if there's anything to commit
    code, staged = git(["diff", "--cached", "--name-only"])
    if not staged.strip():
        print("  No changes to commit (report unchanged).")
        return

    msg = (
        f"data: smart money scan {date_str} "
        f"({n_pos} positions, {n_sig} Kalshi signals)"
    )
    code, out = git(["commit", "-m", msg])
    if code != 0:
        print(f"  git commit failed: {out}")
        sys.exit(1)
    print(f"  Committed: {msg}")

    code, out = git(["push"])
    if code != 0:
        print(f"  git push failed: {out}")
        sys.exit(1)
    print("  Pushed to origin.")


if __name__ == "__main__":
    main()
