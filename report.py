import os
import smtplib
import textwrap
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

CONFIDENCE_ORDER = {"HIGH": 0, "MED": 1, "LOW": 2}
CONF_LABEL       = {"HIGH": "HIGH", "MED": "MED", "LOW": "LOW"}
HORIZON_LABEL    = {
    "INTRADAY":  "Intraday",
    "WEEKLY":    "Weekly",
    "MONTHLY":   "Monthly",
    "QUARTERLY": "Quarterly",
    "LONG":      "Long-Term",
}
W = 68  # line width


# ── Formatters ────────────────────────────────────────────────────────────────

def _pct(v) -> str:
    try:
        return f"{float(v)*100:.1f}%"
    except Exception:
        return "—"

def _usd(v) -> str:
    try:
        return f"${float(v):.4f}"
    except Exception:
        return "—"

def _rule(char="-") -> str:
    return char * W

def _section(title: str) -> str:
    return f"\n{title.upper()}\n{'-' * len(title)}"

def _wrap(text: str, indent: int = 2, width: int = W) -> list[str]:
    prefix = " " * indent
    return textwrap.wrap(text, width - indent, initial_indent=prefix, subsequent_indent=prefix) or [prefix]


# ── Shared helpers ────────────────────────────────────────────────────────────

def _signal_strength(s: dict) -> int:
    """
    Counts independent corroborating signals for a flagged market.
    Each independent data source that agrees adds 1 point.
    Score ≥ 3 = high-conviction stack.
    """
    score = 0
    # Heuristic or edge disagreement with market price
    if s.get("flag_path") in ("HEURISTIC", "EDGE"):
        score += 1
    # Polymarket divergence ≥ 5 pp
    poly = s.get("poly") or {}
    if poly.get("price_gap") is not None and abs(poly["price_gap"]) >= 0.05:
        score += 1
    # External market consensus (Manifold/PredictIt/Metaculus) — any source agrees on direction
    ext = s.get("ext_markets") or []
    if any(abs(e.get("price_gap", 0)) >= 0.05 for e in ext):
        score += 1
    # Smart money watchlist signal (top Polymarket traders positioned)
    if s.get("watchlist_signal"):
        score += 1
    # Whale activity in same direction as flag
    whale = s.get("whale_data") or {}
    if whale.get("whale_detected"):
        score += 1
    # Smart money wallets active (accounts.py discovery, independent of watchlist)
    if s.get("smart_money"):
        score += 1
    # Cross-market promotion (was flagged purely by Polymarket divergence)
    if s.get("flag_path") == "CROSS_MARKET":
        score += 1
    # Recent activity: volume spike or price jump suggests fresh information
    vol_total = float(s.get("volume_fp") or s.get("volume") or 0)
    vol_24h   = float(s.get("volume_24h_fp") or 0)
    if vol_total > 0 and vol_24h > 0 and (vol_24h / vol_total) >= 0.20:
        score += 1
    prev_p = float(s.get("previous_price_dollars") or 0)
    last_p = float(s.get("last_price_dollars") or 0)
    if prev_p > 0 and last_p > 0 and abs((last_p - prev_p) / prev_p) >= 0.20:
        score += 1
    return score


def compute_leviathan_score(s: dict) -> int:
    """
    Composite signal quality score (0–100) combining confidence, net edge,
    convergence, and persistence.

    Rubric:
      BASE 40
      + Confidence:    HIGH +20, MED +10, LOW 0
      + Net edge:      >10pp +10, 5-10pp +6, 0-5pp +2, ≤0 -8
      + Convergence:   ≥3 sources +10, 2 sources +5
      + Persistence:   3+ consistent days +5, 2 days +2
      + Smart money:   watchlist aligned +4
      + Whale + OB:    both firing +3
      - Short horizon: INTRADAY/WEEKLY -5
      - PASS history:  pass_count ≥3 -8, ≥2 -3

    Clamps to [0, 100].
    """
    pts = 40

    conf = s.get("confidence", "")
    if conf == "HIGH":    pts += 20
    elif conf == "MED":   pts += 10

    ne = s.get("net_edge")
    if ne is not None:
        ne = float(ne)
        if ne > 0.10:      pts += 10
        elif ne > 0.05:    pts += 6
        elif ne > 0:       pts += 2
        else:              pts -= 8

    conv = _signal_strength(s)
    if conv >= 3:   pts += 10
    elif conv >= 2: pts += 5

    pa = s.get("prior_appearances", 0)
    consistent = s.get("direction_consistent")
    if pa >= 3 and consistent:   pts += 5
    elif pa >= 2:                pts += 2

    wl_dir = (s.get("watchlist_direction") or "").upper()
    if s.get("watchlist_signal") and wl_dir in ("YES", "NO"):
        pts += 4

    whale = s.get("whale_data") or {}
    if whale.get("whale_detected") and s.get("ob_flag"):
        pts += 3

    if s.get("time_horizon") in ("INTRADAY", "WEEKLY"):
        pts -= 5

    pc = s.get("pass_count", 0)
    if pc >= 3:   pts -= 8
    elif pc >= 2: pts -= 3

    return max(0, min(100, pts))


