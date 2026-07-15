"""
analysis/eval_rescore.py — Re-score harness for the eval dataset.

Runs the CURRENT scoring prompt (core/scorer.py's build_prompt /
build_system_prompt — read, never modified here) across every market in
a frozen eval dataset, dispatching to whichever backend config.json's
llm.backend names (mirrors score_markets()'s own dispatch exactly):

  backend="api" — Anthropic Messages API via core/llm.py, temperature
                  pinned to 0 for reproducibility. Requires a valid
                  ANTHROPIC_API_KEY.
  backend="cli" — local Claude CLI subprocess (Pro OAuth), same path
                  production uses by default. The CLI does not expose a
                  temperature control, so "identical estimates" on this
                  path is a best-effort reproducibility check, not a
                  guaranteed one.

IMPORTANT — this is a pipeline-determinism check, not a fresh forecast.
Markets in the frozen dataset have already resolved and are public
knowledge, so live web search will likely surface the actual outcome.
Re-scored estimates are NOT used as honest forecasts anywhere in this
repo — analysis/eval.py grades the ORIGINAL at-signal-time estimates
instead. What this module proves is that the pipeline itself (same
prompt + same inputs -> same output) is deterministic, which is what
matters when comparing prompt version A vs B on markets that have NOT
yet resolved.

Explicitly NOT part of the default `python analysis/eval.py` run —
costs real money/time on the API backend, and a real CLI subprocess
call on the cli backend. Invoke directly:

    python analysis/eval_rescore.py            # re-score once
    python analysis/eval_rescore.py --check     # re-score twice, diff the runs
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))

from core import logger, llm
from core.scorer import build_prompt, build_system_prompt, _score_via_cli

from analysis.eval_dataset import load_latest


def _load_config() -> dict:
    cfg_path = ROOT / "config.json"
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            return json.load(f)
    with open(ROOT / "config.example.json", encoding="utf-8") as f:
        return json.load(f)


def _market_dict_for_ticker(ticker: str) -> dict | None:
    """Reconstruct a scorer-compatible market dict from the stored signal row."""
    rows = logger.get_signal_log(limit=1, ticker=ticker)
    if not rows:
        return None
    row = dict(rows[0])
    row["mid_price"] = row.get("market_price")
    return row


def rescore_dataset(dataset: dict, temperature: float = 0.0, config: dict | None = None) -> dict:
    """
    Re-scores every market in a frozen dataset in one batched call
    (matching production's score_markets batching), dispatching to
    whichever backend config.json's llm.backend names — same dispatch
    score_markets() itself uses. Returns {ticker: score_dict}.
    """
    config = config or _load_config()
    markets = []
    for row in dataset["rows"]:
        m = _market_dict_for_ticker(row["market_id"])
        if m is not None:
            markets.append(m)
    if not markets:
        return {}

    user_prompt = build_prompt(markets)
    sys_prompt = build_system_prompt()

    backend = config.get("llm", {}).get("backend", "cli")
    if backend == "api":
        scores, _token_info = llm.score_via_api(
            sys_prompt, user_prompt, config, temperature=temperature
        )
    else:
        scores = _score_via_cli(sys_prompt, user_prompt)
    return {s["ticker"]: s for s in scores}


def check_determinism(dataset: dict, temperature: float = 0.0, config: dict | None = None) -> dict:
    """Re-scores the dataset twice and reports whether the estimates matched exactly."""
    config = config or _load_config()
    run1 = rescore_dataset(dataset, temperature=temperature, config=config)
    run2 = rescore_dataset(dataset, temperature=temperature, config=config)

    tickers = sorted(set(run1) | set(run2))
    diffs = []
    for t in tickers:
        e1 = run1.get(t, {}).get("our_estimate")
        e2 = run2.get(t, {}).get("our_estimate")
        if e1 != e2:
            diffs.append({"ticker": t, "run1": e1, "run2": e2})

    return {
        "n_scored":  len(tickers),
        "identical": len(diffs) == 0,
        "diffs":     diffs,
        "run1":      run1,
        "run2":      run2,
    }


def main():
    parser = argparse.ArgumentParser(description="Leviathan eval re-score harness")
    parser.add_argument("--check", action="store_true",
                         help="Re-score twice and report whether results are identical")
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args()

    print("WARNING: re-scoring already-resolved, publicly-known markets via live web")
    print("search. Estimates below reflect hindsight contamination and must NOT be")
    print("read as honest forecasts. See module docstring.\n")

    config = _load_config()
    backend = config.get("llm", {}).get("backend", "cli")
    if backend != "api":
        print(f"NOTE: backend='{backend}' (config.json llm.backend) has no temperature")
        print("control. \"Identical\" below is a best-effort reproducibility check, not")
        print("a guaranteed one -- only backend='api' pins temperature=0.\n")

    dataset = load_latest()

    if args.check:
        result = check_determinism(dataset, temperature=args.temperature, config=config)
        print(f"Re-scored {result['n_scored']} markets twice via backend='{backend}'")
        print(f"Identical both runs: {result['identical']}")
        if not result["identical"]:
            print("Diffs:")
            for d in result["diffs"]:
                print(f"  {d['ticker']}: run1={d['run1']}  run2={d['run2']}")
    else:
        scores = rescore_dataset(dataset, temperature=args.temperature)
        for ticker, s in scores.items():
            print(f"  {ticker:<45} estimate={s.get('our_estimate')}  direction={s.get('direction')}")


if __name__ == "__main__":
    main()
