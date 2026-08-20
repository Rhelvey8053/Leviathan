"""
scripts/send_editorial_selftest.py — Phase 3 dogfood send
(leviathan-report-format-decision.md).

Renders the editorial subscriber design AND the editorial weekly (same
renderers the two preview harnesses use, so this is never a second copy of
either layout) and emails each to the owner (config.report.email_to) as
the text/html part of its own real MIMEMultipart("alternative") message,
reusing the existing send_report SMTP path. Standalone script, not hooked
into main.py or any scheduled task -- the live production daily/weekly
sends (render_html/compile_report, render_weekly_html/compile_weekly_digest)
are completely untouched by this.

subscribers.json is empty as of 2026-08-19 (owner-only), so send_report's
normal recipient-list logic naturally sends to just the owner already --
this script doesn't special-case that.

Usage:
    python scripts\\send_editorial_selftest.py --dry-run              # render + print, no SMTP
    python scripts\\send_editorial_selftest.py --live                 # send both daily and weekly
    python scripts\\send_editorial_selftest.py --live --only daily    # send just one
    python scripts\\send_editorial_selftest.py --live --only weekly
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.report import send_report
from scripts.render_subscriber_preview import render as render_editorial_daily
from scripts.render_weekly_subscriber_preview import render as render_editorial_weekly

CONFIG_PATH = os.path.join(ROOT, "config.json")


def _load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _send_one(kind: str, html_body: str, config: dict, now_utc: datetime, dry_run: bool) -> None:
    text_body = (
        f"Leviathan -- Editorial design self-test ({kind}).\n\n"
        "This is a dogfood send of the editorial design "
        "(leviathan-report-format-decision.md Phase 3). If you're reading "
        "this in plain text, your email client didn't render the HTML "
        "part -- that's exactly the kind of thing this send exists to "
        f"catch. Rendered {now_utc.strftime('%Y-%m-%d %H:%M UTC')}."
    )
    subject = f"Leviathan — Editorial Design Self-Test ({kind.title()}) — {now_utc.strftime('%Y-%m-%d %H:%M UTC')}"

    if dry_run:
        print(f"[{kind}] Subject: {subject}")
        print(f"[{kind}] Would send to: {config.get('report', {}).get('email_to', '(none configured)')}")
        print(f"[{kind}] HTML body length: {len(html_body)} chars")
        return

    send_report(text_body, [], 0, config, subject_override=subject, html_body=html_body)
    print(f"[{kind}] Sent to {config.get('report', {}).get('email_to', '(none configured)')}")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Render and print only, no SMTP call.")
    group.add_argument("--live", action="store_true", help="Actually send via SMTP.")
    parser.add_argument("--only", choices=["daily", "weekly"], default=None,
                         help="Send only one kind (default: both).")
    args = parser.parse_args()

    config = _load_config()
    now_utc = datetime.now(timezone.utc)
    kinds = [args.only] if args.only else ["daily", "weekly"]

    for kind in kinds:
        html_body = render_editorial_daily(now_utc=now_utc) if kind == "daily" else render_editorial_weekly(now_utc=now_utc)
        _send_one(kind, html_body, config, now_utc, dry_run=args.dry_run)

    if args.dry_run:
        print("--dry-run: no SMTP call made.")


if __name__ == "__main__":
    main()