def _kelly_fraction(direction: str, market_price: float, estimate: float) -> tuple[float, float] | None:
    """
    Full and quarter-Kelly bet fraction for a binary Kalshi contract.

    Returns (full_kelly, quarter_kelly) as fractions of bankroll, or None if
    no positive edge or inputs are invalid.

    Formula for YES: f* = (p - q) / (1 - q)  where q = market_price
    Formula for NO:  f* = ((1-p) - q_no) / (1 - q_no)  where q_no = 1 - market_price
    """
    if direction not in ("YES", "NO"):
        return None
    try:
        p   = float(estimate)
        mkt = float(market_price)
    except (TypeError, ValueError):
        return None
    if not (0 < mkt < 1) or not (0 < p < 1):
        return None
    if direction == "YES":
        # Buy YES at mkt: win (1-mkt) if correct, lose mkt if wrong
        # Kelly: f* = (p - mkt) / (1 - mkt)
        edge  = p - mkt
        denom = 1 - mkt
    else:
        # Buy NO at (1-mkt): win mkt if correct (YES doesn't resolve), lose (1-mkt) if wrong
        # Kelly: f* = ((1-p) - (1-mkt)) / mkt  =  (mkt - p) / mkt
        edge  = mkt - p
        denom = mkt
    if edge <= 0 or denom <= 0:
        return None
    full_kelly    = edge / denom
    quarter_kelly = full_kelly / 4
    return round(full_kelly, 4), round(quarter_kelly, 4)


def _qualifying(signals: list[dict], threshold_rank: int, min_lv: int = 0) -> list[dict]:
    out = [
        s for s in signals
        if (
            CONFIDENCE_ORDER.get(s.get("confidence", "LOW"), 2) <= threshold_rank
            or s.get("second_pass")  # always include second-pass signals
        )
        and s.get("direction", "PASS") != "PASS"
        and compute_leviathan_score(s) >= min_lv
    ]
    out.sort(key=lambda s: (
        CONFIDENCE_ORDER.get(s.get("confidence", "LOW"), 2),
        -compute_leviathan_score(s),   # composite: strength + net_edge + persistence + smart money
        -(abs(float(s.get("edge") or 0))),  # raw edge as final tiebreaker
    ))
    return out


# ── Signal block ──────────────────────────────────────────────────────────────

