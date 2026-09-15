"""
analysis/forecastbench_pilot.py -- bounded external-calibration pilot
against ForecastBench's public dataset (backlog: ai-workflow-research-
findings-2026-09, Finding 2). Ruled out KalshiBench-v2 for this same
purpose 2026-09-15: every question in it predates 2026-01-01 (Claude
Sonnet 5's training cutoff) and it has no market-price baseline at all.
ForecastBench is different on both counts -- it's a live, bi-weekly-
updated benchmark (forecastingresearch/forecastbench-datasets on GitHub,
public, no auth) with a real "freeze_datetime_value" (the market price at
scoring time) for Kalshi-sourced questions specifically, and its most
recent resolution set (2026-08-30) has resolved Kalshi questions dated
well past the cutoff.

WHAT THIS DOES: pulls resolved, Kalshi-sourced questions from a
ForecastBench resolution_set + its matching question_set, cross-checks
the ones ForecastBench's own file still shows unresolved against the
live Kalshi API directly (Kalshi is authoritative and more current than
a bi-weekly snapshot file), maps them to Leviathan's own market dict
shape, and runs them through the REAL production scorer (core.scorer's
build_prompt/build_system_prompt/_score_via_cli -- unmodified, same
functions score_markets() itself dispatches to) exactly the way
backtesting/replay_runner.py reuses the live pipeline rather than a
parallel one. Grades with the identical Brier formula core.logger.
brier_component() uses (see grade()'s docstring), so the result is
directly comparable to the project's own live Brier/ECE numbers.

LOOK-AHEAD CONTAMINATION CAVEAT -- same structural, unfixable limitation
backtesting/replay_runner.py already documents and accepts: the scorer
runs with WebSearch enabled (matching production), and these questions
have already resolved and may be publicly discoverable, so a good Brier
score here is not proof of forecasting skill the way a genuinely blind
forecast would be. What it DOES rule out: if the scorer performs POORLY
even with search access to the real outcome, that is still informative
(a lower bound on how bad calibration can be), and a materially
different Brier from the project's own live number either way is a
useful cross-check for the checkpoint post-mortem. Never pooled with
live `signals` table data -- results write only to reports/
forecastbench_pilot.md, isolated from any WIN/LOSS-grading table.

USAGE:
    python analysis/forecastbench_pilot.py                    # live pilot run
    python analysis/forecastbench_pilot.py --dry-run           # build+print the market list, score nothing
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import kalshi, logger, scorer

GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/forecastingresearch/"
    "forecastbench-datasets/main/datasets"
)
REPORT_PATH = ROOT / "reports" / "forecastbench_pilot.md"


def fetch_json(url: str) -> dict:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_resolution_set(date_str: str) -> dict:
    return fetch_json(f"{GITHUB_RAW_BASE}/resolution_sets/{date_str}_resolution_set.json")


def fetch_question_set(filename: str) -> dict:
    return fetch_json(f"{GITHUB_RAW_BASE}/question_sets/{filename}")


def _to_market_dict(question: dict, ground_truth: float, resolution_date: str | None) -> dict | None:
    """
    Maps one ForecastBench question + its resolved outcome to Leviathan's
    own market dict shape (feeds core.scorer.build_prompt() unmodified).
    Returns None when freeze_datetime_value isn't a usable 0-1 price --
    a market missing this has nothing comparable to Kalshi's own
    mid_price, so it's excluded rather than guessed at.
    """
    try:
        mid_price = float(question.get("freeze_datetime_value"))
    except (TypeError, ValueError):
        return None
    if not (0.0 < mid_price < 1.0):
        return None
    return {
        "ticker": question["id"],
        "title": question.get("question", ""),
        "mid_price": mid_price,
        "close_time": question.get("market_info_close_datetime") or "",
        "time_horizon": "MONTHLY",
        "flag_path": None,
        "_ground_truth": float(ground_truth),
        "_freeze_datetime": question.get("freeze_datetime"),
        "_resolution_date": resolution_date,
    }


def build_pilot_markets(resolution_date: str, config: dict, source: str = "kalshi",
                         cross_check_live: bool = True) -> list[dict]:
    """
    Fetches one ForecastBench resolution_set + its matching question_set,
    keeps `source`-sourced questions ForecastBench's own file marks
    resolved, and (when cross_check_live=True) additionally checks every
    NOT-yet-resolved-per-ForecastBench question of that source directly
    against the live Kalshi API (kalshi.fetch_market) -- ForecastBench's
    bi-weekly snapshot lags real settlement by however long has passed
    since its own forecast_due_date, so a question that resolved AFTER
    that check still shows unresolved in the file even though Kalshi
    itself now has a real result. Only meaningful for source="kalshi" --
    cross_check_live is silently a no-op for any other source since
    there's no equivalent live-lookup path wired here.

    Returns markets with "_ground_truth"/"_freeze_datetime"/
    "_resolution_date" keys (leading underscore -- not real Leviathan
    market fields, strip before passing to build_prompt()).
    """
    res_set = fetch_resolution_set(resolution_date)
    q_set = fetch_question_set(res_set["question_set"])
    questions_by_id = {q["id"]: q for q in q_set["questions"]}

    markets: list[dict] = []
    seen_ids: set[str] = set()

    for r in res_set["resolutions"]:
        if r.get("source") != source:
            continue
        qid = r["id"]
        question = questions_by_id.get(qid)
        if not question or qid in seen_ids:
            continue

        if r.get("resolved"):
            m = _to_market_dict(question, r["resolved_to"], r.get("resolution_date"))
            if m:
                markets.append(m)
                seen_ids.add(qid)
        elif cross_check_live and source == "kalshi":
            try:
                live = kalshi.fetch_market(config, qid)
            except Exception:
                continue
            result = (live or {}).get("result", "")
            if result not in ("yes", "no"):
                continue
            ground_truth = 1.0 if result == "yes" else 0.0
            m = _to_market_dict(question, ground_truth, None)
            if m:
                m["_resolution_date"] = "live-cross-check"
                markets.append(m)
                seen_ids.add(qid)

    return markets


def score_pilot_markets(markets: list[dict], config: dict, calibration: dict | None = None,
                         flag_cal: list | None = None, batch_size: int = 10,
                         now: datetime | None = None) -> tuple[dict, dict]:
    """
    Scores markets through the real production scorer, chunked into
    batch_size groups matching config.scoring.max_markets_per_run's own
    default shape rather than one giant prompt. Returns (scored_by_ticker,
    combined_token_info).
    """
    sys_prompt = scorer.build_system_prompt(calibration, flag_cal=flag_cal)
    scored_by_ticker: dict[str, dict] = {}
    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
              "cost_usd": 0.0}

    for i in range(0, len(markets), batch_size):
        batch = markets[i:i + batch_size]
        clean_batch = [{k: v for k, v in m.items() if not k.startswith("_")} for m in batch]
        user_prompt = scorer.build_prompt(clean_batch, now=now)
        scores, tok = scorer._score_via_cli(sys_prompt, user_prompt, config)
        for key in totals:
            totals[key] += tok.get(key, 0) or 0
        for s in scores:
            scored_by_ticker[s.get("ticker")] = s

    return scored_by_ticker, totals


def score_pilot_markets_blind(markets: list[dict], config: dict, calibration: dict | None = None,
                               flag_cal: list | None = None, batch_size: int = 10,
                               now: datetime | None = None) -> tuple[dict, dict]:
    """
    One-off validation variant, NOT a change to core.scorer._score_via_cli
    (production keeps WebSearch unconditionally -- that's correct for
    live scoring; this function exists only to measure this pilot's own
    look-ahead-contamination gap, not to become a new production path).

    Live-verified 2026-09-15: score_pilot_markets() (WebSearch enabled,
    matching production) scored 10/16 already-resolved post-cutoff
    Kalshi questions at 0.01/0.99 confidence with mean Brier 0.0003 --
    a textbook contamination signature (the market-price Brier baseline
    over the same population was 0.1212, two orders of magnitude worse)
    -- the model is finding the real outcome via search, not forecasting
    under uncertainty. This function omits --allowedTools WebSearch
    entirely so the CLI's own default Manual permission mode denies any
    search attempt (headless -p has nobody to approve it), forcing the
    model to answer from parametric knowledge alone. Still not a fully
    clean read (a post-cutoff event can correlate with pre-cutoff
    context the model does have), but removes the dominant, mechanical
    contamination source this pilot's own first run just demonstrated.

    Duplicates _score_via_cli's subprocess-invocation shape deliberately
    rather than adding a "web_search: bool" parameter to the shared
    production function for a one-off validation need.
    """
    import os as _os
    import re as _re
    import subprocess as _subprocess
    import tempfile as _tempfile
    from core.llm import _find_claude, RECORD_SCORES_TOOL

    sys_prompt = scorer.build_system_prompt(calibration, flag_cal=flag_cal)
    scored_by_ticker: dict[str, dict] = {}
    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
              "cost_usd": 0.0}

    claude_cmd = _find_claude()
    clean_env = {k: v for k, v in _os.environ.items() if k != "ANTHROPIC_API_KEY"}
    schema = json.dumps(RECORD_SCORES_TOOL["input_schema"])

    for i in range(0, len(markets), batch_size):
        batch = markets[i:i + batch_size]
        clean_batch = [{k: v for k, v in m.items() if not k.startswith("_")} for m in batch]
        user_prompt = scorer.build_prompt(clean_batch, now=now)

        sp_file = _tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        sp_file.write(sys_prompt)
        sp_file.close()
        cli_args = [claude_cmd, "--print", "--system-prompt-file", sp_file.name,
                    "--output-format", "json", "--json-schema", schema]
        try:
            result = _subprocess.run(
                cli_args, input=user_prompt, capture_output=True, text=True, timeout=600,
                encoding="utf-8", errors="replace", env=clean_env, cwd=_tempfile.gettempdir(),
            )
        finally:
            _os.unlink(sp_file.name)

        if result.returncode != 0:
            raise RuntimeError(f"score_pilot_markets_blind: exit {result.returncode}: "
                                f"{(result.stderr or result.stdout)[:500]}")
        envelope = json.loads(result.stdout.strip())
        usage = envelope.get("usage") or {}
        for key in totals:
            if key != "cost_usd":
                totals[key] += usage.get(key, 0) or 0
        totals["cost_usd"] += round(envelope.get("total_cost_usd", 0.0) or 0.0, 6)

        structured = envelope.get("structured_output")
        scores = structured.get("scores") if isinstance(structured, dict) else None
        if scores is None:
            all_text = (envelope.get("result") or "").strip()
            fence = _re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", all_text, _re.DOTALL)
            raw_json = fence.group(1).strip() if fence else all_text
            scores = json.loads(raw_json)
        for s in scores:
            scored_by_ticker[s.get("ticker")] = s

    return scored_by_ticker, totals


def grade(markets: list[dict], scored_by_ticker: dict[str, dict]) -> dict:
    """
    Brier score = mean((our_estimate - ground_truth)^2) over every
    YES/NO-directed call. Matches core.logger.brier_component() exactly:
    that function's outcome_binary (1 if the called direction WON, 0 if
    LOST) always equals the raw ground-truth "did YES happen" value
    regardless of which direction was called -- a YES call that wins
    means YES happened (ground_truth=1=outcome_binary); a NO call that
    wins means YES did NOT happen (ground_truth=0=outcome_binary) -- so
    this reduces to the identical formula without needing the WIN/LOSS
    re-derivation brier_component() does for the live signals table
    (which doesn't have a raw ground_truth column, only a result label).
    PASS calls are excluded, matching get_brier_score()'s own filter.
    """
    graded = []
    passed = []  # PASS calls (or missing scores) -- not Brier-graded, but kept
                 # for the report rather than silently dropped, so a run like
                 # the 2026-09-15 blind pilot (16/16 PASS) is inspectable --
                 # its own our_estimate/reasoning show WHY, not just that it did.
    for m in markets:
        s = scored_by_ticker.get(m["ticker"])
        direction = s.get("direction") if s else None
        est = s.get("our_estimate") if s else None

        if direction in ("YES", "NO") and est is not None:
            brier = (float(est) - m["_ground_truth"]) ** 2
            graded.append({
                "ticker": m["ticker"], "title": m["title"],
                "direction": direction, "confidence": s.get("confidence"),
                "our_estimate": float(est), "market_price": m["mid_price"],
                "ground_truth": m["_ground_truth"], "brier": brier,
                "reasoning": s.get("reasoning", ""),
            })
        else:
            passed.append({
                "ticker": m["ticker"], "title": m["title"],
                "direction": direction or "(no score)",
                "confidence": s.get("confidence") if s else None,
                "our_estimate": est, "market_price": m["mid_price"],
                "ground_truth": m["_ground_truth"],
                "reasoning": (s.get("reasoning", "") if s else ""),
            })

    n = len(graded)
    n_pass = len(passed)
    mean_brier = sum(g["brier"] for g in graded) / n if n else None
    market_briers = [
        (m["mid_price"] - m["_ground_truth"]) ** 2 for m in markets
        if scored_by_ticker.get(m["ticker"], {}).get("direction") in ("YES", "NO")
    ]
    mean_market_brier = sum(market_briers) / len(market_briers) if market_briers else None

    # "All-estimate" Brier -- uses our_estimate even on a PASS call (the
    # model still reports a probability, it just declines to act on it
    # per the edge threshold). Useful specifically for a run like the
    # 2026-09-15 blind pilot where almost everything PASSed: n/mean_brier
    # above would be near-empty and hide the actual calibration signal,
    # which is in the raw estimates themselves, not the few directional
    # calls. Paired with the market price over the SAME population (not
    # mean_market_brier's YES/NO-only one above) for a fair comparison.
    all_scored = [m for m in markets if scored_by_ticker.get(m["ticker"], {}).get("our_estimate") is not None]
    all_est_briers = [
        (float(scored_by_ticker[m["ticker"]]["our_estimate"]) - m["_ground_truth"]) ** 2
        for m in all_scored
    ]
    all_market_briers = [(m["mid_price"] - m["_ground_truth"]) ** 2 for m in all_scored]
    mean_all_estimate_brier = sum(all_est_briers) / len(all_est_briers) if all_est_briers else None
    mean_all_market_brier = sum(all_market_briers) / len(all_market_briers) if all_market_briers else None

    return {
        "graded": graded, "passed": passed, "n": n, "n_pass": n_pass,
        "mean_brier": mean_brier, "mean_market_brier": mean_market_brier,
        "n_all_scored": len(all_scored),
        "mean_all_estimate_brier": mean_all_estimate_brier,
        "mean_all_market_brier": mean_all_market_brier,
    }


def render_report(resolution_date: str, markets: list[dict], result: dict, token_info: dict,
                   mode: str = "search") -> str:
    if mode == "blind":
        caveat = (
            "**Mode: blind (WebSearch disabled)**. These questions have already resolved, "
            "but this run omits --allowedTools WebSearch so the model cannot look up the "
            "real outcome -- it answers from parametric knowledge alone. Not a fully clean "
            "read (a post-cutoff event can still correlate with pre-cutoff context the "
            "model has), but removes the dominant contamination source the search-enabled "
            "run (mode=search) demonstrated. See this module's docstring for the full "
            "caveat. Isolated from the live `signals` table -- never pooled with "
            "production win/loss grading."
        )
    else:
        caveat = (
            "**Mode: search (matches production) -- look-ahead contamination caveat**. "
            "These questions have already resolved and the scorer runs with WebSearch "
            "enabled, matching production -- a good Brier score here is not proof of "
            "blind forecasting skill, it may just mean the model found the real outcome. "
            "See this module's own docstring for the full caveat. Isolated from the live "
            "`signals` table -- never pooled with production win/loss grading."
        )
    lines = [
        "# ForecastBench External Calibration Pilot",
        "",
        f"Run: {datetime.now(timezone.utc).isoformat()}",
        f"ForecastBench resolution set: {resolution_date}",
        f"Source data: forecastingresearch/forecastbench-datasets (public, no auth)",
        "",
        caveat,
        "",
        f"n scored (YES/NO): {result['n']}  |  n PASS/unscored: {result['n_pass']}",
        f"Mean scorer Brier (YES/NO calls only): {result['mean_brier']:.4f}" if result['mean_brier'] is not None else "Mean scorer Brier (YES/NO calls only): n/a",
        f"Mean market-price Brier (same YES/NO population): {result['mean_market_brier']:.4f}" if result['mean_market_brier'] is not None else "Mean market-price Brier (same YES/NO population): n/a",
        "",
        f"All-estimate comparison (n={result['n_all_scored']}, includes PASS calls -- "
        "our_estimate is reported even when the model declines to act on it):",
        f"  Mean our_estimate Brier: {result['mean_all_estimate_brier']:.4f}" if result['mean_all_estimate_brier'] is not None else "  Mean our_estimate Brier: n/a",
        f"  Mean market-price Brier: {result['mean_all_market_brier']:.4f}" if result['mean_all_market_brier'] is not None else "  Mean market-price Brier: n/a",
        f"Token usage: {token_info}",
        "",
        "## Per-market detail",
        "",
        "| ticker | direction | confidence | our_estimate | market_price | ground_truth | brier |",
        "|---|---|---|---|---|---|---|",
    ]
    for g in sorted(result["graded"], key=lambda x: -x["brier"]):
        lines.append(
            f"| {g['ticker']} | {g['direction']} | {g['confidence']} | "
            f"{g['our_estimate']:.3f} | {g['market_price']:.3f} | {g['ground_truth']:.0f} | "
            f"{g['brier']:.4f} |"
        )

    if result.get("passed"):
        lines += [
            "",
            "## PASS / unscored (excluded from Brier, kept for inspection)",
            "",
            "| ticker | direction | confidence | our_estimate | market_price | ground_truth | reasoning |",
            "|---|---|---|---|---|---|---|",
        ]
        for p in result["passed"]:
            est_str = f"{p['our_estimate']:.3f}" if p["our_estimate"] is not None else "n/a"
            reasoning = (p["reasoning"] or "")[:150].replace("|", "/")
            lines.append(
                f"| {p['ticker']} | {p['direction']} | {p['confidence']} | "
                f"{est_str} | {p['market_price']:.3f} | {p['ground_truth']:.0f} | {reasoning} |"
            )

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="ForecastBench external calibration pilot")
    parser.add_argument("--resolution-date", default="2026-08-30",
                         help="ForecastBench resolution_set date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true",
                         help="Build and print the market list; score nothing")
    parser.add_argument("--blind", action="store_true",
                         help="Score with WebSearch disabled (see score_pilot_markets_blind's "
                              "docstring) instead of the search-enabled production path")
    args = parser.parse_args()

    cfg_path = ROOT / "config.json"
    config = json.loads(cfg_path.read_text(encoding="utf-8"))

    print(f"[forecastbench_pilot] fetching resolution set {args.resolution_date}...")
    markets = build_pilot_markets(args.resolution_date, config)
    print(f"[forecastbench_pilot] {len(markets)} resolved Kalshi-sourced markets "
          f"(ForecastBench's own check + live Kalshi cross-check)")

    if args.dry_run:
        for m in markets:
            print(f"  {m['ticker']}  price={m['mid_price']:.2f}  "
                  f"gt={m['_ground_truth']:.0f}  {m['title'][:80]}")
        return

    calibration = logger.get_stats_by_confidence()
    flag_cal = logger.get_stats_by_flag_path()

    mode = "blind" if args.blind else "search"
    score_fn = score_pilot_markets_blind if args.blind else score_pilot_markets
    print(f"[forecastbench_pilot] scoring {len(markets)} markets via the live CLI scorer (mode={mode})...")
    scored_by_ticker, token_info = score_fn(markets, config, calibration, flag_cal)
    result = grade(markets, scored_by_ticker)

    print(f"[forecastbench_pilot] n={result['n']} scored, {result['n_pass']} PASS")
    print(f"[forecastbench_pilot] mean scorer Brier (YES/NO only): {result['mean_brier']}")
    print(f"[forecastbench_pilot] mean market-price Brier (same pop): {result['mean_market_brier']}")
    print(f"[forecastbench_pilot] all-estimate (n={result['n_all_scored']}): "
          f"our_estimate Brier={result['mean_all_estimate_brier']}, "
          f"market Brier={result['mean_all_market_brier']}")

    report = render_report(args.resolution_date, markets, result, token_info, mode=mode)
    report_path = REPORT_PATH if mode == "search" else REPORT_PATH.with_name("forecastbench_pilot_blind.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"[forecastbench_pilot] wrote {report_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
