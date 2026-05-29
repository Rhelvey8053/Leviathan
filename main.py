import io
import json
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

import kalshi
import polymarket
import external_markets
import accounts
import scanner
import whales
import scorer
import logger
import report

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def _fmt_usd(value) -> str:
    try:
        return f"${float(value):.4f}"
    except (TypeError, ValueError):
        return "$—"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def estimate_cost(token_info: dict, model: str) -> float:
    # Scoring runs via claude CLI (Pro subscription) — no per-token API billing
    return 0.0


def main():
    run_id     = str(uuid.uuid4())[:8]
    start_time = time.time()
    print(f"\n=== Leviathan v1 | run {run_id} | {datetime.now(timezone.utc).isoformat()} ===\n")

    config = load_config()
    print(f"Environment: {config.get('environment', 'demo').upper()}")

    run_meta = {
        "run_id":            run_id,
        "timestamp":         datetime.now(timezone.utc).isoformat(),
        "markets_scanned":   0,
        "signals_generated": 0,
        "whale_flags":       0,
        "model_used":        config.get("scoring", {}).get("scorer_model", "claude-sonnet-4-6"),
        "tokens_used":       0,
        "cost_usd":          0,
        "runtime_ms":        0,
    }

    all_markets    = []
    flagged_markets = []
    whale_results  = {}
    final_signals  = []
    whale_only     = []
    token_info     = {"input_tokens": 0, "output_tokens": 0}

    # Step 1 — Authenticate
    print("[1/8] Authenticating with Kalshi...")
    try:
        kalshi.authenticate(config)
        print("      OK")
    except Exception as e:
        print(f"      FAILED: {e}")
        print("      Cannot proceed without valid auth. Exiting.")
        return

    # Step 2 — Fetch markets via events catalog
    print("[2/8] Fetching markets via events catalog...")
    try:
        events     = kalshi.fetch_events(config)
        categories = config.get("markets", {}).get("categories", [])
        if categories:
            events = [e for e in events if e.get("category", "") in categories]

        seen = set()
        for event in events:
            event_ticker = event.get("event_ticker") or event.get("ticker", "")
            if not event_ticker or "KXMVE" in event_ticker:
                continue
            try:
                for m in kalshi.fetch_event_markets(config, event_ticker):
                    t = m.get("ticker")
                    if t and t not in seen:
                        seen.add(t)
                        all_markets.append(m)
            except Exception:
                pass
        print(f"      Fetched {len(all_markets)} markets from {len(events)} events")
    except Exception as e:
        print(f"      Events fetch failed ({e}), falling back to /markets...")
        try:
            all_markets = kalshi.fetch_markets(config)
            print(f"      Fetched {len(all_markets)} markets (fallback)")
        except Exception as e2:
            print(f"      FAILED: {e2}")
            traceback.print_exc()

    # Step 3 — Filter and score for mispricing
    print("[3/8] Scanning for mispriced markets...")
    try:
        filtered        = scanner.filter_markets(all_markets, config)
        scored_markets  = scanner.score_markets(filtered, config)
        flagged_markets = [m for m in scored_markets if m.get("flag")]
        print(f"      {len(filtered)} markets passed filter (from {len(all_markets)})")
        print(f"      {len(flagged_markets)} markets flagged for edge")
    except Exception as e:
        print(f"      FAILED: {e}")
        traceback.print_exc()

    run_meta["markets_scanned"] = len(all_markets)

    # Step 4 — Polymarket cross-reference
    print("[4/8] Cross-referencing with Polymarket...")
    poly_data = {}
    try:
        if flagged_markets and config.get("polymarket", {}).get("enabled", True):
            poly_data = polymarket.enrich_flagged(flagged_markets, config)
            matched   = sum(1 for v in poly_data.values() if v.get("price_gap") is not None)
            gaps      = [v for v in poly_data.values() if v.get("price_gap") and abs(v["price_gap"]) >= 0.05]
            print(f"      {matched} Polymarket matches found, {len(gaps)} with gap ≥5%")
        else:
            print("      Skipped (disabled or no flagged markets)")
    except Exception as e:
        print(f"      FAILED: {e}")
        traceback.print_exc()

    for m in flagged_markets:
        m["poly"] = poly_data.get(m.get("ticker"))

    # External market cross-reference (Manifold + PredictIt)
    ext_data = {}
    try:
        ext_data = external_markets.cross_reference(flagged_markets, config)
    except Exception as e:
        print(f"      External markets failed: {e}")

    for m in flagged_markets:
        ticker     = m.get("ticker", "")
        matches    = ext_data.get(ticker, [])
        m["ext_markets"] = matches
        m["ext_consensus"] = external_markets.consensus_summary(matches, m.get("mid_price") or 0)

    # Smart money: check if winning Polymarket wallets are in matched markets
    smart_money = {}
    try:
        smart_money = accounts.enrich_with_smart_money(flagged_markets, poly_data, config)
        total_signals = sum(len(v) for v in smart_money.values())
        if total_signals:
            print(f"      Smart money: {total_signals} winning wallet(s) positioned in {len(smart_money)} market(s)")
    except Exception as e:
        print(f"      Smart money scan failed: {e}")

    for m in flagged_markets:
        m["smart_money"] = smart_money.get(m.get("ticker"), [])

    # Step 5 — Whale detection + order book depth
    print("[5/8] Checking whale activity and order book depth...")
    try:
        trades_by_ticker  = {}
        orderbook_by_ticker = {}
        for ticker in [m["ticker"] for m in flagged_markets if m.get("ticker")]:
            try:
                trades_by_ticker[ticker] = kalshi.fetch_trades(config, ticker)
            except Exception:
                trades_by_ticker[ticker] = []
            try:
                ob = kalshi.fetch_orderbook(config, ticker)
                orderbook_by_ticker[ticker] = scanner.compute_orderbook_signal(ob)
            except Exception:
                orderbook_by_ticker[ticker] = {}

        whale_results = {
            w["ticker"]: w
            for w in whales.scan_all_markets(list(trades_by_ticker), trades_by_ticker, config)
        }
        print(f"      {len(whale_results)} whale flags  |  {sum(1 for v in orderbook_by_ticker.values() if v.get('ob_flag'))} order book imbalances")
        run_meta["whale_flags"] = len(whale_results)
    except Exception as e:
        print(f"      FAILED: {e}")
        traceback.print_exc()

    for m in flagged_markets:
        ticker = m.get("ticker")
        whale  = whale_results.get(ticker)
        m["whale_data"]     = whale
        m["whale_reversal"] = scanner.compute_whale_reversal(m, whale)
        m.update(orderbook_by_ticker.get(ticker) or {})
        if (m["whale_reversal"] or m.get("ob_flag")) and not m.get("flag"):
            m["flag"] = True

    # Sort: time-sensitive first (INTRADAY before LONG), then by volume within each bucket
    flagged_markets.sort(
        key=lambda m: (
            scanner.BUCKET_PRIORITY.get(m.get("time_horizon", "MONTHLY"), 2),
            -float(m.get("volume_fp") or m.get("volume") or 0),
        )
    )

    # Step 6 — Score with Claude + web search
    print("[6/8] Scoring with Claude...")

    claude_scores = []
    try:
        if flagged_markets:
            claude_scores, token_info = scorer.score_markets(flagged_markets, config)
            print(f"      Scored {len(claude_scores)} markets via claude CLI")
        else:
            print("      No flagged markets to score.")
    except Exception as e:
        print(f"      FAILED: {e}")
        traceback.print_exc()

    cost = estimate_cost(token_info, run_meta["model_used"])
    run_meta["tokens_used"] = token_info.get("input_tokens", 0) + token_info.get("output_tokens", 0)
    run_meta["cost_usd"]    = cost

    # Merge Claude scores with full market context (including upstream signals)
    scored_by_ticker   = {s["ticker"]: s for s in claude_scores}
    conf_threshold_rank = {"HIGH": 0, "MED": 1, "LOW": 2}
    threshold_rank      = conf_threshold_rank.get(
        config.get("scoring", {}).get("confidence_threshold", "MED"), 1
    )

    for m in flagged_markets:
        ticker = m.get("ticker", "")
        cs     = scored_by_ticker.get(ticker)
        if not cs:
            continue

        whale  = whale_results.get(ticker, {})
        signal = {
            **cs,
            "title":           m.get("title", cs.get("title", "")),
            "whale_detected":  whale.get("whale_detected", False),
            "whale_direction": whale.get("whale_direction"),
            "whale_reversal":  m.get("whale_reversal", False),
            "drift_flag":      m.get("drift_flag", False),
            "price_drift":     m.get("price_drift"),
            "spread_wide":     m.get("spread_wide", False),
            "spread_pct":      m.get("spread_pct"),
            "ob_flag":         m.get("ob_flag", False),
            "ob_imbalance":    m.get("ob_imbalance"),
            "ob_direction":    m.get("ob_direction"),
            "time_horizon":    m.get("time_horizon", "MONTHLY"),
            "poly":            m.get("poly"),
            "ext_markets":     m.get("ext_markets", []),
            "ext_consensus":   m.get("ext_consensus", {}),
            "smart_money":     m.get("smart_money", []),
            "run_id":          run_id,
        }

        if conf_threshold_rank.get(cs.get("confidence", "LOW"), 2) <= threshold_rank and cs.get("direction", "PASS") != "PASS":
            final_signals.append(signal)
        elif whale.get("whale_detected"):
            whale_only.append({**whale, "title": m.get("title", "")})

    run_meta["signals_generated"] = len(final_signals)

    # Tag signals as new or repeat (seen in past 7 days)
    recent_tickers = logger.get_recent_tickers(days=7)
    for sig in final_signals:
        sig["is_repeat"] = sig.get("ticker", "") in recent_tickers
    new_signals    = [s for s in final_signals if not s.get("is_repeat")]
    repeat_signals = [s for s in final_signals if s.get("is_repeat")]

    # Step 7 — Log signals
    print("[7/8] Logging signals...")
    try:
        for sig in new_signals:  # only log new signals to avoid duplicate rows
            logger.log_signal(sig)
        run_meta["runtime_ms"] = int((time.time() - start_time) * 1000)
        logger.log_run(run_meta)
        print(f"      Logged {len(new_signals)} new, {len(repeat_signals)} repeat signal(s)")
    except Exception as e:
        print(f"      FAILED: {e}")
        traceback.print_exc()

    # Weekly digest — send on Sundays or if explicitly triggered
    now_local = datetime.now(timezone.utc)
    if now_local.weekday() == 6:  # Sunday
        try:
            week_sigs = logger.get_week_signals(days=7)
            if week_sigs:
                weekly_body = report.compile_weekly_digest(week_sigs, logger.get_stats(), config)
                report.send_report(weekly_body, [], 0, config,
                                   subject_override=f"Leviathan Weekly — {now_local.strftime('%b %d, %Y')}")
                print("      Weekly digest sent")
        except Exception as e:
            print(f"      Weekly digest failed: {e}")

    # Step 8 — Compile and email report
    print("[8/8] Sending report...")
    try:
        stats = logger.get_stats()
        body  = report.compile_report(final_signals, whale_only, stats, run_meta, config,
                                      all_filtered=filtered,
                                      new_signals=new_signals,
                                      repeat_signals=repeat_signals)
        report.send_report(body, final_signals, run_meta["whale_flags"], config)
    except Exception as e:
        print(f"      FAILED: {e}")
        traceback.print_exc()
        print("\n--- REPORT (unsent) ---")
        try:
            body = report.compile_report(final_signals, whale_only, logger.get_stats(), run_meta, config)
            print(body)
        except Exception:
            pass

    print(f"\n=== Done in {time.time() - start_time:.1f}s | {len(final_signals)} signals | cost {_fmt_usd(cost)} ===\n")


if __name__ == "__main__":
    main()