def _signal_block(s: dict, index: int = 0) -> list[str]:
    lines = []

    conf      = s.get("confidence", "LOW")
    direction = s.get("direction", "")
    horizon   = HORIZON_LABEL.get(s.get("time_horizon", "MONTHLY"), s.get("time_horizon", ""))
    ticker    = s.get("ticker", "")
    title     = s.get("title", "")
    close_raw = s.get("close_time") or s.get("expiration_time", "")
    close_fmt  = ""
    urgency    = ""
    if close_raw:
        try:
            dt        = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
            close_fmt = dt.strftime("Closes %b %d, %Y").replace(" 0", " ")
            days_left = (dt - datetime.now(timezone.utc)).days
            if days_left <= 0:
                urgency = "  [CLOSING TODAY/TOMORROW]"
            elif days_left <= 3:
                urgency = f"  [CLOSING IN {days_left}d]"
            elif days_left <= 7:
                urgency = f"  [closes in {days_left}d]"
        except Exception:
            close_fmt = close_raw[:10]

    mkt_p    = _pct(s.get("market_price"))
    est_p    = _pct(s.get("our_estimate"))
    edge_v   = float(s.get("edge") or 0)
    edge_s   = f"{edge_v*100:+.1f} pp"
    kelly    = _kelly_fraction(direction, s.get("market_price"), s.get("our_estimate"))
    kelly_s  = (
        f"  (full: {kelly[0]*100:.1f}%  |  1/4 Kelly: {kelly[1]*100:.1f}%)"
        if kelly else ""
    )

    # Header line — includes signal strength score (corroborating evidence count)
    num        = f"[{index}]  " if index else ""
    pass_label = "  [SECOND PASS — LOW CONVICTION]" if s.get("second_pass") else ""
    dg_label   = "  [conf downgraded: edge<10pp]" if s.get("confidence_downgraded") else ""
    fp_label   = f"  [{s.get('flag_path')}]" if s.get("flag_path") else ""
    sh_label   = "  [SHORT HORIZON — verify within 72h]" if s.get("short_horizon") else ""
    strength   = _signal_strength(s)
    str_label  = f"  ★×{strength}" if strength >= 2 else ""
    repeat_cnt  = s.get("repeat_count", 0) or 0
    rep_label   = f"  [REPEAT x{repeat_cnt}]" if repeat_cnt >= 2 else ("  [REPEAT]" if s.get("is_repeat") else "")
    lv_score    = compute_leviathan_score(s)
    if lv_score >= 70:   lv_band = "A"
    elif lv_score >= 55: lv_band = "B"
    elif lv_score >= 40: lv_band = "C"
    else:                lv_band = "D"
    lv_label    = f"  [LV:{lv_score}/{lv_band}]"
    lines.append(f"{num}{CONF_LABEL[conf]} CONFIDENCE  /  BUY {direction}  /  {horizon}{pass_label}{dg_label}{fp_label}{sh_label}{str_label}{lv_label}")
    lines.append(f"{ticker}  ·  {close_fmt}{urgency}{rep_label}" if close_fmt else f"{ticker}{urgency}{rep_label}")
    lines.append("")

    # Title
    lines.extend(_wrap(title))
    lines.append("")

    # Prices
    lines.append(f"  Market:       {mkt_p}")
    lines.append(f"  Our Estimate: {est_p}")
    lines.append(f"  Edge:         {edge_s}")
    _ne = s.get("net_edge")
    if _ne is not None:
        _ne_str = f"  Net Edge:     {_ne*100:+.1f} pp (after spread)"
        if _ne <= 0:
            _ne_str += "  [SPREAD > EDGE]"
        lines.append(_ne_str)
    if kelly_s:
        lines.append(f"  Kelly:{kelly_s}")

    # Signal persistence
    pa = s.get("prior_appearances", 0)
    if pa > 0:
        prev_yes = s.get("prior_yes", 0)
        prev_no  = s.get("prior_no", 0)
        c_str = "consistent" if s.get("direction_consistent") else "mixed"
        lines.append(f"  Seen {pa}d/14d:   {prev_yes}Y/{prev_no}N prior — {c_str}")

    # Signals fired
    fired = []
    if s.get("flag_path") == "HEURISTIC" and s.get("base_rate") is not None:
        br_pct = f"{float(s['base_rate'])*100:.0f}%"
        fired.append(f"Heuristic Base Rate {br_pct}")
    if s.get("drift_flag"):
        fired.append(f"Drift {(s.get('price_drift') or 0)*100:+.0f}%")
    if s.get("spread_wide"):
        fired.append(f"Wide Spread {(s.get('spread_pct') or 0)*100:.0f}%")
    if s.get("whale_reversal"):
        fired.append("Whale Reversal")
    if s.get("ob_flag"):
        fired.append(f"Order Book {s.get('ob_direction','?')} {(s.get('ob_imbalance') or 0)*100:.0f}%")
    n_cross = len([e for e in (s.get("ext_markets") or []) if abs(e.get("price_gap") or 0) >= 0.04])
    if s.get("poly") and abs((s.get("poly") or {}).get("price_gap") or 0) >= 0.04:
        n_cross += 1
    if n_cross:
        fired.append(f"Cross-Market x{n_cross}")
    if s.get("watchlist_signal"):
        fired.append("Watchlist: Top Polymarket Trader")
    if s.get("smart_money"):
        dirs = set(sm.get("direction") for sm in s["smart_money"] if sm.get("direction"))
        fired.append(f"Smart Money x{len(s['smart_money'])} ({'·'.join(dirs)})")

    if fired:
        lines.append(f"  Signals:      {' · '.join(fired)}")

    # Flag conflict warning — DRIFT and HEURISTIC pointing in opposite directions
    drift_pct_val = s.get("price_drift") or 0
    mid_p         = float(s.get("market_price") or 0)
    br            = s.get("base_rate")
    if s.get("drift_flag") and br is not None and mid_p > 0:
        drift_says_up     = drift_pct_val < 0
        heuristic_says_up = br > mid_p
        if drift_says_up != heuristic_says_up:
            drift_call     = "YES" if drift_says_up     else "NO"
            heuristic_call = "YES" if heuristic_says_up else "NO"
            lines.append(
                f"  [!] SIGNAL CONFLICT: Drift -> {drift_call} | "
                f"Base rate {br*100:.0f}% -> {heuristic_call} "
                f"(Claude was instructed to weight base rate)"
            )

    # Heuristic vs Claude direction conflict — Claude overrode the base rate
    _direction = s.get("direction", "PASS")
    _br        = s.get("base_rate")
    _mkt_p     = float(s.get("market_price") or 0)
    if _direction in ("YES", "NO") and _br is not None and _mkt_p > 0:
        _leans_yes = _br > _mkt_p + 0.05
        _leans_no  = _br < _mkt_p - 0.05
        if (_direction == "YES" and _leans_no) or (_direction == "NO" and _leans_yes):
            _heuristic_call = "YES" if _leans_yes else "NO"
            lines.append(
                f"  [!] CLAUDE OVERRIDE: Base rate {_br*100:.0f}% leans {_heuristic_call} "
                f"but Claude called {_direction} — requires strong independent evidence."
            )

    # Cross-market
    poly = s.get("poly")
    ext  = s.get("ext_markets") or []
    all_src = []
    if poly and poly.get("price_gap") is not None:
        all_src.append(("Polymarket", poly.get("poly_price", 0), poly.get("price_gap", 0)))
    for e in ext[:3]:
        all_src.append((e.get("source", "?"), e.get("probability", 0), e.get("price_gap", 0)))

    if all_src:
        lines.append("")
        lines.append("  Cross-Market Prices:")
        for src_name, prob, gap in all_src:
            gap_s = f"{gap*100:+.1f} pp"
            lines.append(f"    {src_name:<14}  {_pct(prob):>6}  ({gap_s} vs Kalshi)")
        cons = s.get("ext_consensus") or {}
        if cons.get("consensus_dir") and len(all_src) > 1:
            avg_p = (cons.get("avg_ext_price") or 0) * 100
            cgap  = (cons.get("consensus_gap") or 0) * 100
            high  = cons.get("sources_higher", 0)
            low_  = cons.get("sources_lower", 0)
            lines.append(f"    Consensus: {high} higher, {low_} lower — avg {avg_p:.1f}% ({cgap:+.1f} pp) → {cons['consensus_dir']}")

    # Smart money
    smart = s.get("smart_money") or []
    if smart:
        lines.append("")
        lines.append("  Smart Money Activity:")
        for sm in smart[:4]:
            # Name: prefer real name, then pseudonym, then truncated address
            name = sm.get("display_name") or sm.get("name") or sm.get("pseudonym") or ""
            addr = sm.get("address", "")
            label = name if name else f"{addr[:8]}...{addr[-4:]}" if len(addr) > 12 else addr
            url   = sm.get("profile_url", "")
            d     = sm.get("direction", "?")
            pnl   = f"+{sm.get('avg_pct_pnl', 0):.0f}%"
            wr    = sm.get("win_rate")
            wr_s  = f"  Win rate: {wr:.0f}%" if wr is not None else ""
            tr    = sm.get("trade_count", 0)
            lines.append(f"    {label}  —  BUY {d}  |  Avg PnL {pnl}{wr_s}  |  {tr} trade(s) on this market")
            if url:
                lines.append(f"    Profile: {url}")
            # Active markets this wallet is currently trading
            active = sm.get("active_markets") or []
            if active:
                lines.append(f"    Also trading:")
                for mkt in active[:3]:
                    mkt_url   = mkt.get("url", "")
                    mkt_title = (mkt.get("title") or "")[:55]
                    mkt_out   = mkt.get("outcome", "")
                    mkt_pnl   = f"{mkt.get('pct_pnl', 0):+.0f}%"
                    url_part  = f"  {mkt_url}" if mkt_url else ""
                    lines.append(f"      {mkt_title}  [{mkt_out}]  {mkt_pnl}{url_part}")

    # Analysis
    reasoning = s.get("reasoning", "")
    sources   = s.get("sources_checked") or []
    if reasoning:
        lines.append("")
        lines.append("  Analysis:")
        lines.extend(_wrap(reasoning, indent=4))
    if sources:
        lines.append(f"  Sources: {' · '.join(sources[:3])}")

    lines.append(_rule("-"))
    return lines


