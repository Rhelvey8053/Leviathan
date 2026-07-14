"""
Backtest harness for Leviathan signals.

Replays historical signals against resolved market outcomes to measure
strategy performance across confidence tiers, signal paths, and time horizons.
Scaffold only — no network calls. Plug real resolution data in once available.

Usage:
  python backtest.py --signals data/powerbi_export/signals.csv \\
                     --resolutions sample_resolutions.csv \\
                     --output report.txt

Walk-forward validation (rolling out-of-sample check on the scoring model):
  python backtest.py --signals data/powerbi_export/signals.csv \\
                     --resolutions sample_resolutions.csv \\
                     --output report.txt \\
                     --walk-forward [--min-train N] [--window N]

Sorts matched signals by resolutions' close_date and, one signal at a time,
scores the next signal as an out-of-sample test point against everything
resolved before it. Compares the aggregated out-of-sample hit rate to the
full-sample hit rate to flag whether apparent edge is real or an in-sample
artifact — see BacktestRunner.walk_forward / walk_forward_summary.
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
                "close_date":  res.get("close_date") or "",
            })
        self.matches = matches

    def walk_forward(self, min_train: int = 3, window: int | None = None) -> list[dict]:
        """
        Rolling out-of-sample validation over matched signals.

        Sorts matches chronologically by close_date, then walks forward one
        signal at a time: at each step, everything before it is the "train"
        set (what we'd have known at that point), and the signal itself is
        an out-of-sample test point the train set never saw. Aggregating
        those test-only outcomes gives a hit rate that can't be inflated by
        fitting on data the model was scored against.

        window=None uses an expanding train set (all history to date).
        window=N restricts training to the N most recent prior signals,
        approximating a fixed-size rolling window.

        Requires close_date on resolutions (see load_resolutions). Matches
        missing a close_date are excluded — they can't be ordered.

        Returns one row per walk-forward step, or [] if there are fewer
        than min_train + 1 dateable matches.
        """
        ordered = sorted(
            (m for m in self.matches if m.get("close_date")),
            key=lambda m: m["close_date"],
        )
        if len(ordered) <= min_train:
            return []

        folds = []
        oos_hits = 0
        for i in range(min_train, len(ordered)):
            train = ordered[i - window:i] if window else ordered[:i]
            test = ordered[i]
            train_hits = sum(1 for r in train if r["hit"])
            oos_hits += 1 if test["hit"] else 0
            oos_n = i - min_train + 1
            folds.append({
                "fold":                  len(folds) + 1,
                "close_date":            test["close_date"],
                "train_n":               len(train),
                "train_hit_rate":        train_hits / len(train) if train else None,
                "test_ticker":           test["ticker"],
                "test_hit":              test["hit"],
                "cumulative_oos_n":      oos_n,
                "cumulative_oos_hits":   oos_hits,
                "cumulative_oos_hit_rate": oos_hits / oos_n,
            })
        return folds

    def walk_forward_summary(self, folds: list[dict]) -> dict:
        """
        Compares the final out-of-sample hit rate against the full-sample
        (in-sample) hit rate over the same dateable matches, to flag whether
        apparent edge is likely an in-sample artifact.
        """
        if not folds:
            return {
                "oos_n": 0, "oos_hit_rate": None,
                "in_sample_hit_rate": None, "delta": None, "verdict": "INSUFFICIENT_DATA",
            }

        dateable = [m for m in self.matches if m.get("close_date")]
        in_sample_hits = sum(1 for m in dateable if m["hit"])
        in_sample_rate = in_sample_hits / len(dateable) if dateable else None

        last = folds[-1]
        oos_rate = last["cumulative_oos_hit_rate"]
        delta = (oos_rate - in_sample_rate) if in_sample_rate is not None else None

        if delta is None:
            verdict = "INSUFFICIENT_DATA"
        elif last["cumulative_oos_n"] < 5:
            verdict = "TOO_FEW_FOLDS_TO_JUDGE"
        elif delta < -0.15:
            verdict = "DEGRADES_OUT_OF_SAMPLE"
        elif delta > 0.15:
            verdict = "IMPROVES_OUT_OF_SAMPLE"
        else:
            verdict = "STABLE"

        return {
            "oos_n":              last["cumulative_oos_n"],
            "oos_hit_rate":        oos_rate,
            "in_sample_hit_rate":  in_sample_rate,
            "delta":               delta,
            "verdict":             verdict,
        }

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

    def report(self, output_path: str, walk_forward: bool = False,
               min_train: int = 3, window: int | None = None) -> None:
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

        if walk_forward:
            folds = self.walk_forward(min_train=min_train, window=window)
            summary = self.walk_forward_summary(folds)
            lines.append("=" * 60)
            lines.append("WALK-FORWARD VALIDATION (rolling out-of-sample)")
            lines.append("=" * 60)
            mode = f"rolling window={window}" if window else "expanding"
            lines.append(f"Mode:             {mode}  (min_train={min_train})")
            if not folds:
                lines.append("Not enough dateable matched signals to run walk-forward yet"
                              f" (need > {min_train}).")
            else:
                lines.append("")
                lines.append(f"{'Fold':>4}  {'Date':<10}  {'Ticker':<14}  {'TrainRate':>9}  {'Test':>4}  {'CumOOSRate':>10}")
                for f in folds:
                    tr = _rate(f["train_hit_rate"])
                    hit = "HIT" if f["test_hit"] else "miss"
                    cum = _rate(f["cumulative_oos_hit_rate"])
                    lines.append(f"{f['fold']:>4}  {f['close_date']:<10}  {f['test_ticker'][:14]:<14}"
                                 f"  {tr:>9}  {hit:>4}  {cum:>10}")
                lines.append("")
                lines.append(f"Out-of-sample n:      {summary['oos_n']}")
                lines.append(f"Out-of-sample rate:   {_rate(summary['oos_hit_rate'])}")
                lines.append(f"In-sample rate:       {_rate(summary['in_sample_hit_rate'])}")
                delta = summary["delta"]
                lines.append(f"Delta (OOS - in):     {f'{delta:+.1%}' if delta is not None else 'N/A'}")
                lines.append(f"Verdict:              {summary['verdict']}")
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
    parser.add_argument("--walk-forward", action="store_true",
                        help="Add rolling out-of-sample validation section to the report")
    parser.add_argument("--min-train", type=int, default=3, metavar="N",
                        help="Warm-up size before walk-forward testing begins (default: 3)")
    parser.add_argument("--window", type=int, default=None, metavar="N",
                        help="Rolling train-window size; omit for an expanding window")
    args = parser.parse_args()

    runner = BacktestRunner()
    runner.load_signals(args.signals)
    runner.load_resolutions(args.resolutions)
    runner.match_signals_to_resolutions()
    runner.report(args.output, walk_forward=args.walk_forward,
                  min_train=args.min_train, window=args.window)


if __name__ == "__main__":
    main()
