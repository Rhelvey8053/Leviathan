"""
Probability estimation via Claude CLI (Claude Code / Pro subscription).
Uses subprocess to call the local `claude` CLI — no Anthropic API key required.
Web search is enabled via the built-in WebSearch tool.
"""

import json
import re
import shutil
import subprocess

SYSTEM_PROMPT = (
    "You are a prediction market analyst. For each market provided, estimate the true "
    "probability of the YES outcome occurring. Use web search to find relevant recent "
    "information. Return ONLY valid JSON — no markdown, no explanation outside the JSON.\n\n"

    "CALIBRATION RULES (follow strictly):\n"
    "1. TAIL PROBABILITY: If the market price is below 15%, it is almost always correct. "
    "The crowd has already discounted this. Require extraordinary, independently-verified "
    "evidence to set your estimate above 30% on a sub-15% market. If in doubt, PASS.\n"
    "2. SOURCE CHAIN: Before citing 'multiple sources confirm X', verify they are truly "
    "independent. Media reports citing the same original tweet/press release/rumour are "
    "ONE source, not many. A viral story is still one source.\n"
    "3. ANNOUNCED vs COMPLETED: For IPOs, mergers, media releases, product launches — "
    "'announced' or 'confirmed in development' is NOT evidence of completion by the "
    "market's deadline. Deals fall through. Release dates slip constantly.\n"
    "4. ENTERTAINMENT/MEDIA MARKETS: Treat any market about a movie, TV show, streaming "
    "release, or entertainment event with extreme skepticism. Even confirmed productions "
    "routinely miss announced dates. Base rate for on-time delivery is ~25%. CRITICAL: "
    "If a market about a media/entertainment release is priced below 10%, your estimate "
    "MUST be below 15% regardless of any announcement you find. 'In production', "
    "'confirmed', 'announced release date' are NOT evidence of on-time delivery — "
    "treat them exactly like Rule 3. Finding a confirmation does NOT justify >15%.\n"
    "5. IPO ANNOUNCEMENT MARKETS ('When will X officially announce an IPO?'): Base rate "
    "is ~25% for any given 3-6 month window regardless of company. 'Confidentially filed', "
    "'preparing to IPO', 'considering going public', 'rumored 2026 IPO', or 'banks hired' "
    "are STANDARD pre-IPO steps that every company goes through — they are NOT evidence of "
    "imminent announcement. Only an actual public S-1 filing or confirmed official date "
    "should meaningfully push your estimate above the base rate.\n"
    "6. CABINET/STAFF DEPARTURE MARKETS ('Will any member of X Cabinet leave before Y?'): "
    "Base rate is ~65% within the first 20 months of a Trump term based on historical "
    "turnover. A market priced below 50% is likely underpriced — weight the historical "
    "base rate heavily unless there is specific evidence of unusual stability.\n"
    "7. SPORTS DEBUT MARKETS ('Will X make his MLB/NBA/NHL debut by Y?'): Base rate for "
    "an unconfirmed prospect is ~35% within a 6-month window. A player 'expected to be "
    "called up', 'on the 40-man roster', or 'in spring training' is still 35% base rate. "
    "Only an active roster assignment with a confirmed start date qualifies as strong "
    "evidence. Injuries to regulars at the prospect's position modestly raise the rate.\n"
    "8. AI/TECH MODEL RELEASE MARKETS ('Will OpenAI/Anthropic/Google release X by Y?'): "
    "Apply Rules 3 and 4 strictly. Base rate is ~25% for any given 3-6 month window. "
    "'In development', 'expected to launch', 'roadmap mentions', 'leaked benchmarks', "
    "and 'CEO hints at release' are NOT evidence of on-time delivery — treat exactly "
    "like Rule 3 (announced ≠ completed). Only an official public release date with a "
    "live product should meaningfully raise your estimate above 25%. Even 'launched in "
    "limited access' does NOT meet the deadline unless the market specifies limited access.\n"
    "9. CROSS-MARKET DIVERGENCE: When CROSS-MARKET or POLYMARKET data shows the same "
    "question priced significantly differently on another platform, treat it as strong "
    "evidence. Polymarket in particular is liquid, has professional traders, and is "
    "often better calibrated than Kalshi for political and world events. A consistent "
    "multi-platform divergence (e.g., Polymarket AND Manifold both 20pp higher than "
    "Kalshi) is a much stronger signal than a single-platform gap — weight it heavily.\n"
    "10. HIGH CONFIDENCE threshold: Only assign HIGH confidence when you find dated, "
    "primary-source evidence (official press release, regulatory filing, official "
    "announcement by the relevant authority) that directly speaks to the specific "
    "deadline in the market. News articles speculating about likelihood do not qualify.\n"
    "11. EDGE REQUIREMENT: Only call YES or NO if your estimate differs from the market "
    "price by at least 10 percentage points AND you have clear evidence. Otherwise PASS.\n"
    "12. LEGISLATIVE MARKETS ('Will X bill pass the Senate/House by Y?'): Base rate for "
    "any specific bill reaching a floor vote and passing is ~35%. A market priced above "
    "55% requires specific primary-source evidence of unusual momentum: cloture already "
    "cleared, the other chamber already passed it, or a confirmed floor vote with a "
    "whip count showing the votes are there. News articles saying a bill 'has momentum' "
    "or 'could pass' do NOT qualify. Avoid HIGH confidence on legislative markets.\n"
    "13. PRICE/LEVEL MARKETS ('Will X reach $Y?' or 'Will X be above/below Y%?'): These "
    "markets are near 50/50 by construction — the crowd has already priced in the "
    "current trajectory. Your estimate should only deviate meaningfully from 50% if you "
    "find a specific, dated catalyst that the crowd has clearly not yet priced. Routine "
    "trend extrapolation is already priced in. Default to PASS on price-level markets "
    "unless the current price is more than 20pp from 50%.\n"
    "14. EARNINGS BEAT/MISS MARKETS ('Will X beat earnings estimates in Q?'): Base rate "
    "is ~50% by definition — analysts recalibrate continuously and the market has priced "
    "the consensus. Deviate meaningfully only if you find a specific, verified catalyst "
    "(channel-check data, pre-announced result, or unusual guidance revision) that the "
    "market has not yet absorbed. 'Strong quarter expected' or 'analysts optimistic' is "
    "already priced in. Default to PASS.\n"
    "15. DIPLOMATIC SUMMIT MARKETS ('Will X meet with Y?' / 'Will there be a bilateral summit?'): "
    "Base rate is ~40% for any specific 3-6 month window — diplomatic meetings are "
    "frequently scheduled, postponed, and rescheduled. 'Talks scheduled', 'both sides "
    "willing', or 'diplomatic channel open' is standard background — NOT evidence a "
    "specific summit will happen by the deadline. Only a confirmed date with official "
    "public statements from both governments qualifies as strong evidence.\n"
    "16. REELECTION MARKETS ('Will X win re-election?'): Treat like general election "
    "markets. Incumbents have a modest structural advantage (~52%), but current polling, "
    "approval ratings, and economic conditions dominate near the election date. Avoid "
    "HIGH confidence unless you find a dated, primary-source polling average with a "
    "clear and sustained lead (>5pp in likely-voter models) within the past 30 days.\n"
    "17. CORPORATE LEADERSHIP MARKETS ('Will X become CEO/CFO/Chair of Y?'): Base rate "
    "is ~35% for any specific appointment within a given window. 'Board is considering', "
    "'rumored front-runner', 'headhunters hired', 'activist pressure mounting', or "
    "'name being floated' are standard pre-appointment steps that frequently do not "
    "materialize. Only a confirmed board announcement or SEC filing (8-K) qualifies as "
    "strong evidence. Media speculation and activist letters do NOT justify >50%.\n"
    "18. UN SECURITY COUNCIL MARKETS ('Will the UNSC pass a resolution on X?'): Base "
    "rate is ~15% due to Chinese and Russian veto risk on most contested topics. "
    "'Widespread Western support', 'draft circulating', or 'strongly worded statement' "
    "are NOT evidence of passage — Russia and China have vetoed dozens of such drafts. "
    "Only a clearly non-contested procedural vote or documented unanimous agreement "
    "justifies an estimate above 30%.\n"
    "19. LEGAL/CRIMINAL PROCEEDINGS MARKETS: Apply category-specific base rates. "
    "Pardon/clemency ('Will X be pardoned?'): ~35% — highly dependent on political climate; "
    "'under consideration' or 'allies lobbying' is NOT a pardon. "
    "Plea deal ('Will X plead guilty?'): ~45% for high-profile contested cases — federal "
    "prosecutors have strong leverage, but prominent defendants often fight charges. "
    "Acquittal ('Will X be acquitted?'): ~35% for prediction-market trials (contested, "
    "high-profile) — conviction rates are high historically but political/celebrity cases "
    "are more mixed. For each, only direct reporting of an imminent agreement or jury "
    "deliberation outcome qualifies as HIGH confidence evidence.\n"
    "20. GOVERNMENT FUNDING / DEBT CEILING MARKETS: Two distinct market types with "
    "opposite base rates. "
    "Shutdown averted ('Will Congress avoid a shutdown by X?'): ~85% — Congress almost "
    "always passes a CR or omnibus at the last minute; treat pessimistic market prices "
    "below 60% as likely underpriced unless specific breakdown evidence is verified. "
    "Shutdown begins ('Will there be a government shutdown in X?'): ~15% — the mirror "
    "of the above. "
    "Debt ceiling raised/suspended ('Will Congress raise the debt ceiling by X?'): ~70% "
    "— default has never occurred; Congress always resolves it, though timing is uncertain. "
    "Generic debt ceiling question (no resolution language): ~65%. "
    "CRITICAL: Distinguish averted/raised (HIGH base rate) from starts/default (LOW base rate).\n"
    "21. GEOPOLITICAL / MILITARY ESCALATION MARKETS: News-cycle intensity is NOT evidence "
    "of probability — base rates for dramatic geopolitical events are very low regardless "
    "of media coverage. Apply these base rates strictly: "
    "Military invasion of a sovereign nation: ~15% per 6-month window; "
    "NATO Article 5 invocation: ~5% (never successfully invoked in modern combat); "
    "US military strike against a named country: ~15%; "
    "Coup or regime change: ~10%. "
    "Escalating rhetoric, troop mobilization headlines, or 'senior officials say X is possible' "
    "are already priced into current market levels — they are NOT independent evidence to raise "
    "your estimate above the base rate. Require a verified, dated incident (confirmed military "
    "action, official government declaration) to deviate meaningfully from base rate. "
    "Default to PASS on geopolitical escalation markets unless edge > 15pp.\n"
    "22. NATURAL DISASTER / WEATHER SEVERITY MARKETS ('Will wildfire burn X million acres?', "
    "'Will hurricane cause $X billion in damage?'): Specific severity thresholds in any given "
    "window are near 50/50 once a hazard has been identified. Base rates: "
    "Wildfire acreage threshold: ~35%; Major hurricane making landfall: ~45% for active seasons; "
    "Earthquake of specific magnitude in a named region: ~30%. "
    "CRITICAL: Weather forecasts, fire weather warnings, and active-season outlooks are "
    "already priced in by the crowd. Do not raise your estimate above the base rate solely "
    "because forecasters say 'conditions are favorable' — that information is public and "
    "already in the market price. Only a confirmed developing event (named storm within 3 days, "
    "active fire already within 50% of the threshold) justifies deviation. Default to PASS.\n"
    "23. AI CAPABILITY MILESTONE MARKETS ('Will AI pass the MCAT?', 'Will AI outperform "
    "doctors on X?', 'Will AI achieve Y score on Z benchmark?'): Distinct from model RELEASE "
    "markets (Rule 8) — these ask whether AI will demonstrate a specific capability, not "
    "whether it will be released. Base rates for any 6-month window: AI passing a professional "
    "exam (bar, MCAT, CPA) ~40%; AI achieving AGI by a specific date <5%; AI passing a "
    "specific coding competition ~50%. CRITICAL: You have a training-data bias toward AI "
    "optimism — successful benchmarks get press coverage while failures are buried. Correct "
    "for this by applying base rates conservatively. 'Achieves near-human performance', "
    "'shows remarkable capabilities', or 'beats previous state of the art' are NOT evidence "
    "of meeting the specific threshold in the market question. Default to PASS unless edge > 12pp.\n"
    "24. BANK FAILURE / FINANCIAL SYSTEM RISK MARKETS ('Will X bank fail?', 'Will there be "
    "a banking crisis?'): Base rates for any given window: Specific named bank failure ~15%; "
    "Systemic banking crisis requiring Fed emergency action ~10%; Regional bank stress "
    "resulting in failure ~20%. CRITICAL: 'Share price declining', 'analyst downgrades', "
    "'liquidity concerns reported', 'stress test scenario', and 'short sellers increasing "
    "positions' are routine banking news that precedes actual failure in only a small "
    "fraction of cases — they are NOT independent evidence above base rate. Only FDIC "
    "intervention, confirmed resolution proceedings, or a verified bank run with documented "
    "deposit outflows qualifies as strong evidence. Default to PASS on bank failure markets "
    "unless you find official regulatory action already taken.\n"
    "25. EMERGING TECHNOLOGY READINESS MARKETS ('Will fully autonomous vehicles be "
    "commercially available?', 'Will quantum computing break encryption by Y?', 'Will "
    "humanoid robots be sold commercially by Z?'): Base rates for a given 6-12 month window: "
    "Full self-driving commercial availability (L4/L5, not geofenced) ~25%; Quantum computing "
    "breaking current RSA encryption <5%; Consumer humanoid robot available at scale ~15%; "
    "Commercial nuclear fusion power <5%. CRITICAL: Technology demonstrations, press "
    "releases, and 'limited pilot programs' are NOT evidence of the broad commercial or "
    "capability threshold typically asked. Regulatory approval ≠ commercial deployment. "
    "A controlled-environment demonstration ≠ general availability. Announced technology "
    "timelines in this space slip by 2-5x on average. Default to PASS.\n"
    "26. CLIMATE / ENVIRONMENTAL RECORDS MARKETS ('Will 2026 be the hottest year on "
    "record?', 'Will global average temperature exceed X°C in Y?'): Different from natural "
    "disaster severity (Rule 22) — records are more trend-predictable but near-coin-flip "
    "for any specific year. Base rates: Hottest year on record in any given recent year "
    "~40%; Specific annual global temperature threshold in a given year ~35-50% depending "
    "on current trajectory. CRITICAL: Climate trend extrapolation is already priced in — "
    "'scientists say it will likely be warm' and 'early months are on pace' are already "
    "reflected in market prices. Only a verifiable multi-month YTD anomaly that definitively "
    "confirms the record is already locked in justifies deviation. Default to PASS unless "
    "the current price is more than 20pp from 50%.\n"
    "27. CRYPTOCURRENCY / DIGITAL ASSET MARKETS ('Will Bitcoin reach $X?', 'Will ETH "
    "price exceed $Y by Z?', 'Will crypto market cap reach $W?'): Rule 13 (price-level "
    "markets) applies strictly, with an additional volatility caveat. Base rates: Bitcoin "
    "reaching a price 50% above current level within 3 months ~25%; reaching 2x within "
    "6 months ~20%; any specific crypto price threshold near current price near 50/50. "
    "CRITICAL: Crypto markets are driven by macro sentiment, regulatory news, and "
    "speculative momentum — all already priced in by the highly active trading community. "
    "'Institutional adoption', 'ETF flows', 'halving cycle', and 'on-chain data suggests "
    "X' are widely cited and already reflected in market prices. Default to PASS on all "
    "crypto price-level markets unless the current price is more than 25pp from 50%."
)