# ── Smart money section ───────────────────────────────────────────────────────

def _smart_money_section(result: dict | None) -> list[str]:
    out = []
    out.append(_rule("="))
    out.append("SMART MONEY WATCHLIST  (Top Polymarket Traders)")
    out.append(_rule("="))
    out.append("")

    if not result:
        out.append("  No smart money data available this run.")
        out.append("")
        return out

    n_traders = result.get("traders_active", 0)
    n_pos     = result.get("positions_total", 0)
    signals   = result.get("kalshi_signals", [])
    run_at    = result.get("run_at", "")[:19].replace("T", " ")

    out.append(f"  Traders Active:     {n_traders}")
    out.append(f"  Positions Tracked:  {n_pos}")
    out.append(f"  Kalshi X-Refs:      {len(signals)}")
    out.append(f"  Snapshot:           {run_at} UTC")
    out.append("")

    # Grouped by Kalshi ticker — most actionable view
    grouped = result.get("grouped_signals", [])
    if grouped:
        out.append("  Kalshi Targets  (grouped by ticker, total smart money behind each):")
        out.append(f"  {'Ticker':<30}  {'Traders':>7}  {'$Total':>11}  {'Direction':<10}  Kalshi Market")
        out.append(f"  {'-'*30}  {'-'*7}  {'-'*11}  {'-'*10}  {'-'*38}")
        for g in sorted(grouped, key=lambda x: -x["total_position_val"]):
            ticker  = g["kalshi_ticker"][:30]
            n_t     = g["trader_count"]
            total_v = f"${g['total_position_val']:>10,.0f}"
            dirs    = g.get("directions", {})
            yes_c   = dirs.get("YES", 0)
            no_c    = dirs.get("NO", 0)
            if yes_c > 0 and no_c > 0:
                dir_s = f"MIXED(Y{yes_c}/N{no_c})"
            else:
                dir_s = g.get("consensus_direction", "?")
            kalshi_t = g.get("kalshi_title", "")[:38]
            out.append(f"  {ticker:<30}  {n_t:>7}  {total_v:>11}  {dir_s:<10}  {kalshi_t}")
        out.append("")

    # Kalshi cross-references — sorted by match quality × position size
    if signals:
        out.append("  Per-Trader Cross-References:")
        out.append(f"  {'Trader':<18}  {'Out':<4}  {'$Position':>10}  {'Price':>5}  {'Match':>5}  Kalshi Ticker")
        out.append(f"  {'-'*18}  {'-'*4}  {'-'*10}  {'-'*5}  {'-'*5}  {'-'*32}")
        ranked = sorted(signals, key=lambda x: -(x["match_score"] * x["position_val"]))
        for s in ranked[:12]:
            trader  = s["trader"][:18]
            outcome = s["poly_outcome"][:4]
            val     = f"${s['position_val']:,.0f}"
            price   = f"{s['poly_price']:.2f}"
            score   = f"{s['match_score']:.0%}"
            ticker  = s["kalshi_ticker"]
            out.append(f"  {trader:<18}  {outcome:<4}  {val:>10}  {price:>5}  {score:>5}  {ticker}")
            # Second line: Poly title → Kalshi title
            poly_t   = s["poly_title"][:48]
            kalshi_t = s.get("kalshi_title", "")[:48]
            if kalshi_t and kalshi_t != poly_t:
                out.append(f"    Poly:   {poly_t}")
                out.append(f"    Kalshi: {kalshi_t}")
            else:
                out.append(f"    {poly_t}")
        out.append("")

    # Top 10 positions across all traders by value
    top_pos = []
    for name, data in result.get("trader_data", {}).items():
        for p in data.get("positions", []):
            val = float(p.get("currentValue") or 0)
            top_pos.append((name, p, val))
    top_pos.sort(key=lambda x: -x[2])

    if top_pos:
        out.append("  Largest Open Positions:")
        out.append(f"  {'Trader':<20}  {'Outcome':<20}  {'Value':>10}  {'Price':>6}  {'PnL':>7}  Market")
        out.append(f"  {'-'*20}  {'-'*20}  {'-'*10}  {'-'*6}  {'-'*7}  -----")
        for name, p, val in top_pos[:10]:
            outcome = (p.get("outcome") or "?")[:20]
            price   = float(p.get("curPrice") or p.get("avgPrice") or 0)
            pnl     = float(p.get("percentPnl") or 0)
            title   = (p.get("title") or "")[:38]
            out.append(f"  {name:<20}  {outcome:<20}  ${val:>9,.0f}  {price:>6.2f}  {pnl:>+6.1f}%  {title}")
        out.append("")

    return out


