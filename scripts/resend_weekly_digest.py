"""
One-off resend of the weekly digest that failed on 2026-09-13 due to a
transient SMTP SSL error. Mirrors main.py's Sunday-digest block exactly
(same logger/report calls) without rerunning the full 8-step pipeline.
"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import logger, report

CONFIG_PATH = ROOT / "config.json"

with open(CONFIG_PATH, encoding="utf-8") as f:
    config = json.load(f)

now_local = datetime.now(timezone.utc)

week_sigs = logger.get_week_signals(days=7)
if not week_sigs:
    print("No signals in the last 7 days — nothing to send.")
    raise SystemExit(0)

_weekly_stats    = logger.get_stats()
_weekly_flag_cal = logger.get_stats_by_flag_path()
_weekly_heur_cal = logger.get_stats_by_heuristic_label()
_weekly_whale    = logger.get_stats_by_whale()
_weekly_brier    = logger.get_brier_score()
_weekly_lv       = logger.get_stats_by_leviathan_score()

weekly_body = report.compile_weekly_digest(
    week_sigs, _weekly_stats, config,
    flag_path_stats=_weekly_flag_cal,
    brier=_weekly_brier,
    lv_stats=_weekly_lv,
    heuristic_label_stats=_weekly_heur_cal,
    whale_stats=_weekly_whale,
)

weekly_html = None
try:
    weekly_html = report.render_weekly_html(
        week_sigs, _weekly_stats, config,
        flag_path_stats=_weekly_flag_cal,
        brier=_weekly_brier,
        lv_stats=_weekly_lv,
        heuristic_label_stats=_weekly_heur_cal,
        whale_stats=_weekly_whale,
    )
except Exception as e:
    print(f"[warn] Weekly HTML render failed, sending text-only: {e}")

report.send_report(
    weekly_body, [], 0, config,
    subject_override=f"Leviathan Weekly — {now_local.strftime('%b %d, %Y')} (resend)",
    html_body=weekly_html,
)
print("Weekly digest resent.")
