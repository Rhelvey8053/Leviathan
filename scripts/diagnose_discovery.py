"""
scripts/diagnose_discovery.py — Runs the smart-money wallet discovery
funnel diagnostic once against the live Polymarket API and prints the
survivor-count table and per-gate metric distributions.

Instrumentation only: promotes no wallet, writes nothing to
winning_accounts.json, and changes no threshold, sample size, or gate
in sources/accounts.py (see diagnose_discovery / _classify_wallet).

Usage:
    python scripts/diagnose_discovery.py [--sample-size N]

--sample-size overrides config.json's accounts.discovery_sample_size
for this run only (useful if the default is too slow/rate-limited for
a first pass — the funnel SHAPE is what matters, not hitting a fixed N).
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sources.accounts import diagnose_discovery, format_diagnostic_report


def load_config() -> dict:
    cfg_path = os.path.join(ROOT, "config.json")
    if not os.path.exists(cfg_path):
        cfg_path = os.path.join(ROOT, "config.example.json")
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="Smart-money discovery funnel diagnostic")
    parser.add_argument("--sample-size", type=int, default=None,
                         help="Override accounts.discovery_sample_size for this run")
    args = parser.parse_args()

    config = load_config()
    if args.sample_size is not None:
        config.setdefault("accounts", {})["discovery_sample_size"] = args.sample_size

    result = diagnose_discovery(config)
    print(format_diagnostic_report(result))


if __name__ == "__main__":
    main()