def _top_picks(signals: list[dict], n: int = 3) -> list[str]:
    """Compact executive summary of the top-N signals sorted by quality score."""
    if not signals:
        return []
    ranked = sorted(signals, key=lambda s: (
        CONFIDENCE_ORDER.get(s.get("confidence", "LOW"), 2),
        -compute_leviathan_score(s),
        -(abs(float(s.get("edge") or 0))),
    ))[:n]

    out = []
    out.append(_rule("="))
    out.append(f"TOP PICKS  (best {min(n, len(ranked))} signals by conviction + edge)")
    out.append(_rule("-"))
    for i, s in enumerate(ranked, 1):
        conf      = s.get("confidence", "LOW")
        direction = s.get("direction", "")
        horizon   = HORIZON_LABEL.get(s.get("time_horizon", "MONTHLY"), s.get("time_horizon", ""))
        fp        = s.get("flag_path", "")
        strength  = _signal_strength(s)
        str_l     = f"  ★×{strength}" if strength >= 2 else ""
        fp_l      = f"  [{fp}]" if fp else ""

        ticker    = s.get("ticker", "")
        close_raw = s.get("close_time") or s.get("expiration_time", "")
        close_fmt = ""
        urgency   = ""
        if close_raw:
            try:
                dt        = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
                close_fmt = dt.strftime("Closes %b %d, %Y").replace(" 0", " ")
                days_left = (dt - datetime.now(timezone.utc)).days
                if days_left <= 0:
                    urgency = "  [CLOSING TODAY/TOMORROW]"
                elif days_left <= 3:
                    urgency = f"  [CLOSING IN {days_left}d]"
                elif days_left <= 7:
                    urgency = f"  [closes in {days_left}d]"
            except Exception:
                close_fmt = close_raw[:10]

        mkt_p   = _pct(s.get("market_price"))
        est_p   = _pct(s.get("our_estimate"))
        edge_v  = float(s.get("edge") or 0)
        kelly   = _kelly_fraction(direction, s.get("market_price"), s.get("our_estimate"))
        kelly_s = f"  Kelly(1/4): {kelly[1]*100:.1f}%" if kelly else ""

        rep_cnt = s.get("repeat_count", 0) or 0
        rep_l   = f"  [REPEAT x{rep_cnt}]" if rep_cnt >= 2 else ("  [REPEAT]" if s.get("is_repeat") else "")

        out.append(f"{i}. {CONF_LABEL[conf]} / BUY {direction}  /  {horizon}{fp_l}{str_l}")
        ticker_close = f"{ticker}  ·  {close_fmt}{urgency}{rep_l}" if close_fmt else f"{ticker}{urgency}{rep_l}"
        out.append(f"   {ticker_close}")
        out.append(f"   Market: {mkt_p}  Est: {est_p}  Edge: {edge_v*100:+.1f} pp{kelly_s}")
        if i < len(ranked):
            out.append("")
    out.append(_rule("="))
    out.append("")
    return out


# ── Daily report ──────────────────────────────────────────────────────────────

