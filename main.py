import io
import json
import os
import random
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone

if sys.platform == "win32":
    import winsound

from dotenv import load_dotenv

load_dotenv()

from core import kalshi, scanner, whales, scorer, logger, report
from core.fees import kalshi_fee
from sources import polymarket, external_markets, accounts
from analysis.smart_money_scan import run_smart_money_scan, save_report as save_sm_report

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
    """API-equivalent cost at claude-sonnet-4-6 pricing ($3/$15 per M tokens).
    Actual run uses Pro subscription — no direct per-token API bill."""
    input_tokens  = token_info.get("input_tokens", 0)
    output_tokens = token_info.get("output_tokens", 0)
    if not input_tokens and not output_tokens:
        return 0.0
    return (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000


_MARKET_SHAPE_REQUIRED_FIELDS = ["ticker", "close_time", "yes_bid_dollars", "yes_ask_dollars", "title"]


def _validate_market_shape(markets: list[dict]) -> dict:
    """
    Checks fetched markets for the fields the rest of the pipeline assumes
    exist on every real Kalshi market object (unattended-ops: graceful
    degradation when an upstream API changes shape).

    A high anomaly_rate signals Kalshi's response shape has actually
    changed (fields renamed/dropped), not just "fewer markets today" --
    something filter_markets()/score_market() would otherwise absorb
    silently via their own `.get(x) or default` coercions, producing a
    quietly-empty or garbage scan rather than surfacing the real problem.

    Returns {"checked": N, "anomaly_rate": float, "missing_fields": {field: count}}.
    """
    missing_counts = {f: 0 for f in _MARKET_SHAPE_REQUIRED_FIELDS}
    if not markets:
        return {"checked": 0, "anomaly_rate": 1.0, "missing_fields": missing_counts}
    for m in markets:
        for f in _MARKET_SHAPE_REQUIRED_FIELDS:
            if f not in m:
                missing_counts[f] += 1
    anomalous = sum(1 for m in markets if any(f not in m for f in _MARKET_SHAPE_REQUIRED_FIELDS))
    return {"checked": len(markets), "anomaly_rate": anomalous / len(markets),
            "missing_fields": missing_counts}


def _sample_for_blind_arm(flagged_markets: list[dict], scored_by_ticker: dict,
                           n: int, run_id: str = "") -> list[dict]:
    """
    Picks up to n markets, from those the anchored scorer actually produced
    a score for, to run through the price-blind shadow scorer (backlog:
    price-blind-arm). Uses a random.Random(run_id) sample rather than the
    first n in flagged_markets' existing priority order -- that list is
    pre-sorted by pre-signal strength before scoring, so taking the head
    every time would systematically sample only the highest-conviction
    markets (large edge, whale activity, drift), which is exactly the
    slice where the anchored scorer is most likely to already be justified
    in leaning on the price. The whole point of this experiment is whether
    anchoring adds information on a REPRESENTATIVE sample of scored
    markets, not just the easiest ones. Seeding on run_id keeps a given
    run's sample reproducible if re-executed, without biasing which
    markets get picked. Pure selection only -- callers are responsible for
    making sure the result is never fed back into scored_by_ticker/
    final_signals; this function has no way to enforce that.
    """
    if n <= 0:
        return []
    eligible = [m for m in flagged_markets if m.get("ticker", "") in scored_by_ticker]
    if len(eligible) <= n:
        return eligible
    return random.Random(run_id).sample(eligible, n)


def _rescore_shortlist_for_clean_sources(
    final_signals: list[dict],
    flagged_markets: list[dict],
    config: dict,
    calibration: dict | None = None,
    flag_cal: dict | None = None,
) -> dict:
    """
    Re-score the published shortlist one-market-per-call for clean, honest
    source attribution (GOAL_phase2-6_decisions.md Decision 1). A batch
    score_markets() call can cover several markets in one API request, and
    Anthropic's web_search_tool_result blocks aren't tagged per-market --
    every score in that batch shares one `sources` list, which is a real
    misattribution once rendered as "the sources behind THIS pick." Only
    the handful of top-ranked markets need this -- cost is ~N_published
    extra API calls, not O(flagged_markets).

    Mutates each shortlisted signal's `sources` key in place (same object
    identity report.determine_top_shortlist returns, per its own
    docstring) and returns accounting info for the caller to fold into
    run_meta: {"rescored_count": n, "shortlist_size": n, "tokens": n,
    "cost_usd": float}. A market re-score that comes back empty leaves that
    pick's existing batch-scored `sources` untouched rather than losing the
    pick outright.

    Also mutates each successfully re-scored signal's `citations` key
    (backlog: citations-provenance-grounding) via a second, non-forced API
    call that re-fetches the pick's own sources and asks Claude to ground
    its reasoning in specific cited passages -- see scorer.ground_citations.
    A citations-grounding failure is non-fatal and independent of the
    `sources` re-score above: it never removes or downgrades `sources`,
    it just leaves `citations` unset on that signal.
    """
    shortlist = report.determine_top_shortlist(final_signals, config)
    markets_by_ticker = {m.get("ticker", ""): m for m in flagged_markets}
    rescored_count = 0
    tokens = 0
    cost_usd = 0.0
    for sig in shortlist:
        market = markets_by_ticker.get(sig.get("ticker", ""))
        if not market:
            continue
        fresh_score, rescore_info = scorer.rescore_single_market(
            market, config, calibration=calibration, flag_cal=flag_cal,
        )
        cost_usd += rescore_info.get("cost_usd", 0) or 0
        tokens   += (rescore_info.get("input_tokens", 0) or 0) + \
                    (rescore_info.get("output_tokens", 0) or 0)
        if fresh_score:
            sig["sources"] = fresh_score.get("sources") or []
            rescored_count += 1
            # backlog: citations-provenance-grounding. A second, non-forced
            # API call re-fetches this pick's own sources and asks Claude to
            # ground its reasoning in specific cited passages -- additive,
            # never touches sources/reasoning, and failure here must not
            # cost the pick its already-good sources from the block above.
            try:
                citations, cite_info = scorer.ground_citations(market, fresh_score, config)
                sig["citations"] = citations
                cost_usd += cite_info.get("cost_usd", 0) or 0
                tokens   += (cite_info.get("input_tokens", 0) or 0) + \
                            (cite_info.get("output_tokens", 0) or 0)
            except Exception as e:
                print(f"      Citations grounding FAILED for {sig.get('ticker', '?')} (non-fatal): {e}")

    return {
        "shortlist_size": len(shortlist),
        "rescored_count": rescored_count,
        "tokens":         tokens,
        "cost_usd":       cost_usd,
    }


def _extremize(p: float, alpha: float) -> float:
    """
    Satopää et al. (2014) extremizing transform.
    When multiple independent sources agree on a probability, the true
    probability is typically more extreme than a simple average suggests.
    alpha > 1 pushes the estimate toward 0 or 1; alpha = 1 is identity.
    """
    if not (0.001 < p < 0.999):
        return p
    return (p ** alpha) / (p ** alpha + (1.0 - p) ** alpha)


def _count_agreeing_signals(m: dict, direction: str) -> int:
    """
    Count independent signal sources aligned with the given direction.
    Used to determine the extremizing alpha factor.
    """
    count = 0
    if m.get("heuristic_direction") == direction:
        count += 1
    poly_gap = float((m.get("poly") or {}).get("price_gap") or 0)
    if direction == "YES" and poly_gap >= 0.05:
        count += 1
    elif direction == "NO" and poly_gap <= -0.05:
        count += 1
    cons = m.get("ext_consensus") or {}
    if abs(cons.get("consensus_gap", 0) or 0) >= 0.05 and cons.get("consensus_dir") == direction:
        count += 1
    wh = m.get("whale_data") or {}
    if wh.get("whale_detected") and wh.get("whale_direction") == direction:
        count += 1
    if m.get("ob_flag") and m.get("ob_direction") == direction:
        count += 1
    if m.get("watchlist_signal") and (m.get("watchlist_direction") or "").upper() == direction:
        count += 1
    return count


def _smart_money_majority_dir(smart_money: list) -> "str | None":
    """
    Majority direction among winning wallets tracked on this market, or None
    if there's no directional majority (tied or no directional entries).
    Flattened for persistence -- the raw smart_money list itself is never
    stored on the signals row, only this summary plus the count.
    """
    yes_n = sum(1 for s in smart_money if s.get("direction") == "YES")
    no_n  = sum(1 for s in smart_money if s.get("direction") == "NO")
    if yes_n == no_n:
        return None
    return "YES" if yes_n > no_n else "NO"


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

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
        "model_used":        (config.get("llm", {}).get("cli_model_override")
                               if config.get("llm", {}).get("backend", "cli") == "cli"
                               else None) or config.get("scoring", {}).get("scorer_model", "claude-sonnet-4-6"),
        "tokens_used":       0,
        "cost_usd":          0,
        "runtime_ms":        0,
    }

    all_markets       = []
    flagged_markets   = []
    unflagged_markets = []
    whale_results     = {}
    final_signals     = []
    whale_only        = []
    token_info        = {"input_tokens": 0, "output_tokens": 0}

    # Step 1 — Authenticate
    print("[1/8] Authenticating with Kalshi...")
    try:
        kalshi.authenticate(config)
        print("      OK")
    except Exception as e:
        print(f"      FAILED: {e}")
        print("      Cannot proceed without valid auth. Exiting.")
        # 2026-08-30: this early return used to be completely silent -- no
        # alert, unlike the shape-anomaly abort below. A real incident (a
        # transient Kalshi API failure during Task Scheduler's 7am run,
        # likely the SDK's own exponential-backoff retry exhausting itself
        # over ~22 minutes before finally raising) went unnoticed for 19
        # hours until heartbeat_check.py's own staleness alert caught it --
        # this run's actual error message was never seen by anyone, since
        # Task Scheduler's action has no output capture and this path sent
        # no email of its own. Matches the shape-anomaly alert's pattern.
        try:
            report.send_report(
                f"Kalshi authentication failed at the start of this run: {e}\n\n"
                "This run aborted before fetching any markets -- no scan happened. "
                "If this was a transient API/network issue it may already be resolved; "
                "if it recurs, check config.json's kalshi credentials and "
                "core.kalshi.authenticate()'s error for the real cause.",
                [], 0, config,
                subject_override="Leviathan ALERT — Kalshi auth failed, run aborted",
            )
        except Exception as _e:
            print(f"      [warn] Auth-failure alert email failed: {_e}")
        return

    # Resolve any prior calls that have since settled
    try:
        resolved = logger.resolve_outcomes(config)
        if resolved:
            print(f"      {resolved} prior call(s) resolved — track record updated")
    except Exception as e:
        print(f"      Outcome resolution failed: {e}")

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
            # series_ticker and category live on the EVENT object only (not
            # the raw market object) — capture them here and attach to each
            # market so they're available downstream, the same way
            # event_ticker is already carried through.
            series_ticker = event.get("series_ticker", "")
            category      = event.get("category", "")
            try:
                for m in kalshi.fetch_event_markets(config, event_ticker):
                    t = m.get("ticker")
                    if t and t not in seen:
                        seen.add(t)
                        m["series_ticker"] = series_ticker
                        m["category"]      = category
                        all_markets.append(m)
            except Exception as _e:
                print(f"      [warn] fetch_event_markets({event_ticker}): {_e}")
        print(f"      Fetched {len(all_markets)} markets from {len(events)} events")

        # Near-dated supplement (GOAL: expand scope so more data resolves
        # faster) -- the events-catalog loop above structurally never
        # surfaces near-dated markets (confirmed empirically: 0 of 2722
        # closed within 30 days, starving resolve_first.py's whole
        # mechanism). See fetch_near_dated_markets's docstring for the
        # full root-cause writeup. Merged into the same `seen`/all_markets
        # so downstream code (scanner, resolve_first via the snapshot)
        # sees one unified list, never a second population to reconcile.
        nd_cfg = config.get("markets", {})
        try:
            near_dated = kalshi.fetch_near_dated_markets(
                config,
                max_days=nd_cfg.get("near_dated_max_days", 14),
                target_count=nd_cfg.get("near_dated_target_count", 200),
                max_pages=nd_cfg.get("near_dated_max_pages", 30),
            )
            nd_added = 0
            for m in near_dated:
                t = m.get("ticker")
                if t and t not in seen:
                    seen.add(t)
                    all_markets.append(m)
                    nd_added += 1
            print(f"      Near-dated supplement: +{nd_added} markets (closing within "
                  f"{nd_cfg.get('near_dated_max_days', 14)}d, {len(near_dated)} fetched)")
        except Exception as _e:
            print(f"      [warn] fetch_near_dated_markets: {_e}")
    except Exception as e:
        print(f"      Events fetch failed ({e}), falling back to /markets...")
        try:
            all_markets = kalshi.fetch_markets(config)
            all_markets = kalshi.attach_event_category_metadata(config, all_markets)
            print(f"      Fetched {len(all_markets)} markets (fallback)")
        except Exception as e2:
            print(f"      FAILED: {e2}")
            traceback.print_exc()

    # Shape-anomaly check — abort before scoring garbage rather than let a
    # Kalshi API shape change flow silently through filter/score's own
    # `.get(x) or default` coercions (unattended-ops: graceful degradation).
    _shape = _validate_market_shape(all_markets)
    _shape_threshold = config.get("markets", {}).get("shape_anomaly_threshold", 0.5)
    _shape_min_sample = config.get("markets", {}).get("shape_anomaly_min_sample", 20)
    if _shape["checked"] >= _shape_min_sample and _shape["anomaly_rate"] > _shape_threshold:
        print(f"      [ALERT] {_shape['anomaly_rate']:.0%} of {_shape['checked']} fetched markets "
              f"missing expected fields {_shape['missing_fields']} — looks like a Kalshi API "
              f"shape change, not normal variance. Aborting before scoring.")
        try:
            report.send_report(
                f"{_shape['anomaly_rate']:.0%} of {_shape['checked']} markets fetched this run are "
                f"missing expected fields: {_shape['missing_fields']}.\n\n"
                "This run aborted before scoring to avoid processing garbage data as if it were "
                "real. See docs/RUNBOOK.md — 'API shape anomaly detected'.",
                [], 0, config,
                subject_override="Leviathan ALERT — Kalshi API shape anomaly, run aborted",
            )
        except Exception as _e:
            print(f"      [warn] Shape-anomaly alert email failed: {_e}")
        return

    # Save fresh snapshot for smart money cross-reference and analysis scripts
    if all_markets:
        try:
            from analysis.snapshot_markets import save_snapshot
            save_snapshot(all_markets, 0, config)
        except Exception as _e:
            print(f"      [warn] Snapshot save failed: {_e}")

    # Step 3 — Filter and score for mispricing
    print("[3/8] Scanning for mispriced markets...")
    try:
        filtered = scanner.filter_markets(all_markets, config)

        # Load smart money signals cache to boost priority for matched markets
        try:
            _sig_cache = os.path.join(os.path.dirname(__file__),
                                      "data", "smart_money", "latest_signals.json")
            if os.path.exists(_sig_cache):
                import json as _json
                _sig_data        = _json.load(open(_sig_cache, encoding="utf-8"))
                _sm_tickers      = set(_sig_data.get("kalshi_tickers", []))
                _ticker_details  = _sig_data.get("ticker_details", {})
                # Staleness check: positions from >24h ago may have changed
                _run_at_str = _sig_data.get("run_at", "")
                _stale = False
                if _run_at_str:
                    try:
                        from datetime import timezone as _tz
                        _run_at = datetime.fromisoformat(_run_at_str.replace("Z", "+00:00"))
                        _hours_old = (datetime.now(_tz.utc) - _run_at).total_seconds() / 3600
                        _stale = _hours_old > 24
                    except Exception:
                        pass
                scanner.tag_watchlist_overlap(filtered, _sm_tickers, _ticker_details,
                                              stale=_stale)
                n_boost = sum(1 for m in filtered if m.get("watchlist_signal"))
                if n_boost:
                    _stale_note = " [STALE >24h]" if _stale else ""
                    print(f"      Smart money boost: {n_boost} market(s) matched watchlist tickers "
                          f"(from {_sig_data.get('signal_count', 0)} X-refs, "
                          f"run {_sig_data.get('run_at', '')[:10]}{_stale_note})")
        except Exception as _e:
            print(f"      [warn] Smart money tag failed: {_e}")

        scored_markets, hp_filtered = scanner.score_markets(filtered, config)
        run_meta["high_price_filtered"] = hp_filtered

        # Post-scoring event dedup: picks the best-signal market per event
        # (watchlist > net_edge > raw_edge > volume) instead of raw volume.
        # Running after score_markets() means we use realizable edge, not just liquidity.
        if config.get("markets", {}).get("dedup_by_event", False):
            before_dedup = len(scored_markets)
            scored_markets = scanner.dedup_by_event_scored(scored_markets)
            print(f"      Dedup: {before_dedup} -> {len(scored_markets)} markets "
                  f"(kept best-signal market per event)")
        # Include flagged markets + any watchlist-tagged markets not already flagged
        flagged_markets = [m for m in scored_markets if m.get("flag")]
        wl_unflagged    = [m for m in scored_markets if m.get("watchlist_signal") and not m.get("flag")]
        if wl_unflagged:
            for m in wl_unflagged:
                m["flag"]      = True
                m["flag_path"] = "WATCHLIST"
            flagged_markets = wl_unflagged + flagged_markets  # watchlist first
            print(f"      Watchlist override: {len(wl_unflagged)} market(s) force-flagged by smart money signal")
        print(f"      {len(filtered)} markets passed filter (from {len(all_markets)})")
        print(f"      {len(flagged_markets)} markets flagged for edge")
        # Collect unflagged pool for cross-market promotion in step 4
        unflagged_markets = [m for m in scored_markets if not m.get("flag")]
    except Exception as e:
        print(f"      FAILED: {e}")
        traceback.print_exc()

    run_meta["markets_scanned"] = len(all_markets)

    # Step 4 — Polymarket cross-reference
    print("[4/8] Cross-referencing with Polymarket...")
    poly_data = {}
    try:
        if config.get("polymarket", {}).get("enabled", True):
            poly_index = polymarket.fetch_and_build_index(config)

            if flagged_markets:
                poly_data = polymarket.match_markets(flagged_markets, poly_index, config)
                matched   = sum(1 for v in poly_data.values() if v.get("price_gap") is not None)
                gaps      = [v for v in poly_data.values() if v.get("price_gap") and abs(v["price_gap"]) >= 0.05]
                print(f"      {matched} Polymarket matches found, {len(gaps)} with gap ≥5%")

            # Cross-market promotion: unflagged markets with large Polymarket divergence
            poly_cfg    = config.get("polymarket", {})
            cross_on         = poly_cfg.get("cross_market_promote", True)
            cross_gap        = poly_cfg.get("cross_market_min_gap", 0.15)
            cross_min_score  = poly_cfg.get("cross_market_min_match_score", 0.65)
            cross_max        = poly_cfg.get("cross_market_max_candidates", 50)
            if cross_on and unflagged_markets and poly_index:
                candidates = sorted(
                    unflagged_markets,
                    key=lambda m: -float(m.get("volume_fp") or m.get("volume") or 0),
                )[:cross_max]
                cross_matches = polymarket.match_markets(
                    candidates, poly_index, config,
                    min_gap=cross_gap, min_match_score=cross_min_score,
                )
                n_promoted = 0
                for m in candidates:
                    ticker = m.get("ticker", "")
                    if ticker in cross_matches:
                        cd             = cross_matches[ticker]
                        m["flag"]      = True
                        m["flag_path"] = "CROSS_MARKET"
                        m["poly"]      = cd
                        # Poly-based net_edge for CROSS_MARKET signals:
                        # abs(Poly-Kalshi gap) minus half bid-ask spread.
                        # raw_edge is None here (no base_rate), so use Poly gap as proxy.
                        _poly_gap = abs(cd.get("price_gap") or 0)
                        _bid = float(m.get("yes_bid_dollars") or 0)
                        _ask = float(m.get("yes_ask_dollars") or 0)
                        if _poly_gap > 0 and _bid > 0 and _ask > 0:
                            m["net_edge"] = round(_poly_gap - (_ask - _bid) / 2, 6)
                        flagged_markets.append(m)
                        poly_data[ticker] = cd
                        n_promoted += 1
                if n_promoted:
                    print(f"      Cross-market: {n_promoted} unflagged market(s) promoted "
                          f"(Polymarket gap ≥{cross_gap:.0%})")
        else:
            print("      Skipped (disabled)")
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

    # Step 5 — Whale detection + order book depth + price history
    print("[5/8] Checking whale activity and order book depth...")
    try:
        trades_by_ticker    = {}
        orderbook_by_ticker = {}
        history_by_ticker   = {}

        _period_map = {
            "INTRADAY":  86400,
            "WEEKLY":    86400 * 3,
            "MONTHLY":   86400 * 7,
            "QUARTERLY": 86400 * 14,
            "LONG":      86400 * 30,
        }
        _period_label = {
            "INTRADAY": "24h", "WEEKLY": "3d", "MONTHLY": "7d",
            "QUARTERLY": "14d", "LONG": "30d",
        }

        for m in flagged_markets:
            ticker = m.get("ticker")
            if not ticker:
                continue
            try:
                trades_by_ticker[ticker] = kalshi.fetch_trades(config, ticker)
            except Exception:
                trades_by_ticker[ticker] = []
            try:
                ob = kalshi.fetch_orderbook(config, ticker)
                orderbook_by_ticker[ticker] = scanner.compute_orderbook_signal(ob)
            except Exception:
                orderbook_by_ticker[ticker] = {}
            try:
                horizon       = m.get("time_horizon", "MONTHLY")
                period        = _period_map.get(horizon, 86400 * 7)
                label         = _period_label.get(horizon, "7d")
                series_ticker = m.get("series_ticker")
                end_ts        = int(time.time())
                start_ts      = end_ts - period
                # Hourly candles for the 1-day INTRADAY lookback (enough points to
                # show a trend); daily candles for every longer horizon.
                period_interval = 60 if horizon == "INTRADAY" else 1440
                hist = (
                    kalshi.fetch_market_candlesticks(
                        config, series_ticker, ticker, start_ts, end_ts, period_interval,
                    )
                    if series_ticker else []
                )
                hist.sort(key=lambda c: c.get("end_period_ts", 0))
                prices = [
                    float(c["price"]["close_dollars"])
                    for c in hist
                    if c.get("price", {}).get("close_dollars")
                ]
                if len(prices) >= 2:
                    start  = prices[0]
                    end    = prices[-1]
                    change = (end - start) * 100
                    trend  = "rising" if change > 1 else ("declining" if change < -1 else "stable")
                    history_by_ticker[ticker] = f"{change:+.1f}% ({start*100:.1f}% → {end*100:.1f}%) — {trend} ({label})"
            except Exception as _e:
                print(f"      [warn] price history({ticker}): {_e}")

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
        m["whale_data"]      = whale
        m["whale_reversal"]  = scanner.compute_whale_reversal(m, whale)
        m["price_trend"]     = history_by_ticker.get(ticker)
        m.update(orderbook_by_ticker.get(ticker) or {})
        if (m["whale_reversal"] or m.get("ob_flag")) and not m.get("flag"):
            m["flag"] = True

    # Whale persistence streak — track same-direction whale activity across daily scans.
    # A whale buying YES for 3 consecutive scans is qualitatively stronger than a one-off.
    whale_streak_data = {}
    try:
        whale_streak_data = whales.load_whale_streak()
        whale_streak_data = whales.update_whale_streak(
            whale_results, whale_streak_data, datetime.now(timezone.utc).isoformat()
        )
        whales.save_whale_streak(whale_streak_data)
        n_streaks = sum(1 for v in whale_streak_data.values() if v.get("streak", 0) >= 2)
        if n_streaks:
            print(f"      Whale streak: {n_streaks} ticker(s) with 2+ consecutive directional scan(s)")
    except Exception as _e:
        print(f"      [warn] Whale streak update failed: {_e}")

    for m in flagged_markets:
        ticker = m.get("ticker", "")
        streak = whale_streak_data.get(ticker, {})
        current_dir = (whale_results.get(ticker) or {}).get("whale_direction")
        m["whale_streak"] = (
            streak["streak"]
            if streak.get("direction") == current_dir and streak.get("streak", 0) >= 2
            else 0
        )

    # PASS history: markets Claude has repeatedly declined get deprioritized.
    # Single batch lookup to avoid N DB queries.
    _pass_tickers = {}
    try:
        _pass_tickers = logger.get_pass_tickers(days=14)
    except Exception:
        pass
    for m in flagged_markets:
        m["pass_count"] = _pass_tickers.get(m.get("ticker", ""), 0)

    # Hard PASS suppression: markets that Claude has PASS'd many times in the
    # look-back window are systematic scanner false-positives. Remove them from
    # the queue entirely — deprioritization is not enough at that point.
    _max_suppress = config.get("scoring", {}).get("max_pass_before_suppress", 5)
    if _max_suppress > 0:
        _before_sup = len(flagged_markets)
        flagged_markets = [m for m in flagged_markets if m.get("pass_count", 0) < _max_suppress]
        _n_sup = _before_sup - len(flagged_markets)
        if _n_sup:
            print(f"      PASS suppress: {_n_sup} market(s) hard-suppressed "
                  f"({_max_suppress}+ PASSes in 14d — systematic false-positives)")

    # Signal persistence: query DB for prior paper signals on these tickers.
    # Runs in one batch query. Gives Claude longitudinal context: "flagged 4 days
    # in a row, all YES" is much stronger than a single-day appearance.
    _history_batch = logger.get_signal_history_batch(
        [m.get("ticker", "") for m in flagged_markets], days=14
    )
    for m in flagged_markets:
        ticker = m.get("ticker", "")
        hist   = _history_batch.get(ticker, [])
        days_seen = len({h["timestamp"][:10] for h in hist})
        m["prior_appearances"] = days_seen
        dirs = [h["direction"] for h in hist if h.get("direction") in ("YES", "NO")]
        m["prior_yes"] = sum(1 for d in dirs if d == "YES")
        m["prior_no"]  = len(dirs) - m["prior_yes"]
        m["direction_consistent"] = (
            (m["prior_yes"] == 0 or m["prior_no"] == 0) and bool(dirs)
        )
        # Oldest entry in DESC list = first time we flagged this ticker
        m["first_flagged_price"] = hist[-1]["market_price"] if hist else None

    def _pre_sort_score(m: dict) -> int:
        """Pre-Claude signal quality score: higher = more evidence of real edge."""
        sc = 0
        fp = m.get("flag_path")
        if m.get("watchlist_signal"):
            sc += 5 if m.get("watchlist_stale") else 10   # stale >24h = half weight
        if (m.get("whale_data") or {}).get("whale_detected"): sc += 3
        if m.get("whale_reversal"):                           sc += 2
        if fp in ("HEURISTIC", "EDGE"):                       sc += 2
        if fp == "CROSS_MARKET":                              sc += 2
        if m.get("drift_flag"):                               sc += 1
        if m.get("ob_flag"):                                  sc += 1
        if m.get("spread_wide"):                              sc += 1
        poly = m.get("poly") or {}
        if poly.get("price_gap") is not None and abs(poly["price_gap"]) >= 0.10: sc += 2
        ext  = m.get("ext_markets") or []
        if any(abs(e.get("price_gap", 0)) >= 0.05 for e in ext): sc += 1

        # Signal persistence: consecutive multi-day appearance with consistent direction
        # is strong evidence of a structural mispricing, not transient noise.
        pa         = m.get("prior_appearances", 0)
        consistent = m.get("direction_consistent")
        if pa >= 3 and consistent:   sc += 3
        elif pa >= 2 and consistent: sc += 2
        elif pa >= 2:                sc += 1

        # Recent activity signals: evidence of fresh information flowing in.
        vol_total = float(m.get("volume_fp") or m.get("volume") or 0)
        vol_24h   = float(m.get("volume_24h_fp") or 0)
        if vol_total > 0 and vol_24h > 0 and (vol_24h / vol_total) >= 0.20:
            sc += 1  # volume spike — recent elevated trading activity
        prev_p = float(m.get("previous_price_dollars") or 0)
        last_p = float(m.get("last_price_dollars") or 0)
        if prev_p > 0 and last_p > 0 and abs((last_p - prev_p) / prev_p) >= 0.20:
            sc += 2  # price jump — ≥20% move signals a specific catalyst

        # Signal convergence: count directional sources that agree.
        # Multi-platform external consensus: weight by platform count (cap 3 votes).
        # Strong convergence (3+ sources) = much more likely a real mispricing.
        _yes = _no = 0
        hd = m.get("heuristic_direction")
        if hd == "YES": _yes += 1
        elif hd == "NO": _no += 1
        pg = (poly.get("price_gap") or 0)
        if abs(pg) >= 0.05:
            if pg > 0: _yes += 1
            else:      _no  += 1
        cons = (m.get("ext_consensus") or {})
        if abs(cons.get("consensus_gap", 0) or 0) >= 0.05:
            cd     = cons.get("consensus_dir")
            # Number of platforms agreeing — cap at 3 to bound total contribution
            n_plat = min(3, (cons.get("sources_higher", 0) if cd == "YES"
                             else cons.get("sources_lower", 0)))
            if cd == "YES":   _yes += n_plat
            elif cd == "NO":  _no  += n_plat
        drift_pct = m.get("price_drift") or 0
        if m.get("drift_flag"):
            if drift_pct < 0: _yes += 1
            else:             _no  += 1
        wh = (m.get("whale_data") or {})
        wd = wh.get("whale_direction")
        if wh.get("whale_detected") and wd == "YES": _yes += 1
        elif wh.get("whale_detected") and wd == "NO": _no += 1
        if m.get("ob_direction") == "YES" and m.get("ob_flag"): _yes += 1
        elif m.get("ob_direction") == "NO"  and m.get("ob_flag"): _no  += 1
        if m.get("watchlist_signal"):
            wl_dir = (m.get("watchlist_direction") or "").upper()
            if wl_dir == "YES":   _yes += 1
            elif wl_dir == "NO":  _no  += 1

        convergence = max(_yes, _no)
        if convergence >= 3: sc += 3   # strong multi-source convergence
        elif convergence == 2: sc += 1  # moderate convergence

        # Conflict penalty: when heuristic and Polymarket strongly disagree (≥10pp),
        # demote the signal — Polymarket has real money and current information.
        # Only apply penalty when there's no third-party corroboration (watchlist/whale).
        hd = m.get("heuristic_direction")
        if (hd in ("YES", "NO") and abs(pg) >= 0.10 and
                ((hd == "YES" and pg < 0) or (hd == "NO" and pg > 0))):
            has_corroboration = (
                m.get("watchlist_signal") or
                (m.get("whale_data") or {}).get("whale_detected")
            )
            if not has_corroboration:
                sc -= 2  # counter-evidence from a liquid market weakens the signal

        # Whale persistence: same-direction whale across consecutive daily scans.
        # Multi-day persistence is the Kyle (1985) informed-trader signature.
        ws = m.get("whale_streak", 0)
        if ws >= 3:   sc += 3
        elif ws >= 2: sc += 1

        # Net edge: realizable edge after bid-ask spread. Positive = tradeable.
        # Markets with net_edge ≤ 0 would be underwater on entry — deprioritise
        # them so Claude slots go to actually tradeable opportunities first.
        ne = m.get("net_edge")
        if ne is not None:
            if ne > 0.10:   sc += 3   # wide, tradeable edge
            elif ne > 0.05: sc += 2   # solid post-spread edge
            elif ne > 0:    sc += 1   # marginally tradeable
            else:           sc -= 4   # spread consumes edge entirely

        # Short-horizon penalty: if weak signal AND closes within 7 days,
        # deprioritise relative to long-horizon markets with more evidence.
        if m.get("short_horizon") and sc < 5:
            sc -= 2

        # PASS penalty: markets Claude has repeatedly declined are likely
        # scanner false-positives — move them toward the back of the queue.
        pc = m.get("pass_count", 0)
        if pc >= 3:   sc -= 3
        elif pc >= 2: sc -= 1

        return sc

    # Sort: time-sensitive first (INTRADAY before LONG), then by pre-signal quality,
    # then by volume within each bucket — maximises evidence in the top-N Claude slots
    flagged_markets.sort(
        key=lambda m: (
            scanner.BUCKET_PRIORITY.get(m.get("time_horizon", "MONTHLY"), 2),
            -_pre_sort_score(m),
            -float(m.get("volume_fp") or m.get("volume") or 0),
        )
    )

    # backlog: rolled-market-repeat-detection. Attaches each flagged
    # market's repeat-family history (same real-world story, previously
    # flagged under a different rolling-window ticker -- see
    # logger.get_repeat_family()) so build_prompt() can surface it as
    # context. Deliberately visibility only, no automatic confidence
    # penalty -- see get_repeat_family()'s own docstring for why.
    for m in flagged_markets:
        try:
            m["repeat_family"] = logger.get_repeat_family(m.get("ticker", ""))
        except Exception:
            m["repeat_family"] = []

    # Step 6 — Score with Claude + web search
    print("[6/8] Scoring with Claude...")

    claude_scores = []
    # Set only inside the except below -- that branch is only reachable when
    # flagged_markets was non-empty and scorer.score_markets() raised (e.g.
    # the CLI backend exhausted its usage limit and every retry in
    # scorer.py's own backoff loop still failed), never for a legitimately
    # quiet day with nothing flagged. Drives the sys.exit(1) at the very end
    # of main() -- everything else in this function still runs and still
    # reports/logs normally; this only changes the process's final exit
    # code so Task Scheduler's RestartCount on Leviathan-DailyRun (see
    # schedule_setup.ps1) has something real to catch and retry.
    scoring_hard_failed = False
    try:
        if flagged_markets:
            # Pass historical calibration so Claude can self-correct if overconfident
            try:
                _cal      = logger.get_stats_by_confidence()
                _flag_cal = logger.get_stats_by_flag_path()
            except Exception:
                _cal      = None
                _flag_cal = None
            claude_scores, token_info = scorer.score_markets(
                flagged_markets, config,
                calibration=_cal, flag_cal=_flag_cal,
            )
            print(f"      Scored {len(claude_scores)} markets via claude CLI")
        else:
            print("      No flagged markets to score.")
    except Exception as e:
        print(f"      FAILED: {e}")
        traceback.print_exc()
        scoring_hard_failed = True

    cost = estimate_cost(token_info, run_meta["model_used"])
    run_meta["tokens_used"] = token_info.get("input_tokens", 0) + token_info.get("output_tokens", 0)
    run_meta["cost_usd"]    = cost

    # Merge Claude scores with full market context (including upstream signals)
    scored_by_ticker    = {s["ticker"]: s for s in claude_scores}
    conf_threshold_rank = {"HIGH": 0, "MED": 1, "LOW": 2}
    threshold_rank      = conf_threshold_rank.get(
        config.get("scoring", {}).get("confidence_threshold", "MED"), 1
    )
    min_high_edge       = float(config.get("scoring", {}).get("min_high_confidence_edge", 0.10))

    for m in flagged_markets:
        ticker = m.get("ticker", "")
        cs     = scored_by_ticker.get(ticker)
        if not cs:
            continue

        whale  = whale_results.get(ticker, {})
        signal = {
            **cs,
            "title":           m.get("title", cs.get("title", "")),
            "event_ticker":    m.get("event_ticker", ""),
            "series_ticker":   m.get("series_ticker", ""),
            "category":        m.get("category", ""),
            "whale_detected":  whale.get("whale_detected", False),
            "whale_direction": whale.get("whale_direction"),
            "whale_max_trade_size": whale.get("max_trade_size"),
            "whale_reversal":  m.get("whale_reversal", False),
            "drift_flag":      m.get("drift_flag", False),
            "price_drift":     m.get("price_drift"),
            "spread_wide":     m.get("spread_wide", False),
            "spread_pct":      m.get("spread_pct"),
            # Already fetched onto `m` for filtering/scoring (core.scanner,
            # core.scorer both read these) but never persisted before --
            # without this, "did edge cluster in illiquid markets" can
            # never be answered from historical data.
            "volume":          m.get("volume_fp") or m.get("volume"),
            "open_interest":   m.get("open_interest_fp") or m.get("open_interest"),
            "ob_flag":         m.get("ob_flag", False),
            "ob_imbalance":    m.get("ob_imbalance"),
            "ob_direction":    m.get("ob_direction"),
            "ob_bid_depth":    m.get("ob_bid_depth"),
            "ob_ask_depth":    m.get("ob_ask_depth"),
            "time_horizon":    m.get("time_horizon", "MONTHLY"),
            "poly":            m.get("poly"),
            "ext_markets":     m.get("ext_markets", []),
            "ext_consensus":   m.get("ext_consensus", {}),
            "smart_money":     m.get("smart_money", []),
            # Flattened for persistence -- the raw poly/ext_consensus/
            # smart_money structures above are used for the prompt/report
            # only and are never stored on the signals row.
            "poly_price":        (m.get("poly") or {}).get("poly_price"),
            "poly_price_gap":    (m.get("poly") or {}).get("price_gap"),
            "consensus_gap":     (m.get("ext_consensus") or {}).get("consensus_gap"),
            "consensus_dir":     (m.get("ext_consensus") or {}).get("consensus_dir"),
            "smart_money_count": len(m.get("smart_money") or []),
            "smart_money_dir":   _smart_money_majority_dir(m.get("smart_money") or []),
            "watchlist_signal": m.get("watchlist_signal", False),
            "flag_path":            m.get("flag_path"),
            "base_rate":            m.get("base_rate"),
            # db-audit-2026-08: computed in the same score_market() call as
            # base_rate/flag_path above but never copied into the logged
            # signal -- heuristic_label was persisted for 2 of 271 rows
            # ever, though ~218 rows have a base_rate.
            "heuristic_label":      m.get("heuristic_label"),
            "net_edge":             m.get("net_edge"),
            "heuristic_direction":  m.get("heuristic_direction"),
            "short_horizon":        m.get("short_horizon", False),
            "sig_edge":             m.get("sig_edge", False),
            "sig_drift":            m.get("sig_drift", False),
            "sig_br_none":          m.get("sig_br_none", False),
            "close_time":           m.get("close_time") or m.get("expiration_time"),
            "run_id":               run_id,
            "net_edge_after_fee":   m.get("net_edge_after_fee"),
        }

        # HIGH confidence gate: downgrade to MED if edge is below minimum threshold.
        # Claude rule 11 already instructs ≥10pp, but this enforces it programmatically.
        if signal.get("confidence") == "HIGH" and abs(float(signal.get("edge") or 0)) < min_high_edge:
            signal["confidence"] = "MED"
            signal["confidence_downgraded"] = True

        # Short-horizon HIGH confidence gate: downgrade to MED when market closes within 7 days
        # and no strong corroborating signal exists (no whale, no watchlist, no Polymarket divergence).
        # Heuristic base rates are long-run averages that are unreliable over 1-7 day windows.
        if signal.get("short_horizon") and signal.get("confidence") == "HIGH":
            _has_corroboration = (
                signal.get("watchlist_signal") or
                (signal.get("whale_data") or signal.get("whale_detected")) or
                abs(((signal.get("poly") or {}).get("price_gap") or 0)) >= 0.10
            )
            if not _has_corroboration:
                signal["confidence"] = "MED"
                signal["confidence_downgraded"] = True

        # Fee-adjusted EV per unit — computed here since direction is now known from Claude.
        _dir_ev  = signal.get("direction", "PASS")
        _mp_ev   = float(signal.get("market_price") or 0)
        _est_ev  = signal.get("our_estimate")
        _unit    = config.get("betting", {}).get("unit_size", 10)
        if _dir_ev in ("YES", "NO") and _mp_ev > 0 and _est_ev is not None:
            _fee = kalshi_fee(_mp_ev, _unit)
            _ev_ff = (float(_est_ev) - _mp_ev) * _unit if _dir_ev == "YES" else (_mp_ev - float(_est_ev)) * _unit
            signal["ev_after_fee_per_contract"] = round(_ev_ff - _fee, 4)

        # backlog: net-edge-fee-depth-model. net_edge_after_fee prices the
        # trade off the top-of-book quote only -- it has no idea whether the
        # book can actually fill unit_size contracts on the side this
        # direction needs. Downgrade confidence (same mechanism as the
        # short-horizon corroboration gate above) rather than trusting the
        # face-value edge when the book is too thin to support the stake.
        _liq = scanner.check_liquidity(_dir_ev, signal.get("ob_bid_depth"), signal.get("ob_ask_depth"), _unit)
        signal["liquidity_checked"] = _liq["liquidity_checked"]
        signal["liquidity_thin"]    = _liq["liquidity_thin"]
        if _liq["liquidity_thin"] and signal.get("confidence") in ("HIGH", "MED"):
            signal["confidence"] = "MED" if signal["confidence"] == "HIGH" else "LOW"
            signal["confidence_downgraded"] = True

        # Extremizing: when ≥2 independent sources agree with Claude's direction,
        # the true probability is more extreme than any single estimate suggests.
        # (Satopää et al. 2014; Good Judgment Project validated this empirically.)
        _dir = signal.get("direction", "PASS")
        _est = float(signal.get("our_estimate") or 0)
        if _dir in ("YES", "NO"):
            # backlog: confluence-detection. Recorded unconditionally (unlike
            # ext_n_signals below, which is only set once the extremizing
            # transform's own >=2 threshold fires) so get_stats_by_confluence()
            # can compare 0 vs 1 vs 2+ agreeing sources against real outcomes,
            # not just the subset that already cleared a different threshold.
            signal["confluence_count"] = _count_agreeing_signals(m, _dir)
        if _dir in ("YES", "NO") and 0.05 < _est < 0.95:
            _n = signal.get("confluence_count", _count_agreeing_signals(m, _dir))
            _alpha = 1.30 if _n >= 3 else (1.15 if _n >= 2 else 1.0)
            if _alpha > 1.0:
                _ext = _extremize(_est, _alpha)
                signal["ext_estimate"]  = round(_ext, 4)
                signal["ext_edge"]      = round(_ext - float(signal.get("market_price") or 0), 4)
                signal["ext_n_signals"] = _n
                signal["ext_alpha"]     = _alpha

        if conf_threshold_rank.get(signal.get("confidence", "LOW"), 2) <= threshold_rank and signal.get("direction", "PASS") != "PASS":
            final_signals.append(signal)
        else:
            # Log PASS decisions for scanner calibration (identify systematic false-positives)
            if signal.get("direction", "PASS") == "PASS":
                signal["leviathan_score"] = report.compute_leviathan_score(signal)
                logger.log_pass(signal)
            if whale.get("whale_detected"):
                whale_only.append({**whale, "title": m.get("title", "")})

    # Second pass: if nothing cleared the threshold, include LOW confidence rather than returning empty
    # Guard: require minimum market price to avoid acting on tail-probability markets
    _min_price_2nd = config.get("markets", {}).get("min_market_price", 0.05)
    if not final_signals and claude_scores:
        print("      No MED/HIGH signals — widening to LOW confidence for second pass...")
        for m in flagged_markets:
            ticker = m.get("ticker", "")
            cs     = scored_by_ticker.get(ticker)
            if not cs or cs.get("direction", "PASS") == "PASS":
                continue
            # Skip sub-threshold market prices in second pass — crowd is almost always right
            mkt_p = float(cs.get("market_price") or m.get("mid_price") or 0)
            if mkt_p > 0 and not (_min_price_2nd <= mkt_p <= (1 - _min_price_2nd)):
                continue
            if cs.get("confidence") == "LOW":
                whale  = whale_results.get(ticker, {})
                signal = {
                    **cs,
                    "title":           m.get("title", cs.get("title", "")),
                    "event_ticker":    m.get("event_ticker", ""),
                    "series_ticker":   m.get("series_ticker", ""),
                    "category":        m.get("category", ""),
                    "whale_detected":  whale.get("whale_detected", False),
                    "whale_direction": whale.get("whale_direction"),
                    "whale_reversal":  m.get("whale_reversal", False),
                    "drift_flag":      m.get("drift_flag", False),
                    "price_drift":     m.get("price_drift"),
                    "spread_wide":     m.get("spread_wide", False),
                    "spread_pct":      m.get("spread_pct"),
                    "volume":          m.get("volume_fp") or m.get("volume"),
                    "open_interest":   m.get("open_interest_fp") or m.get("open_interest"),
                    "ob_flag":         m.get("ob_flag", False),
                    "ob_imbalance":    m.get("ob_imbalance"),
                    "ob_direction":    m.get("ob_direction"),
                    "ob_bid_depth":    m.get("ob_bid_depth"),
                    "ob_ask_depth":    m.get("ob_ask_depth"),
                    "time_horizon":    m.get("time_horizon", "MONTHLY"),
                    "poly":            m.get("poly"),
                    "ext_markets":     m.get("ext_markets", []),
                    "ext_consensus":   m.get("ext_consensus", {}),
                    "smart_money":     m.get("smart_money", []),
                    "poly_price":        (m.get("poly") or {}).get("poly_price"),
                    "poly_price_gap":    (m.get("poly") or {}).get("price_gap"),
                    "consensus_gap":     (m.get("ext_consensus") or {}).get("consensus_gap"),
                    "consensus_dir":     (m.get("ext_consensus") or {}).get("consensus_dir"),
                    "smart_money_count": len(m.get("smart_money") or []),
                    "smart_money_dir":   _smart_money_majority_dir(m.get("smart_money") or []),
                    "watchlist_signal":     m.get("watchlist_signal", False),
                    "flag_path":            m.get("flag_path"),
                    "base_rate":            m.get("base_rate"),
                    "heuristic_label":      m.get("heuristic_label"),
                    "net_edge":             m.get("net_edge"),
                    "heuristic_direction":  m.get("heuristic_direction"),
                    "short_horizon":        m.get("short_horizon", False),
                    "sig_edge":             m.get("sig_edge", False),
                    "sig_drift":            m.get("sig_drift", False),
                    "sig_br_none":          m.get("sig_br_none", False),
                    "close_time":           m.get("close_time") or m.get("expiration_time"),
                    "run_id":               run_id,
                    "second_pass":          True,
                }
                _liq2 = scanner.check_liquidity(
                    signal.get("direction", "PASS"), signal.get("ob_bid_depth"),
                    signal.get("ob_ask_depth"), config.get("betting", {}).get("unit_size", 10),
                )
                signal["liquidity_checked"] = _liq2["liquidity_checked"]
                signal["liquidity_thin"]    = _liq2["liquidity_thin"]
                final_signals.append(signal)
        if final_signals:
            print(f"      Second pass found {len(final_signals)} LOW confidence signal(s)")

    run_meta["signals_generated"] = len(final_signals)

    # Tag signals as new or repeat (seen within the repeat-dedup window);
    # annotate repeat count. Widened 7 -> config-driven (default 30) days
    # 2026-08-20 -- a 7-day window let the same still-open market re-flag
    # with a genuinely different direction just outside the window
    # (KXMLBDEBUT-KANDERSON-26NOV01: NO then YES, 8 days apart), which
    # logs as two independent rows that will resolve as one win and one
    # loss for the same real-world event by construction -- inflating
    # resolved_count (every locked backlog item's gate) with non-
    # independent samples. See config.example.json's repeat_dedup_days note.
    repeat_dedup_days = int(config.get("scoring", {}).get("repeat_dedup_days", 30))
    recent_tickers = logger.get_recent_tickers(days=repeat_dedup_days)
    for sig in final_signals:
        ticker = sig.get("ticker", "")
        sig["is_repeat"] = ticker in recent_tickers
        if sig["is_repeat"]:
            sig["repeat_count"] = logger.get_ticker_day_count(ticker, days=14)
    new_signals    = [s for s in final_signals if not s.get("is_repeat")]
    repeat_signals = [s for s in final_signals if s.get("is_repeat")]

    # Re-score the published shortlist individually so their `sources` are
    # genuinely per-pick, not batch-shared (GOAL_phase2-6_decisions.md
    # Decision 1) -- see _rescore_shortlist_for_clean_sources's docstring.
    # Scoped to new_signals only: repeat signals never get logger.log_signal()
    # called on them (dedup design, see Step 7 below), so re-scoring a repeat
    # would spend a real API call on a `sources` update that's discarded at
    # the end of this run instead of persisted.
    if config.get("llm", {}).get("backend") == "api" and new_signals:
        try:
            rescore_result = _rescore_shortlist_for_clean_sources(
                new_signals, flagged_markets, config, calibration=_cal, flag_cal=_flag_cal,
            )
            if rescore_result["shortlist_size"]:
                print(f"      Re-scored {rescore_result['rescored_count']}/{rescore_result['shortlist_size']} "
                      f"shortlisted pick(s) individually for clean source attribution")
            run_meta["tokens_used"] = run_meta.get("tokens_used", 0) + rescore_result["tokens"]
            run_meta["cost_usd"]    = run_meta.get("cost_usd", 0) + rescore_result["cost_usd"]
        except Exception as e:
            print(f"      Shortlist re-score FAILED (non-fatal, keeping batch-shared sources): {e}")

    # Step 7 — Log signals
    print("[7/8] Logging signals...")
    try:
        for sig in new_signals:  # only log new signals to avoid duplicate rows
            sig["leviathan_score"] = report.compute_leviathan_score(sig)
            logger.log_signal(sig)
        run_meta["runtime_ms"] = int((time.time() - start_time) * 1000)
        # backlog: brier-tracking. Snapshot the current cumulative Brier
        # scores onto this run row so calibration quality can be tracked
        # across runs, not just read as a single current-moment aggregate.
        _brier      = logger.get_brier_score()
        _brier_mkt  = logger.get_market_baseline_brier_score()
        run_meta["brier_scorer"] = _brier.get("brier_score")
        run_meta["brier_market"] = _brier_mkt.get("brier_score")
        run_meta["brier_n"]      = _brier.get("n")
        logger.log_run(run_meta)
        print(f"      Logged {len(new_signals)} new, {len(repeat_signals)} repeat signal(s)")
    except Exception as e:
        print(f"      FAILED: {e}")
        traceback.print_exc()

    # Price-blind shadow scoring (backlog: price-blind-arm) -- runs after
    # signal selection above is already final; results are logged to their
    # own DB table (core.logger.log_blind_score) and never read back into
    # scored_by_ticker/final_signals. Off by default (config.blind_arm.enabled)
    # since, unlike the main scan (Pro subscription, no per-token bill), this
    # goes through the metered Anthropic API on every run it fires.
    if config.get("blind_arm", {}).get("enabled", False) and claude_scores:
        try:
            from core import blind_scorer
            sample_size = int(config.get("blind_arm", {}).get("sample_size", 3))
            sampled = _sample_for_blind_arm(flagged_markets, scored_by_ticker, sample_size, run_id=run_id)
            if sampled:
                blind_results, blind_token_info = blind_scorer.score_blind(sampled, config)
                for br in blind_results:
                    ticker = br.get("ticker", "")
                    cs = scored_by_ticker.get(ticker, {})
                    logger.log_blind_score({
                        "run_id":                run_id,
                        "ticker":                ticker,
                        "title":                 cs.get("title", ""),
                        "estimate":              br.get("estimate"),
                        "confidence":            br.get("confidence"),
                        "reasoning":             br.get("reasoning"),
                        "sources_checked":       br.get("sources_checked"),
                        "market_price_at_score": cs.get("market_price"),
                        "cost_usd":              blind_token_info.get("cost_usd"),
                    })
                print(f"      Blind-arm: scored {len(blind_results)} market(s), "
                      f"cost {_fmt_usd(blind_token_info.get('cost_usd', 0))}")
        except Exception as e:
            print(f"      Blind-arm FAILED (non-fatal): {e}")

    try:
        from core.export_to_csv import export_csvs
        counts = export_csvs()
        print(f"[export] CSVs updated — {counts['signals']} signals, {counts['scan_log']} scan_log, {counts['runs']} runs")
    except Exception as e:
        print(f"[export] CSV update failed (non-fatal): {e}")

    # Weekly digest — send on Sundays or if explicitly triggered
    now_local = datetime.now(timezone.utc)
    if now_local.weekday() == 6:  # Sunday
        try:
            week_sigs = logger.get_week_signals(days=7)
            if week_sigs:
                _weekly_stats    = logger.get_stats()
                _weekly_flag_cal = logger.get_stats_by_flag_path()
                _weekly_heur_cal = logger.get_stats_by_heuristic_label()
                _weekly_whale    = logger.get_stats_by_whale()
                _weekly_brier    = logger.get_brier_score()
                _weekly_lv       = logger.get_stats_by_leviathan_score()
                weekly_body = report.compile_weekly_digest(week_sigs, _weekly_stats, config,
                                                          flag_path_stats=_weekly_flag_cal,
                                                          brier=_weekly_brier,
                                                          lv_stats=_weekly_lv,
                                                          heuristic_label_stats=_weekly_heur_cal,
                                                          whale_stats=_weekly_whale)
                weekly_html = None
                try:
                    weekly_html = report.render_weekly_html(week_sigs, _weekly_stats, config,
                                                            flag_path_stats=_weekly_flag_cal,
                                                            brier=_weekly_brier,
                                                            lv_stats=_weekly_lv,
                                                            heuristic_label_stats=_weekly_heur_cal,
                                                            whale_stats=_weekly_whale)
                except Exception as e:
                    print(f"      [warn] Weekly HTML render failed, sending text-only: {e}")
                report.send_report(weekly_body, [], 0, config,
                                   subject_override=f"Leviathan Weekly — {now_local.strftime('%b %d, %Y')}",
                                   html_body=weekly_html)
                print("      Weekly digest sent")
        except Exception as e:
            print(f"      Weekly digest failed: {e}")

    # Step 7b — Smart money watchlist scan
    smart_money_result = None
    try:
        print("[7b] Running smart money watchlist scan...")
        smart_money_result = run_smart_money_scan(config, force_refresh=False)
        sm_path = save_sm_report(smart_money_result)
        print(f"      {smart_money_result['traders_active']} traders  |  "
              f"{smart_money_result['positions_total']} positions  |  "
              f"{len(smart_money_result['kalshi_signals'])} Kalshi X-refs  |  saved {sm_path}")
    except Exception as e:
        print(f"      Smart money scan failed: {e}")

    # Step 8 — Compile and email report
    print("[8/8] Sending report...")
    try:
        stats            = logger.get_stats()
        probe_stats      = logger.get_stats_probe()
        flag_path_stats  = logger.get_stats_by_flag_path()
        heuristic_stats  = logger.get_stats_by_heuristic_label()
        whale_stats      = logger.get_stats_by_whale()
        lv_stats         = logger.get_stats_by_leviathan_score()
        # Shared now_utc: compile_report and render_html must render the same
        # run's date/time (and every other computed value) identically — see
        # goal_2_email_html_render PART A.
        report_now_utc = datetime.now(timezone.utc)
        body  = report.compile_report(final_signals, whale_only, stats, run_meta, config,
                                      all_filtered=filtered,
                                      new_signals=new_signals,
                                      repeat_signals=repeat_signals,
                                      smart_money_result=smart_money_result,
                                      probe_stats=probe_stats,
                                      flag_path_stats=flag_path_stats,
                                      lv_stats=lv_stats,
                                      db_path=logger.DB_PATH,
                                      now_utc=report_now_utc,
                                      heuristic_label_stats=heuristic_stats,
                                      whale_stats=whale_stats)
        html_body = None
        try:
            html_body = report.render_html(final_signals, whale_only, stats, run_meta, config,
                                           all_filtered=filtered,
                                           new_signals=new_signals,
                                           repeat_signals=repeat_signals,
                                           smart_money_result=smart_money_result,
                                           probe_stats=probe_stats,
                                           flag_path_stats=flag_path_stats,
                                           lv_stats=lv_stats,
                                           db_path=logger.DB_PATH,
                                           now_utc=report_now_utc)
        except Exception as e:
            print(f"      [warn] HTML render failed, sending text-only: {e}")
        report.send_report(body, final_signals, run_meta["whale_flags"], config, html_body=html_body)
    except Exception as e:
        print(f"      FAILED: {e}")
        traceback.print_exc()
        print("\n--- REPORT (unsent) ---")
        try:
            body = report.compile_report(final_signals, whale_only, logger.get_stats(), run_meta, config,
                                         smart_money_result=smart_money_result,
                                         probe_stats=logger.get_stats_probe(),
                                         flag_path_stats=logger.get_stats_by_flag_path(),
                                         lv_stats=logger.get_stats_by_leviathan_score(),
                                         db_path=logger.DB_PATH,
                                         heuristic_label_stats=logger.get_stats_by_heuristic_label(),
                                         whale_stats=logger.get_stats_by_whale())
            print(body)
        except Exception:
            pass

    print(f"\n=== Done in {time.time() - start_time:.1f}s | {len(final_signals)} signals | cost {_fmt_usd(cost)} ===\n")
    if sys.platform == "win32":
        # Task Scheduler's S4U logon runs this in a non-interactive session
        # with no audio device -- PlaySound raises RuntimeError there (real
        # 2026-08-18 incident: the scan/score/log/report work all completed
        # successfully, but this unguarded call still crashed main() at the
        # very last line, so Task Scheduler recorded the whole run as failed
        # (LastTaskResult 0x8007042B) despite nothing upstream being broken.
        # Purely a local "done" chime -- never worth failing a real run over.
        try:
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
        except Exception:
            pass

    # Deliberately last -- unlike the winsound call above (an accidental
    # crash the 2026-08-18 incident specifically fixed away from), this is
    # an intentional signal: markets WERE flagged today but the CLI scoring
    # call never produced a single score, so today's report undercounts by
    # construction. A nonzero exit here is what lets Task Scheduler's
    # RestartCount actually retry -- see scoring_hard_failed above.
    if scoring_hard_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