RESPONSE_SCHEMA = """
Return a JSON array where each element has exactly these fields:
{
  "ticker": "string — Kalshi ticker",
  "market_price": 0.00,
  "our_estimate": 0.00,
  "edge": 0.00,
  "direction": "YES | NO | PASS",
  "confidence": "HIGH | MED | LOW",
  "reasoning": "2-3 sentences max",
  "sources_checked": ["headline or url"]
}

direction = "YES" if our_estimate > market_price and edge is worth acting on
direction = "NO" if our_estimate < market_price and edge is worth acting on
direction = "PASS" if edge is not meaningful or evidence is unclear
"""


def _find_claude() -> str:
    """Locate the claude CLI binary."""
    cmd = shutil.which("claude")
    if cmd:
        return cmd
    # Common Windows install paths
    import os
    candidates = [
        r"C:\Users\Administrator\AppData\Local\AnthropicClaude\claude.exe",
        r"C:\Program Files\Claude\claude.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise RuntimeError(
        "claude CLI not found in PATH. "
        "Run Leviathan from a Claude Code terminal, or ensure `claude` is in PATH."
    )


def build_prompt(markets: list[dict]) -> str:
    lines = [
        "Score the following Kalshi prediction markets. For each, search for recent "
        "relevant information and estimate the true probability of YES occurring.\n",
        RESPONSE_SCHEMA,
        "\n--- MARKETS ---\n",
    ]

    from datetime import datetime, timezone as _tz
    _now = datetime.now(_tz.utc)

    for i, m in enumerate(markets, 1):
        mid_price = m.get("mid_price")
        whale     = m.get("whale_data")
        horizon   = m.get("time_horizon", "MONTHLY")
        horizon_note = {
            "INTRADAY":  "closes today — weight breaking news and current momentum only",
            "WEEKLY":    "closes within 7 days — near-term catalysts most relevant",
            "MONTHLY":   "closes within 30 days — balance recent news with base rates",
            "QUARTERLY": "closes within 90 days — fundamentals and structural factors carry more weight",
            "LONG":      "closes 90+ days out — base rates and long-run trends dominate",
        }.get(horizon, "")

        close_str = m.get("close_time") or m.get("expiration_time", "")
        days_left = None
        if close_str:
            try:
                close_dt  = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                days_left = max(0, (close_dt - _now).days)
            except (ValueError, AttributeError):
                pass
        days_note = f" ({days_left}d remaining)" if days_left is not None else ""

        lines.append(f"{i}. [{m.get('ticker', '')}] {(m.get('title', ''))[:120]}")
        lines.append(f"   Horizon: {horizon} ({horizon_note})")
        lines.append(f"   Current market price (YES): {f'{mid_price * 100:.1f}%' if mid_price is not None else 'unknown'}")
        lines.append(f"   Closes: {close_str}{days_note}")

        # Tell Claude WHY this market was flagged
        fp = m.get("flag_path")
        if fp == "HEURISTIC":
            br     = m.get("base_rate")
            hd     = m.get("heuristic_direction")
            br_str = f"base rate {br*100:.0f}%" if br is not None else "base rate unknown"
            lean   = f" — leans {hd}" if hd and hd != "NEUTRAL" else ""
            lines.append(f"   FLAG REASON: HEURISTIC — {br_str} vs market price{lean}")
        elif fp == "DRIFT":
            lines.append(f"   FLAG REASON: DRIFT — market price has moved significantly from last traded price")
        elif fp == "WATCHLIST":
            lines.append(f"   FLAG REASON: WATCHLIST — top Polymarket traders have open positions on this market")
        elif fp == "EDGE":
            br     = m.get("base_rate")
            hd     = m.get("heuristic_direction")
            br_str = f"base rate {br*100:.0f}%" if br is not None else "base rate unknown"
            lean   = f" — leans {hd}" if hd and hd != "NEUTRAL" else ""
            lines.append(f"   FLAG REASON: EDGE — heuristic {br_str} vs market price{lean}")
        elif fp == "CROSS_MARKET":
            poly = m.get("poly") or {}
            gap_pct = abs((poly.get("price_gap") or 0) * 100)
            direction = "higher" if (poly.get("price_gap") or 0) > 0 else "lower"
            lines.append(
                f"   FLAG REASON: CROSS_MARKET — no heuristic or drift signal, but the equivalent "
                f"Polymarket question is priced {gap_pct:.0f}% {direction} than Kalshi. "
                f"Determine whether Kalshi or Polymarket is better calibrated for this event."
            )

        if whale and whale.get("whale_detected"):
            lines.append(
                f"   WHALE ALERT: Large trades detected buying {whale.get('whale_direction', 'unknown')}. "
                f"Max trade size: {whale.get('max_trade_size', 0):.0f} (avg: {whale.get('avg_trade_size', 0):.1f})"
            )

        if m.get("whale_reversal"):
            lines.append(
                f"   REVERSAL SIGNAL: Whale buying {(whale or {}).get('whale_direction', 'unknown')} while "
                f"price is trending the opposite direction — possible informed contrarian positioning."
            )

        if m.get("drift_flag"):
            drift_pct = (m.get("price_drift") or 0) * 100
            lines.append(
                f"   DRIFT SIGNAL: Order-book mid is {abs(drift_pct):.1f}% "
                f"{'above' if drift_pct > 0 else 'below'} last traded price — mean reversion candidate."
            )
            br = m.get("base_rate")
            if br is not None and mid_price is not None:
                drift_says_up   = drift_pct < 0   # mid < last → mean revert up → buy YES
                heuristic_says_up = br > mid_price  # base_rate > mid → YES is underpriced → buy YES
                if drift_says_up != heuristic_says_up:
                    drift_call     = "YES" if drift_says_up     else "NO"
                    heuristic_call = "YES" if heuristic_says_up else "NO"
                    lines.append(
                        f"   SIGNAL CONFLICT: DRIFT suggests {drift_call} (mean revert) but "
                        f"BASE RATE ({br*100:.0f}%) suggests {heuristic_call}. "
                        f"Weight the base rate over drift for fundamental mispricing; "
                        f"use drift only as a secondary timing cue."
                    )

        if m.get("spread_wide"):
            lines.append(
                f"   SPREAD SIGNAL: Bid/ask spread is {(m.get('spread_pct') or 0) * 100:.1f}% of mid — "
                f"market maker uncertainty, higher probability of mispricing."
            )

        ext  = m.get("ext_markets") or []
        cons = m.get("ext_consensus") or {}
        if ext:
            ext_lines = []
            for e in ext[:4]:
                gap = e.get("price_gap", 0) * 100
                ext_lines.append(
                    f"{e['source']}: {e['probability']*100:.1f}% ({gap:+.1f}% vs Kalshi, match {e['match_score']:.2f})"
                )
            if cons.get("consensus_dir"):
                avg_pct = (cons.get("avg_ext_price") or 0) * 100
                cgap    = (cons.get("consensus_gap")  or 0) * 100
                ext_lines.append(
                    f"Consensus ({cons['sources_higher']} higher, {cons['sources_lower']} lower): "
                    f"avg {avg_pct:.1f}% ({cgap:+.1f}% vs Kalshi) → lean {cons['consensus_dir']}"
                )
            lines.append("   CROSS-MARKET:")
            for el in ext_lines:
                lines.append(f"   · {el}")

        smart_money = m.get("smart_money") or []
        if smart_money:
            yes_t = [s for s in smart_money if s.get("direction") == "YES"]
            no_t  = [s for s in smart_money if s.get("direction") == "NO"]
            sm_parts = []
            if yes_t:
                avg_pnl = sum(s["avg_pct_pnl"] for s in yes_t) / len(yes_t)
                sm_parts.append(f"{len(yes_t)} winning wallet(s) buying YES (avg portfolio +{avg_pnl:.1f}%)")
            if no_t:
                avg_pnl = sum(s["avg_pct_pnl"] for s in no_t) / len(no_t)
                sm_parts.append(f"{len(no_t)} winning wallet(s) buying NO (avg portfolio +{avg_pnl:.1f}%)")
            lines.append(f"   SMART MONEY: {'; '.join(sm_parts)}")

        poly = m.get("poly")
        if poly and poly.get("price_gap") is not None:
            gap       = poly["price_gap"] * 100
            direction = "higher" if gap > 0 else "lower"
            lines.append(
                f"   POLYMARKET: Equivalent market priced at {poly['poly_price'] * 100:.1f}% "
                f"({abs(gap):.1f}% {direction} than Kalshi). "
                f'Match: "{poly["poly_question"][:80]}" (score {poly["match_score"]:.2f})'
            )

        if m.get("ob_flag"):
            imb    = (m.get("ob_imbalance") or 0) * 100
            ob_dir = m.get("ob_direction", "?")
            lines.append(
                f"   ORDER BOOK: {imb:.0f}% of depth is on the {ob_dir} side — "
                f"strong {'buying' if ob_dir == 'YES' else 'selling'} pressure."
            )

        if m.get("watchlist_signal"):
            wl_dir    = m.get("watchlist_direction") or "UNKNOWN"
            wl_val    = m.get("watchlist_position_val")
            wl_ntrade = m.get("watchlist_trader_count", 0)
            val_str   = f" (${wl_val:,.0f} combined)" if wl_val else ""
            dir_str   = f" — pointing {wl_dir}" if wl_dir not in ("UNKNOWN", None) else ""
            trade_str = f"{wl_ntrade} trader(s)" if wl_ntrade else "top Polymarket traders"
            lines.append(
                f"   WATCHLIST SIGNAL: {trade_str}{val_str} on Polymarket hold significant "
                f"open positions on a related market{dir_str}. These are top-20 traders by "
                f"monthly PnL — weight this signal; they have demonstrated edge over thousands of trades."
            )

        if m.get("price_trend"):
            lines.append(f"   Price trend: {m['price_trend']}")

        if m.get("base_rate") is not None:
            lines.append(f"   Base rate estimate: {m['base_rate'] * 100:.1f}%")

        vol_total = float(m.get("volume_fp") or m.get("volume") or 0)
        vol_24h   = float(m.get("volume_24h_fp") or 0)
        if vol_total > 0 and vol_24h > 0:
            vol_pct = vol_24h / vol_total * 100
            if vol_pct >= 20:
                lines.append(
                    f"   VOLUME SPIKE: {vol_24h:.0f} contracts traded in past 24h "
                    f"({vol_pct:.0f}% of total {vol_total:.0f}) — recent market activity is elevated."
                )

        prev_p = float(m.get("previous_price_dollars") or 0)
        last_p = float(m.get("last_price_dollars") or 0)
        if prev_p > 0 and last_p > 0:
            jump_pct = (last_p - prev_p) / prev_p * 100
            if abs(jump_pct) >= 20:
                dir_word = "UP" if jump_pct > 0 else "DOWN"
                lines.append(
                    f"   PRICE JUMP: Last traded price moved {dir_word} {abs(jump_pct):.0f}% "
                    f"vs previous snapshot ({prev_p*100:.1f}% → {last_p*100:.1f}%)."
                )

        # Liquidity context — helps Claude calibrate confidence in the market price
        oi  = float(m.get("open_interest_fp") or m.get("open_interest") or 0)
        vol = float(m.get("volume_fp") or m.get("volume") or 0)
        if vol > 0:
            oi_note = f", OI {oi:.0f}" if oi > 0 else ""
            lines.append(f"   Liquidity: {vol:.0f} total volume{oi_note} contracts")

        lines.append("")

    return "\n".join(lines)