def compile_report(
    signals, whale_only, stats, run_meta, config,
    all_filtered=None, new_signals=None, repeat_signals=None,
    smart_money_result=None, probe_stats=None, flag_path_stats=None,
    lv_stats=None,
) -> str:
    threshold_rank = CONFIDENCE_ORDER.get(
        config.get("scoring", {}).get("confidence_threshold", "MED"), 1
    )
    min_lv     = int(config.get("scoring", {}).get("min_report_lv", 0))
    now_utc    = datetime.now(timezone.utc)
    date_str   = now_utc.strftime("%B %d, %Y")
    time_str   = now_utc.strftime("%H:%M UTC")
    env        = config.get("environment", "prod").upper()
    qualifying = _qualifying(signals, threshold_rank, min_lv)
    new_q      = _qualifying(new_signals or [], threshold_rank, min_lv)
    repeat_q   = _qualifying(repeat_signals or [], threshold_rank, min_lv)
    n_mkt      = run_meta.get("markets_scanned", 0)
    runtime_s  = run_meta.get("runtime_ms", 0) / 1000

    out = []

    # ── Header ────────────────────────────────────────────────────────────
    out.append(_rule("="))
    out.append(f"LEVIATHAN  ·  INTELLIGENCE REPORT")
    out.append(f"{date_str}  ·  {time_str}  ·  {env}")
    out.append(_rule("="))
    out.append("")
    sm_xref = len(smart_money_result.get("kalshi_signals", [])) if smart_money_result else 0
    out.append(f"  New Signals:    {len(new_q)}")
    out.append(f"  Repeat Signals: {len(repeat_q)}")
    out.append(f"  Whale Flags:    {len(whale_only)}")
    out.append(f"  Markets Scanned:{n_mkt}")
    out.append(f"  Smart Money:    {sm_xref} Kalshi x-refs from top Polymarket traders")
    out.append("")

    # ── Top picks executive summary ───────────────────────────────────────
    all_q = _qualifying(signals, threshold_rank, min_lv)
    if all_q:
        out.extend(_top_picks(all_q, n=3))

    # ── New signals ───────────────────────────────────────────────────────
    out.append(_rule("="))
    out.append("NEW SIGNALS")
    out.append(_rule("="))

    if not new_q:
        out.append("")
        out.append("  No new signals this run.")
        out.append("")
    else:
        from scanner import BUCKETS as _BUCKETS
        bucket_order = [b[0] for b in _BUCKETS]
        grouped: dict[str, list] = {}
        for s in new_q:
            grouped.setdefault(s.get("time_horizon", "MONTHLY"), []).append(s)

        idx = 1
        for bucket in bucket_order:
            group = grouped.get(bucket)
            if not group:
                continue
            label = HORIZON_LABEL.get(bucket, bucket)
            out.append(f"\n  {label} ({len(group)})")
            out.append("")
            for s in group:
                out.extend(_signal_block(s, index=idx))
                out.append("")
                idx += 1

    # ── Repeat signals (seen in past 7 days) ─────────────────────────────
    if repeat_q:
        out.append(_rule("="))
        out.append("REPEAT SIGNALS  (previously flagged in the last 7 days)")
        out.append(_rule("="))
        out.append("")
        for s in repeat_q:
            ticker = s.get("ticker", "")
            title  = (s.get("title") or "")[:55]
            conf   = CONF_LABEL.get(s.get("confidence", "LOW"), "?")
            dir_   = s.get("direction", "?")
            mkt    = _pct(s.get("market_price"))
            est    = _pct(s.get("our_estimate"))
            edge   = f"{float(s.get('edge') or 0)*100:+.1f} pp"
            out.append(f"  {ticker}  {conf} / BUY {dir_}  ·  Market {mkt}  →  Estimate {est}  ·  Edge {edge}")
            out.extend(_wrap(title, indent=4))
        out.append("")

    # ── Short-term watchlist ──────────────────────────────────────────────
    short_term = sorted(
        [m for m in (all_filtered or []) if m.get("time_horizon") in ("INTRADAY", "WEEKLY")],
        key=lambda m: float(m.get("volume_fp") or m.get("volume") or 0),
        reverse=True,
    )
    out.append(_rule("="))
    out.append("SHORT-TERM WATCHLIST  (Intraday & Weekly)")
    out.append(_rule("="))
    out.append("")
    if not short_term:
        out.append("  No active intraday or weekly markets this run.")
    else:
        out.append(f"  {len(short_term)} market(s) in short-term window")
        out.append("")
        # Column header
        out.append(f"  {'Title':<28}  {'Horizon':<9}  {'Mid':>5}  {'Vol':>6}  Notes")
        out.append(f"  {'-'*28}  {'-'*9}  {'-'*5}  {'-'*6}  -----")
        for m in short_term[:15]:
            title    = (m.get("title") or "")[:28]
            bucket   = HORIZON_LABEL.get(m.get("time_horizon", ""), "")[:9]
            yes_bid  = float(m.get("yes_bid_dollars") or 0)
            yes_ask  = float(m.get("yes_ask_dollars") or 0)
            mid      = (yes_bid + yes_ask) / 2 if (yes_bid + yes_ask) else None
            mid_s    = f"{mid*100:.0f}%" if mid else "—"
            vol_s    = f"{float(m.get('volume_fp') or 0):.0f}"
            notes = []
            if m.get("drift_flag"):
                notes.append(f"drift {(m.get('price_drift') or 0)*100:+.0f}%")
            if m.get("spread_wide"):
                notes.append("wide spread")
            out.append(f"  {title:<28}  {bucket:<9}  {mid_s:>5}  {vol_s:>6}  {', '.join(notes)}")
    out.append("")

    # ── Smart money watchlist ─────────────────────────────────────────────────
    out.extend(_smart_money_section(smart_money_result))

    # ── Whale activity ────────────────────────────────────────────────────
    out.append(_rule("="))
    out.append("WHALE ACTIVITY  (no qualifying signal)")
    out.append(_rule("="))
    out.append("")
    if not whale_only:
        out.append("  No unusual whale activity this run.")
    else:
        out.append(f"  {'Ticker':<22}  {'Direction':<10}  Size vs Avg  Title")
        out.append(f"  {'-'*22}  {'-'*10}  {'-'*11}  -----")
        for w in whale_only:
            avg   = w.get("avg_trade_size", 0)
            ratio = f"{w.get('max_trade_size', 0)/avg:.1f}x" if avg else "—"
            out.append(f"  {w.get('ticker',''):<22}  {w.get('whale_direction','?'):<10}  {ratio:<11}  {(w.get('title',''))[:30]}")
    out.append("")

    # ── Track record ──────────────────────────────────────────────────────
    wr  = stats.get("win_rate")
    ae  = stats.get("avg_edge_captured")
    pnl = stats.get("total_hypothetical_pnl")
    tc  = stats.get("total_calls", 0)
    res = stats.get("resolved", 0)

    out.append(_rule("="))
    out.append("TRACK RECORD")
    out.append(_rule("="))
    out.append("")
    out.append(f"  Total Calls:    {tc}")
    out.append(f"  Resolved:       {res}")
    out.append(f"  Win Rate:       {f'{wr:.1f}%' if wr is not None else '— (no resolved markets yet)'}")
    out.append(f"  Avg Edge:       {_pct(ae) if ae is not None else '—'}")
    out.append(f"  Hypothetical P&L ($10/contract): {f'${pnl:.2f}' if pnl is not None else '—'}")
    out.append("")

    if probe_stats:
        p_total   = probe_stats.get("total_probes", 0)
        p_res     = probe_stats.get("resolved", 0)
        p_hr      = probe_stats.get("hit_rate")
        p_hi_hr   = probe_stats.get("hi_div_hit_rate")
        p_hi_tot  = probe_stats.get("hi_div_total", 0)
        p_verdict = probe_stats.get("verdict", "")
        out.append("  Research Probe Track Record:")
        out.append(f"    Probes Logged:  {p_total}")
        out.append(f"    Resolved:       {p_res}")
        out.append(f"    Hit Rate:       {f'{p_hr:.1f}%' if p_hr is not None else '— (pending settlement)'}")
        out.append(f"    Hi-Div (≥10%):  {p_hi_tot} probes, {f'{p_hi_hr:.1f}%' if p_hi_hr is not None else 'pending'}")
        if p_verdict:
            out.append(f"    Verdict:        {p_verdict}")
        out.append("")

    if flag_path_stats:
        resolved_paths = [r for r in flag_path_stats if r.get("total", 0) > 0]
        if resolved_paths:
            out.append("  Win Rate by Signal Path  (resolved only):")
            out.append(f"    {'Path':<14}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L':>8}")
            out.append(f"    {'-'*14}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*8}")
            for r in resolved_paths:
                wr_s  = f"{r['win_rate']:.0f}%" if r["win_rate"] is not None else "—"
                pnl_s = f"${r['total_pnl']:.2f}" if r["total_pnl"] is not None else "—"
                out.append(f"    {r['flag_path']:<14}  {r['total']:>5}  {r['wins']:>4}  {wr_s:>6}  {pnl_s:>8}")
            out.append("")

    if lv_stats:
        _BAND_ORDER = ("A", "B", "C", "D", "unscored")
        _lv_rows = [(b, lv_stats[b]) for b in _BAND_ORDER
                    if b in lv_stats and lv_stats[b].get("total", 0) > 0]
        if _lv_rows:
            out.append("  Win Rate by LV Grade  (resolved only — validates scoring rubric):")
            out.append(f"    {'Grade':<10}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'AvgEdge':>8}")
            out.append(f"    {'-'*10}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*8}")
            for band, d in _lv_rows:
                wr_s  = f"{d['win_rate']:.0f}%" if d.get("win_rate") is not None else "—"
                ae_s  = f"{d['avg_edge']*100:.1f}pp" if d.get("avg_edge") is not None else "—"
                label = {"A": "A (≥70)", "B": "B (55-69)", "C": "C (40-54)",
                         "D": "D (<40)", "unscored": "unscored"}.get(band, band)
                out.append(f"    {label:<10}  {d['total']:>5}  {d.get('wins',0):>4}  {wr_s:>6}  {ae_s:>8}")
            # Verdict: A vs D delta (when both have data)
            a_d = lv_stats.get("A", {})
            d_d = lv_stats.get("D", {})
            if a_d.get("win_rate") is not None and d_d.get("win_rate") is not None:
                delta = a_d["win_rate"] - d_d["win_rate"]
                arrow = "✓ scoring predicts win rate" if delta >= 10 else "⚠ grade delta small — review rubric"
                out.append(f"    Grade A vs D delta: {delta:+.0f}pp  {arrow}")
            out.append("")

    # ── Run stats ─────────────────────────────────────────────────────────
    out.append(_rule("="))
    out.append("RUN STATISTICS")
    out.append(_rule("="))
    out.append("")
    model = run_meta.get("model_used", "—").replace("claude-", "")
    out.append(f"  Markets Scanned:   {n_mkt}")
    out.append(f"  Signals Generated: {run_meta.get('signals_generated', 0)}")
    out.append(f"  Model:             {model}")
    out.append(f"  Cost:              {_usd(run_meta.get('cost_usd'))}")
    out.append(f"  Runtime:           {runtime_s:.0f}s")
    out.append("")
    out.append(_rule("="))
    out.append("Leviathan v1  ·  Read-only  ·  For informational purposes only")
    out.append(_rule("="))

    return "\n".join(out)


