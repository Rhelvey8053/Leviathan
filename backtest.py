"""
Backtest harness for Leviathan signals.

Replays historical signals against resolved market outcomes to measure
strategy performance across confidence tiers, signal paths, and time horizons.
Scaffold only — no network calls. Plug real resolution data in once available.

Usage:
  python backtest.py --signals data/powerbi_export/signals.csv \\
                     --resolutions sample_resolutions.csv \\
                     --output report.txt
"""

import argparse
import csv
import sys
from pathlib import Path


class BacktestRunner:
    def __init__(self):
        self.signals: list[dict] = []
        self.resolutions: list[dict] = []
        self.matches: list[dict] = []

    def load_signals(self, path: str) -> None:
        with open(path, newline="", encoding="utf-8") as f:
            self.signals = list(csv.DictReader(f))

    def load_resolutions(self, path: str) -> None:
        """Load resolutions CSV with columns: ticker, resolved_yes (bool), close_date."""
        with open(path, newline="", encoding="utf-8") as f:
            rows = []
            for r in csv.DictReader(f):
                rows.append({
                    "ticker":       r["ticker"].strip(),
                    "resolved_yes": r["resolved_yes"].strip().lower() in ("true", "1", "yes"),
                    "close_date":   r.get("close_date", "").strip(),
                })
            self.resolutions = rows

    def match_signals_to_resolutions(self) -> None:
        """Join signals to resolutions on ticker; compute hit flag."""
        res_map = {r["ticker"]: r for r in self.resolutions}
        matches = []
        for s in self.signals:
            ticker = s.get("ticker", "").strip()
            res = res_map.get(ticker)
            if res is None:
                continue
            direction = (s.get("direction") or "").strip().upper()
            resolved_yes = res["resolved_yes"]
            hit = (direction == "YES" and resolved_yes) or (direction == "NO" and not resolved_yes)
            try:
                edge = float(s.get("edge") or s.get("net_edge") or 0)
            except ValueError:
                edge = 0.0
            conf = (s.get("confidence") or "").strip().upper() or "LOW"
            flag = (s.get("flag_path") or "").strip() or "UNKNOWN"
            horiz = (s.get("time_horizon") or "").strip().upper() or "UNKNOWN"
            matches.append({
                "ticker":      ticker,
                "direction":   direction,
                "resolved_yes": resolved_yes,
                "hit":         hit,
                "edge":        edge,
                "confidence":  conf,
                "flag_path":   flag,
                "time_horizon": horiz,
            })
        self.matches = matches

    def compute_stats(self) -> dict:
        m = self.matches
        total   = len(self.signals)
        matched = len(m)
        hits    = sum(1 for r in m if r["hit"])
        hit_rate = hits / matched if matched > 0 else None
        avg_edge = sum(r["edge"] for r in m) / matched if matched > 0 else None

        def _group(key_fn, labels):
            result = {}
            for label in labels:
                subset = [r for r in m if key_fn(r) == label]
                n = len(subset)
                h = sum(1 for r in subset if r["hit"])
                result[label] = {"n": n, "hits": h, "rate": h / n if n > 0 else None}
            return result

        def _horizon_label(r):
            raw = r["time_horizon"]
            if "INTRA" in raw:           return "Intraday"
            if "WEEK" in raw:            return "Weekly"
            if "QUART" in raw:           return "Quarterly"
            if "LONG" in raw or "MONTH" in raw or "ANNUAL" in raw:
                                          return "Long-Term"
            return "UNKNOWN"

        return {
            "total":        total,
            "matched":      matched,
            "hit_rate":     hit_rate,
            "avg_edge":     avg_edge,
            "by_confidence": _group(lambda r: r["confidence"], ["HIGH", "MED", "LOW"]),
            "by_flag_path":  _group(lambda r: r["flag_path"],
                                    sorted({r["flag_path"] for r in m} or {"UNKNOWN"})),
            "by_horizon":    _group(_horizon_label,
                                    ["Intraday", "Weekly", "Quarterly", "Long-Term", "UNKNOWN"]),
        }

    def report(self, output_path: str) -> None:
        """Write plain-text report to file and print summary to stdout."""
        stats = self.compute_stats()

        def _rate(v):
            return f"{v:.1%}" if v is not None else "N/A"

        def _edge(v):
            return f"{v:.4f}" if v is not None else "N/A"

        lines = [
            "=" * 60,
            "LEVIATHAN BACKTEST REPORT",
            "=" * 60,
            "",
            f"Signals loaded:   {stats['total']}",
            f"Matched:          {stats['matched']}",
            f"Hit rate:         {_rate(stats['hit_rate'])}",
            f"Avg edge:         {_edge(stats['avg_edge'])}",
            "",
            "By Confidence:",
        ]
        for label, d in stats["by_confidence"].items():
            lines.append(f"  {label:<6}  n={d['n']:>3}  hits={d['hits']:>3}  rate={_rate(d['rate'])}")
        lines.append("")
        lines.append("By Signal Path:")
        for path, d in stats["by_flag_path"].items():
            lines.append(f"  {path:<12}  n={d['n']:>3}  hits={d['hits']:>3}  rate={_rate(d['rate'])}")
        lines.append("")
        lines.append("By Horizon:")
        for h, d in stats["by_horizon"].items():
            if d["n"] > 0:
                lines.append(f"  {h:<12}  n={d['n']:>3}  hits={d['hits']:>3}  rate={_rate(d['rate'])}")
        lines.append("")
        lines.append("=" * 60)

        content = "\n".join(lines)
        print(content)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)


def main():
    parser = argparse.ArgumentParser(description="Leviathan backtest harness")
    parser.add_argument("--signals",     required=True, metavar="PATH")
    parser.add_argument("--resolutions", required=True, metavar="PATH")
    parser.add_argument("--output",      required=True, metavar="PATH")
    args = parser.parse_args()

    runner = BacktestRunner()
    runner.load_signals(args.signals)
    runner.load_resolutions(args.resolutions)
    runner.match_signals_to_resolutions()
    runner.report(args.output)


if __name__ == "__main__":
    main()