def score_markets(flagged_markets: list[dict], config: dict) -> tuple[list[dict], dict]:
    """
    Scores a batch of flagged markets using the local claude CLI.
    Returns (scored_markets, token_info).
    token_info is empty — no API billing when using Pro subscription via CLI.
    """
    if not flagged_markets:
        return [], {}

    max_markets = config.get("scoring", {}).get("max_markets_per_run", 20)
    batch       = flagged_markets[:max_markets]
    user_prompt = build_prompt(batch)
    claude_cmd  = _find_claude()

    # Exclude ANTHROPIC_API_KEY so the CLI uses Pro OAuth instead of the (empty) API key
    import os as _os
    import time as _time
    clean_env = {k: v for k, v in _os.environ.items() if k != "ANTHROPIC_API_KEY"}

    max_retries = 2
    result = None
    for attempt in range(max_retries + 1):
        result = subprocess.run(
            [
                claude_cmd,
                "--print",
                "--system-prompt", SYSTEM_PROMPT,
                "--allowedTools", "WebSearch",
                "--output-format", "text",
            ],
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
            env=clean_env,
        )
        if result.returncode == 0:
            break
        if attempt < max_retries:
            _time.sleep(5)

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"scorer.py: claude CLI returned exit {result.returncode} "
            f"after {max_retries + 1} attempt(s): {err[:300]}"
        )

    all_text = result.stdout.strip()
    if not all_text:
        raise RuntimeError("scorer.py: claude CLI returned empty output")

    # Extract JSON array from response
    fence_match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", all_text, re.DOTALL)
    if fence_match:
        raw_json = fence_match.group(1).strip()
    else:
        start, end = all_text.find("["), all_text.rfind("]")
        raw_json = all_text[start:end + 1] if start != -1 and end > start else all_text

    try:
        scored = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"scorer.py: Failed to parse JSON: {e}\nRaw: {raw_json[:500]}")

    # No API billing — return empty token info
    return scored, {}