# ── Weekly digest ─────────────────────────────────────────────────────────────

def compile_weekly_digest(week_signals: list[dict], stats: dict, config: dict,
                          flag_path_stats: list | None = None,
                          brier: dict | None = None,
                          lv_stats: dict | None = None) -> str:
    now_utc  = datetime.now(timezone.utc)
    week_ago = now_utc - timedelta(days=7)
    date_str = now_utc.strftime("%B %d, %Y")

    out = []
    out.append(_rule("="))
    out.append("LEVIATHAN  ·  WEEKLY DIGEST")
    out.append(f"Week ending {date_str}")
    out.append(_rule("="))
    out.append("")

    # Deduplicate by ticker — latest signal per market
    by_ticker: dict[str, dict] = {}
    for row in week_signals:
        t = row.get("ticker", "")
        if t not in by_ticker:
            by_ticker[t] = row

    unique_markets = list(by_ticker.values())
    n_calls  = len(week_signals)
    n_mkts   = len(unique_markets)
    n_yes    = sum(1 for r in unique_markets if r.get("direction") == "YES")
    n_no     = sum(1 for r in unique_markets if r.get("direction") == "NO")
    n_high   = sum(1 for r in unique_markets if r.get("confidence") == "HIGH")

    out.append(f"  Unique Markets Flagged:  {n_mkts}")
    out.append(f"  Total Signal Instances:  {n_calls}  (same market may appear multiple days)")
    out.append(f"  Direction Breakdown:     {n_yes} YES · {n_no} NO")
    out.append(f"  High Confidence:         {n_high}")
    out.append("")

    # Signals table — one row per unique market
    out.append(_rule("="))
    out.append("MARKETS FLAGGED THIS WEEK")
    out.append(_rule("="))
    out.append("")
    out.append(f"  {'First Seen':<12}  {'Ticker':<26}  {'Conf':<4}  {'Dir':<3}  {'Edge':>7}  {'Net':>7}  {'LV':>4}  Title")
    out.append(f"  {'-'*12}  {'-'*26}  {'-'*4}  {'-'*3}  {'-'*7}  {'-'*7}  {'-'*4}  -----")

    for row in sorted(unique_markets, key=lambda r: r.get("timestamp", ""), reverse=True):
        ts_raw = row.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            ts_s = ts.strftime("%b %d %H:%M")
        except Exception:
            ts_s = ts_raw[:12]
        ticker = row.get("ticker", "")[:26]
        conf   = CONF_LABEL.get(row.get("confidence", "LOW"), "?")
        dir_   = row.get("direction", "?")
        try:
            edge_s = f"{float(row.get('edge', 0))*100:+.1f}pp"
        except Exception:
            edge_s = "--"
        try:
            ne = row.get("net_edge")
            net_s = f"{float(ne)*100:+.1f}pp" if ne is not None else "--"
        except Exception:
            net_s = "--"
        _lv    = compute_leviathan_score(row)
        _band  = "A" if _lv >= 70 else "B" if _lv >= 55 else "C" if _lv >= 40 else "D"
        lv_s   = f"{_lv}{_band}"
        title  = (row.get("title") or "")[:36]
        out.append(f"  {ts_s:<12}  {ticker:<26}  {conf:<4}  {dir_:<3}  {edge_s:>7}  {net_s:>7}  {lv_s:>4}  {title}")

    out.append("")

    # Cross-market activity this week
    out.append(_rule("="))
    out.append("TRACK RECORD  (all-time)")
    out.append(_rule("="))
    out.append("")
    wr  = stats.get("win_rate")
    ae  = stats.get("avg_edge_captured")
    pnl = stats.get("total_hypothetical_pnl")
    out.append(f"  Total Calls:    {stats.get('total_calls', 0)}")
    out.append(f"  Resolved:       {stats.get('resolved', 0)}")
    out.append(f"  Win Rate:       {f'{wr:.1f}%' if wr is not None else '— (none resolved yet)'}")
    out.append(f"  Avg Edge:       {_pct(ae) if ae is not None else '—'}")
    out.append(f"  Hypo P&L:       {f'${pnl:.2f}' if pnl is not None else '—'}")
    if brier:
        bs = brier.get("brier_score")
        bs_n = brier.get("n", 0)
        bs_label = brier.get("label", "")
        if bs is not None:
            out.append(f"  Brier Score:    {bs:.4f}  ({bs_label}, n={bs_n})  [0=perfect, 0.25=random]")
        else:
            out.append("  Brier Score:    PENDING — no resolved signals yet")
    out.append("")

    if flag_path_stats:
        resolved_paths = [r for r in flag_path_stats if r.get("total", 0) > 0]
        if resolved_paths:
            out.append("  Win Rate by Signal Path  (resolved only):")
            out.append(f"    {'Path':<14}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L':>8}")
            out.append(f"    {'-'*14}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*8}")
            for r in resolved_paths:
                wr_s  = f"{r['win_rate']:.0f}%" if r["win_rate"] is not None else "—"
                pnl_s = f"${r['total_pnl']:.2f}" if r["total_pnl"] is not None else "—"
                out.append(f"    {r['flag_path']:<14}  {r['total']:>5}  {r['wins']:>4}  {wr_s:>6}  {pnl_s:>8}")
            out.append("")

    if lv_stats:
        _BAND_ORDER = ("A", "B", "C", "D", "unscored")
        _lv_rows = [(b, lv_stats[b]) for b in _BAND_ORDER
                    if b in lv_stats and lv_stats[b].get("total", 0) > 0]
        if _lv_rows:
            out.append("  Win Rate by LV Grade  (resolved only):")
            out.append(f"    {'Grade':<10}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'AvgEdge':>8}")
            out.append(f"    {'-'*10}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*8}")
            for band, d in _lv_rows:
                wr_s  = f"{d['win_rate']:.0f}%" if d.get("win_rate") is not None else "—"
                ae_s  = f"{d['avg_edge']*100:.1f}pp" if d.get("avg_edge") is not None else "—"
                label = {"A": "A (≥70)", "B": "B (55-69)", "C": "C (40-54)",
                         "D": "D (<40)", "unscored": "unscored"}.get(band, band)
                out.append(f"    {label:<10}  {d['total']:>5}  {d.get('wins',0):>4}  {wr_s:>6}  {ae_s:>8}")
            out.append("")

    out.append(_rule("="))
    out.append("Leviathan v1  ·  Weekly Summary  ·  For informational purposes only")
    out.append(_rule("="))

    return "\n".join(out)


