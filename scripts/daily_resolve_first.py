"""
Daily resolve-first runner.

Runs analysis/resolve_first.py's near-dated market selector so signals
close and resolve faster (accelerates n toward the n=20 calibration
gate). Reads whatever snapshot main.py / daily_smart_money.py already
refreshed earlier in the day — no live Kalshi fetch in the normal case.

Writes only to the local, git-ignored leviathan.db (and its own
git-ignored snapshot/backup files) — nothing here needs a git commit.

Scheduled via Windows Task Scheduler — see scripts/setup_resolve_first_scheduler.ps1.
Run it after the daily main pipeline and smart-money scan so the
snapshot is fresh.
"""

import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from analysis.resolve_first import main as resolve_first_main


def main():
    print(f"\n[daily_resolve_first] {datetime.now(timezone.utc).isoformat()}")

    cfg_path = os.path.join(ROOT, "config.json")
    with open(cfg_path, encoding="utf-8") as f:
        config = json.load(f)

    resolve_first_main(config)


if __name__ == "__main__":
    main()