# ── Send ──────────────────────────────────────────────────────────────────────

def _unsubscribe_footer(token: str) -> str:
    return (
        "\n\n" + "-" * 68 + "\n"
        "Leviathan  ·  Prediction Market Intelligence  ·  For informational purposes only\n"
        f"To unsubscribe: python subscribers.py remove {token}\n"
        "or reply to this email with 'UNSUBSCRIBE' in the subject line."
    )


def send_report(body: str, signals: list[dict], whale_flags: int, config: dict,
                subject_override: str = "") -> None:
    import subscribers as _subs

    report_cfg   = config.get("report", {})
    email_from   = report_cfg.get("email_from") or report_cfg.get("email_to", "")
    smtp_host    = report_cfg.get("smtp_host", "smtp.gmail.com")
    smtp_port    = report_cfg.get("smtp_port", 587)
    app_password = os.getenv("GMAIL_APP_PASSWORD")

    if not app_password:
        raise RuntimeError("GMAIL_APP_PASSWORD not set in environment")

    if subject_override:
        subject = subject_override
    else:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        n_sig    = len(signals)
        subject  = (
            f"Leviathan — {date_str} | "
            f"{n_sig} signal{'s' if n_sig!=1 else ''} | "
            f"{whale_flags} whale flag{'s' if whale_flags!=1 else ''}"
        )

    # Build recipient list: owner (from config) always included, plus active subscribers
    owner        = report_cfg.get("email_to", "")
    active_subs  = _subs.get_active_subscribers()

    recipients: list[tuple[str, str | None]] = []
    if owner:
        recipients.append((owner, None))
    for sub in active_subs:
        if sub["email"] != owner:
            recipients.append((sub["email"], sub["token"]))

    if not recipients:
        raise RuntimeError("No recipients configured (set report.email_to in config.json or add subscribers)")

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(email_from, app_password)

        sent = 0
        for email_to, token in recipients:
            footer      = _unsubscribe_footer(token) if token else (
                "\n\n" + "-" * 68 + "\n"
                "Leviathan  ·  Prediction Market Intelligence  ·  For informational purposes only"
            )
            full_body   = body + footer
            msg         = MIMEText(full_body, "plain", "utf-8")
            msg["Subject"] = subject
            msg["From"]    = email_from
            msg["To"]      = email_to
            try:
                server.sendmail(email_from, email_to, msg.as_string())
                sent += 1
            except Exception as e:
                print(f"  [report] Failed to send to {email_to}: {e}")

    n_subs = len(recipients) - (1 if owner else 0)
    print(f"  [report] Sent to {sent} recipient(s) ({n_subs} subscriber(s))")
