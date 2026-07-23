import html as _html
import os
import smtplib
import textwrap
from datetime import datetime, date, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from analysis.smart_money_scan import _is_sports_title
from core.fees import kalshi_fee
from core.kalshi import kalshi_market_url

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


def _ev_float(direction: str, market_price, estimate, unit_size: float = 10) -> float | None:
    """Returns EV in dollars as a float, or None if inputs are missing/invalid."""
    try:
        mp  = float(market_price)
        est = float(estimate)
    except (TypeError, ValueError):
        return None
    if direction == "YES":
        return (est - mp) * unit_size
    elif direction == "NO":
        return (mp - est) * unit_size
    return None


def _ev_per_contract(direction: str, market_price, estimate, unit_size: float = 10) -> str | None:
    """Returns formatted EV/contract string, or None if inputs are missing."""
    ev = _ev_float(direction, market_price, estimate, unit_size)
    return f"${ev:+.2f}" if ev is not None else None


def _wilson_ci(p_pct, n: int) -> str:
    """Returns a formatted Wilson 95% CI line for a win rate percentage over n trials."""
    if n == 0:
        return "  95% CI:         N/A (no resolved signals)"
    p = p_pct / 100.0
    z = 1.96
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = z * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5) / denom
    low  = center - margin
    high = center + margin
    tag = ", low confidence" if n < 5 else ""
    return f"  95% CI:         {low:.1%} – {high:.1%}  (n={n}{tag})"

def _section(title: str) -> str:
    return f"\n{title.upper()}\n{'-' * len(title)}"

def _wrap(text: str, indent: int = 2, width: int = W) -> list[str]:
    prefix = " " * indent
    return textwrap.wrap(text, width - indent, initial_indent=prefix, subsequent_indent=prefix) or [prefix]


# ── Layout toolkit (PART B) ───────────────────────────────────────────────────

def _close_and_urgency(s: dict) -> tuple[str, str]:
    """
    Parse close_time/expiration_time from a signal dict.
    Returns (close_fmt, urgency_label) with IDENTICAL thresholds to the old
    inline logic in _signal_block and _top_picks (<=0d, <=3d, <=7d).
    """
    close_raw = s.get("close_time") or s.get("expiration_time", "")
    if not close_raw:
        return "", ""
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
        else:
            urgency = ""
        return close_fmt, urgency
    except Exception:
        return close_raw[:10], ""


def _format_label_stack(
    warning_labels: list[str],
    info_labels: list[str],
    max_width: int = 96,
) -> list[str]:
    """
    Format signal header label tags onto at most 2 lines.

    Warning tier (SECOND PASS, conf downgraded, SHORT HORIZON):
      attention-getters about signal quality that need to stand out — rendered
      on their own line with a [!] marker so they are never buried in an
      info-tag run.

    Info tier (flag_path, LV band, strength star, REPEAT count):
      supplementary metadata — rendered together on a second line.

    Returns 0–2 non-empty strings; caller appends them after the primary
    header line (confidence/direction/horizon) and before the ticker line.
    """
    out = []
    if warning_labels:
        line = "  [!] " + "  ".join(warning_labels)
        out.append(line[:max_width])
    if info_labels:
        line = "      " + "  ".join(info_labels)
        out.append(line[:max_width])
    return out


def _render_table(
    headers: list[str],
    rows: list[list[str]],
    widths: list[int] | None = None,
    indent: int = 2,
) -> list[str]:
    """
    Render a fixed-width left-aligned column table.

    If widths is provided each column is exactly that width (content truncated
    with '...' if too long). If widths is None columns auto-size to max content
    width capped at 30 chars each. Two spaces separate columns.
    """
    n_cols = len(headers)
    if widths is None:
        widths = []
        for i in range(n_cols):
            col_vals = [headers[i]] + [r[i] if i < len(r) else "" for r in rows]
            widths.append(min(max(len(v) for v in col_vals), 30))

    prefix = " " * indent

    def _cell(val: str, w: int) -> str:
        if len(val) > w:
            return (val[:w - 3] + "...") if w > 3 else val[:w]
        return val.ljust(w)

    def _row(cells: list[str]) -> str:
        return (prefix + "  ".join(_cell(cells[i] if i < len(cells) else "", widths[i])
                                   for i in range(n_cols))).rstrip()

    out = [_row(headers), prefix + "  ".join("-" * w for w in widths)]
    for row in rows:
        out.append(_row(row))
    return out


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

    ws = s.get("whale_streak", 0)
    if ws >= 3:   pts += 5
    elif ws >= 2: pts += 2

    if s.get("time_horizon") in ("INTRADAY", "WEEKLY"):
        pts -= 5

    pc = s.get("pass_count", 0)
    if pc >= 3:   pts -= 8
    elif pc >= 2: pts -= 3

    # Heuristic specificity bonus — categories with empirically calibrated, precise
    # base rates get extra weight because the heuristic is less noisy than generic ones.
    # HIGH_SPEC: base rates are very well-known and far from 50% (strong prior conviction).
    # MED_SPEC:  base rates are reasonably calibrated (moderate specificity uplift).
    _hl = (s.get("heuristic_label") or "").lower()
    _HIGH_SPEC = {
        "pdufa date",                   # ~85-90% approval rate — gold-standard calibration
        "government shutdown avoided",  # ~85% Congress avoids shutdown historically
        "fda clinical hold",            # ~10% approval while hold is active
        "constitutional amendment",     # ~5% — almost never passes in any given year
        "nato article 5",               # ~5% — never used in combat historically
        "martial law",                  # ~5% — extremely rare in modern democracies
        "volcanic eruption",            # ~5% — geologically rare per year
        "25th amendment",               # ~5% — never successfully used non-voluntarily
    }
    _MED_SPEC = {
        "crypto protocol upgrade",      # ~65% post-testnet — better than generic tech launches
        "debt ceiling resolution",      # ~70% Congress always resolves eventually
        "cabinet departure",            # ~65% based on historical turnover data
        "ceo retention",                # ~65% — most CEOs stay
        "credit rating change",         # ~40% with watch — agency watches are strong predictors
        "opec production decision",     # ~40% well-calibrated from historical meetings
        "chip export restriction",      # ~45% — active US policy area, better calibrated
        "bond/debt issuance",           # ~65% — routine auctions nearly always complete
        "fda complete response letter", # ~60% on resubmission — well-tracked statistic
        "merger or acquisition",        # stage-dependent but well-tracked by deal type
        "merger close (signed deal)",   # ~80% completion once definitive agreement signed
        "hostile takeover bid",         # ~42% — premium-driven, better calibrated than speculation
        "trade tariffs",                # executive action — policy-specific base rates clear
        "presidential veto",            # SAP threats lead to veto ~85% — precise signal
        "spacex launch",                # Falcon 9 cadence well-tracked (~75% on schedule)
    }
    if _hl in _HIGH_SPEC:   pts += 8
    elif _hl in _MED_SPEC:  pts += 4

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

def _signal_block(s: dict, index: int = 0, unit_size: float = 10) -> list[str]:
    lines = []

    conf      = s.get("confidence", "LOW")
    direction = s.get("direction", "")
    horizon   = HORIZON_LABEL.get(s.get("time_horizon", "MONTHLY"), s.get("time_horizon", ""))
    ticker    = s.get("ticker", "")
    title     = s.get("title", "")
    close_fmt, urgency = _close_and_urgency(s)

    mkt_p    = _pct(s.get("market_price"))
    est_p    = _pct(s.get("our_estimate"))
    edge_v   = float(s.get("edge") or 0)
    edge_s   = f"{edge_v*100:+.1f} pp"
    kelly    = _kelly_fraction(direction, s.get("market_price"), s.get("our_estimate"))
    kelly_s  = (
        f"  (full: {kelly[0]*100:.1f}%  |  1/4 Kelly: {kelly[1]*100:.1f}%)"
        if kelly else ""
    )

    # Header line (clean: confidence / direction / horizon only)
    num      = f"[{index}]  " if index else ""
    lines.append(f"{num}{CONF_LABEL[conf]} CONFIDENCE  /  BUY {direction}  /  {horizon}")

    # Warning-tier labels (signal quality concerns) — own line, visually distinct
    # Info-tier labels (metadata tags) — second line
    strength   = _signal_strength(s)
    repeat_cnt = s.get("repeat_count", 0) or 0
    lv_score   = compute_leviathan_score(s)
    lv_band    = "A" if lv_score >= 70 else ("B" if lv_score >= 55 else ("C" if lv_score >= 40 else "D"))

    warn = []
    if s.get("second_pass"):           warn.append("[SECOND PASS — LOW CONVICTION]")
    if s.get("confidence_downgraded"): warn.append("[conf downgraded: edge<10pp]")
    if s.get("short_horizon"):         warn.append("[SHORT HORIZON 72h]")

    info = []
    if s.get("flag_path"):  info.append(f"[{s.get('flag_path')}]")
    if strength >= 2:       info.append(f"★×{strength}")
    info.append(f"[LV:{lv_score}/{lv_band}]")
    if repeat_cnt >= 2:     info.append(f"[REPEAT x{repeat_cnt}]")
    elif s.get("is_repeat"): info.append("[REPEAT]")

    lines.extend(_format_label_stack(warn, info))

    rep_label = ""  # already folded into info labels above
    lines.append(f"{ticker}  ·  {close_fmt}{urgency}" if close_fmt else f"{ticker}{urgency}")
    lines.append("")

    # Title
    lines.extend(_wrap(title))
    lines.append("")

    # Prices
    lines.append(f"  Market:       {mkt_p}")
    lines.append(f"  Our Estimate: {est_p}")
    ext_est = s.get("ext_estimate")
    if ext_est is not None:
        _n_sig  = s.get("ext_n_signals", 0)
        _alpha  = s.get("ext_alpha", 1.0)
        _ext_edge = s.get("ext_edge")
        _ext_edge_str = f"  ext_edge {_ext_edge*100:+.1f}pp" if _ext_edge is not None else ""
        lines.append(
            f"  Adj. Estimate: {_pct(ext_est)}"
            f"  ({_n_sig} signals agree, α={_alpha:.2f}{_ext_edge_str})"
        )
    lines.append(f"  Edge:         {edge_s}")
    _ev = _ev_per_contract(direction, s.get("market_price"), s.get("our_estimate"), unit_size)
    if _ev is not None:
        lines.append(f"  EV/contract:  {_ev}")
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
    ws = s.get("whale_streak", 0)
    if ws >= 2:
        fired.append(f"Whale Streak x{ws}d")
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
        fired_str = " · ".join(fired)
        prefix = "  Signals:      "
        # Wrap long fired-signals list at W chars; subsequent lines align to prefix width
        for ln in textwrap.wrap(fired_str, W - len(prefix),
                                initial_indent=prefix,
                                subsequent_indent=" " * len(prefix)) or [prefix]:
            lines.append(ln)

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
            pnl   = f"+{(sm.get('resolved_avg_pct_pnl') or 0):.0f}%"
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

    # Analysis — always show the section header so absence is explicit, not silent
    reasoning = s.get("reasoning", "")
    sources   = s.get("sources_checked") or []
    lines.append("")
    lines.append("  Analysis:")
    if reasoning:
        lines.extend(_wrap(reasoning, indent=4))
    else:
        lines.append("    (heuristic-only signal — no narrative reasoning generated)")
    if sources:
        lines.append(f"  Sources: {' · '.join(sources[:3])}")

    lines.append(_rule("-"))
    return lines


# ── Smart money section ───────────────────────────────────────────────────────

def _trunc(s: str, n: int, ellipsis: bool = True) -> str:
    if len(s) <= n:
        return s
    return s[:n - 3] + "..." if ellipsis else s[:n]


def _parse_sm_snapshot(md_path: str) -> dict:
    """Returns {(trader, ticker): total_position_val} from a smart_money .md file."""
    snapshot: dict[tuple, float] = {}
    try:
        with open(md_path, encoding="utf-8") as _f:
            in_table = False
            for line in _f:
                line = line.strip()
                if line.startswith("## Kalshi Cross-References"):
                    in_table = True
                    continue
                if in_table and (line.startswith("| Trader") or line.startswith("|---")):
                    continue
                if in_table and line.startswith("|"):
                    cols = [c.strip() for c in line.split("|")[1:-1]]
                    if len(cols) >= 7:
                        trader = cols[0]
                        ticker = cols[4]
                        pos_s  = cols[6].replace("$", "").replace(",", "").strip()
                        try:
                            val = float(pos_s)
                        except ValueError:
                            val = 0.0
                        key = (trader, ticker)
                        snapshot[key] = snapshot.get(key, 0.0) + val
                elif in_table and not line.startswith("|"):
                    break
    except (OSError, IOError):
        pass
    return snapshot


def _smart_money_section(result: dict | None, show_detail: bool = True) -> list[str]:
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

    # Grouped by Kalshi ticker — always shown
    grouped = result.get("grouped_signals", [])
    if grouped:
        sorted_g  = sorted(grouped, key=lambda x: -x["total_position_val"])
        show_g    = sorted_g[:15]
        overflow  = len(sorted_g) - 15
        out.append("  Kalshi Targets  (grouped by ticker):")
        out.append(f"  {'Ticker':<25} {'T':>2} {'$Total':>9} {'Dir':<10}  Title")
        out.append(f"  {'-'*25} {'-'*2} {'-'*9} {'-'*10}  {'-'*18}")
        for g in show_g:
            ticker   = _trunc(g["kalshi_ticker"], 25, ellipsis=False)
            n_t      = g["trader_count"]
            total_v  = f"${g['total_position_val']:>8,.0f}"
            dirs     = g.get("directions", {})
            yes_c    = dirs.get("YES", 0)
            no_c     = dirs.get("NO", 0)
            dir_s    = f"MIXED(Y{yes_c}/N{no_c})" if yes_c > 0 and no_c > 0 else g.get("consensus_direction", "?")
            kalshi_t = _trunc(g.get("kalshi_title", ""), 45)
            out.append(f"  {ticker:<25} {n_t:>2} {total_v:>9} {dir_s:<10}  {kalshi_t}")
        if overflow > 0:
            out.append(f"  ... and {overflow} more")
        out.append("")

    # SMART MONEY DRIFT: compare to yesterday's snapshot
    _run_date = result.get("run_at", "")[:10] if result else ""
    _sm_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "smart_money")
    try:
        _today_dt = date.fromisoformat(_run_date) if _run_date else date.today()
    except ValueError:
        _today_dt = date.today()
    _yest_path = os.path.join(_sm_dir, f"{(_today_dt - timedelta(days=1)).isoformat()}.md")

    if os.path.exists(_yest_path):
        _prev = _parse_sm_snapshot(_yest_path)
        _curr: dict[tuple, float] = {}
        # Build title lookup from signals and grouped data
        _title_by_ticker: dict[str, str] = {}
        for _s in signals:
            _key = (_s.get("trader", ""), _s.get("kalshi_ticker", ""))
            _curr[_key] = _curr.get(_key, 0.0) + float(_s.get("position_val", 0))
            _t = _s.get("kalshi_ticker", "")
            _ti = _s.get("kalshi_title", "")
            if _t and _ti:
                _title_by_ticker[_t] = _ti
        for _g in grouped:
            _t  = _g.get("kalshi_ticker", "")
            _ti = _g.get("kalshi_title", "")
            if _t and _ti:
                _title_by_ticker[_t] = _ti

        _drift: list[tuple] = []
        for _key in sorted(set(_prev) | set(_curr)):
            _trader, _ticker = _key
            _pv = _prev.get(_key, 0.0)
            _cv = _curr.get(_key, 0.0)
            _chg = _cv - _pv
            if abs(_chg) >= 1000:
                if _key not in _prev:
                    _chg_s = "New position"
                elif _key not in _curr:
                    _chg_s = "Closed"
                else:
                    _chg_s = f"{'+' if _chg >= 0 else '-'}${abs(_chg):,.0f}"
                _drift.append((_trader, _ticker, _chg_s))

        out.append("  SMART MONEY DRIFT")
        out.append("  " + "-" * 17)
        if _drift:
            _drift_rows = [
                [_tr[:8], _tk[:25], _cs, _trunc(_title_by_ticker.get(_tk, ""), 24)]
                for _tr, _tk, _cs in _drift
            ]
            out.extend(_render_table(
                ["Wallet", "Ticker", "Change", "Title"],
                _drift_rows,
                widths=[8, 25, 12, 24],
                indent=4,
            ))
        else:
            out.append("  No significant drift today.")
        out.append("")

    if not show_detail:
        return out

    # Per-Trader Cross-References — one line per match
    # Column widths: Trader(18) Out(4) $Pos(10) Price(5) Match(5) Ticker(22) Title(20)
    # indent(2) + 18+4+10+5+5+22+20 = 84 content + 6×2=12 sep = 98 total
    if signals:
        ranked = sorted(signals, key=lambda x: -(x["match_score"] * x["position_val"]))
        out.append("  Per-Trader Cross-References:")
        _xref_rows = []
        for s in ranked[:12]:
            _xref_rows.append([
                _trunc(s["trader"], 18, ellipsis=False),
                s["poly_outcome"][:4],
                f"${s['position_val']:,.0f}",
                f"{s['poly_price']:.2f}",
                f"{s['match_score']:.0%}",
                _trunc(s["kalshi_ticker"], 22, ellipsis=False),
                _trunc(s.get("kalshi_title", ""), 20),
            ])
        out.extend(_render_table(
            ["Trader", "Out", "$Position", "Price", "Match", "Ticker", "Title"],
            _xref_rows,
            widths=[18, 4, 10, 5, 5, 22, 20],
        ))
        out.append("")

    # Largest open positions — sports bets filtered out, capped at 8
    top_pos = []
    for name, data in result.get("trader_data", {}).items():
        for p in data.get("positions", []):
            title = p.get("title") or ""
            if not _is_sports_title(title):
                val = float(p.get("currentValue") or 0)
                top_pos.append((name, p, val))
    top_pos.sort(key=lambda x: -x[2])

    if len(top_pos) >= 3:
        out.append("  Largest Open Positions  (non-sports):")
        out.append(f"  {'Trader':<18}  {'Outcome':<10}  {'Value':>9}  {'Price':>5}  {'PnL':>7}  Market")
        out.append(f"  {'-'*18}  {'-'*10}  {'-'*9}  {'-'*5}  {'-'*7}  -----")
        for name, p, val in top_pos[:8]:
            trader_s = _trunc(name, 18, ellipsis=False)
            outcome  = _trunc(p.get("outcome") or "?", 10, ellipsis=False)
            price    = float(p.get("curPrice") or p.get("avgPrice") or 0)
            pnl      = float(p.get("percentPnl") or 0)
            title_s  = p.get("title") or ""
            title_t  = _trunc(title_s, 42)
            out.append(f"  {trader_s:<18}  {outcome:<10}  ${val:>8,.0f}  {price:>5.2f}  {pnl:>+6.1f}%  {title_t}")
        out.append("")
    else:
        out.append("  (No non-sports positions large enough to display)")
        out.append("")

    return out


# ── HTML rendering helpers ────────────────────────────────────────────────────

def _esc(v) -> str:
    """HTML-escapes any value for safe embedding (titles/tickers come from
    Kalshi market data and may contain &, <, >, quotes)."""
    return _html.escape(str(v if v is not None else ""), quote=True)


def _html_close_date(s: dict) -> str:
    """
    Formats a signal's close date for the HTML pick card, e.g. "closes Jan 1
    2027". Parses the SAME fields (close_time/expiration_time) with the same
    fromisoformat logic as _close_and_urgency — only the final string style
    differs (matching leviathan_report_email_v2.html's cosmetic format),
    never the underlying date value.
    """
    close_raw = s.get("close_time") or s.get("expiration_time", "")
    if not close_raw:
        return ""
    try:
        dt = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
        return dt.strftime("closes %b %-d %Y") if os.name != "nt" else dt.strftime("closes %b %d %Y").replace(" 0", " ")
    except Exception:
        return ""


def _kalshi_link_or_bare(ticker: str, series_ticker: str, event_ticker: str,
                          label: str | None = None) -> str:
    """
    Returns an '<a href="...">display</a>' Kalshi link if series_ticker
    and event_ticker resolve via kalshi_market_url, otherwise the bare
    display text with NO href. Never emits href="" and never builds a URL
    itself — kalshi_market_url is the single confirmed-pattern source of
    truth (rows logged before series_ticker was captured have it empty
    and fall back to bare ticker text).

    `label`, if given, is treated as ALREADY-SAFE markup (e.g. a fixed
    string with an intentional "&nbsp;" entity) and is NOT re-escaped —
    callers must pre-escape any dynamic text (like a ticker) themselves
    before embedding it in `label`. When `label` is omitted, the bare
    `ticker` is escaped here since it's raw, unescaped data.
    """
    display = label if label is not None else _esc(ticker)
    url = kalshi_market_url(series_ticker, event_ticker)
    if not url:
        return display
    return f'<a href="{_esc(url)}" class="klink" style="color:#84b6fb;text-decoration:none;">{display}</a>'


def _rank_top_picks(signals: list[dict], n: int = 3) -> list[dict]:
    """
    Computes the top-N signals by quality score and every per-pick value
    the report needs to display them — ranking, confidence/direction/
    horizon/flag/strength, ticker/close/urgency/repeat labels, and the
    market/est/edge/EV/Kelly stat row.

    This is the SINGLE source of these values: both the text renderer
    (_top_picks) and the HTML renderer (render_html) call this function
    and format its output differently — they can never compute different
    numbers for the same run because there is only one computation.
    """
    if not signals:
        return []
    ranked = sorted(signals, key=lambda s: (
        CONFIDENCE_ORDER.get(s.get("confidence", "LOW"), 2),
        -compute_leviathan_score(s),
        -(abs(float(s.get("edge") or 0))),
    ))[:n]

    picks = []
    for i, s in enumerate(ranked, 1):
        conf      = s.get("confidence", "LOW")
        direction = s.get("direction", "")
        horizon   = HORIZON_LABEL.get(s.get("time_horizon", "MONTHLY"), s.get("time_horizon", ""))
        fp        = s.get("flag_path", "")
        strength  = _signal_strength(s)

        ticker    = s.get("ticker", "")
        close_fmt, urgency = _close_and_urgency(s)

        market_price = s.get("market_price")
        our_estimate = s.get("our_estimate")
        edge_v  = float(s.get("edge") or 0)
        kelly   = _kelly_fraction(direction, market_price, our_estimate)
        ev_s    = _ev_per_contract(direction, market_price, our_estimate)

        rep_cnt = s.get("repeat_count", 0) or 0
        is_repeat = bool(s.get("is_repeat"))

        picks.append({
            "rank":          i,
            "confidence":    conf,
            "direction":     direction,
            "horizon":       horizon,
            "flag_path":     fp,
            "strength":      strength,
            "ticker":        ticker,
            "event_ticker":  s.get("event_ticker", ""),
            "series_ticker": s.get("series_ticker", ""),
            "close_fmt":     close_fmt,
            "close_time_raw": s.get("close_time") or s.get("expiration_time", ""),
            "urgency":       urgency,
            "market_price":  market_price,
            "our_estimate":  our_estimate,
            "market_pct":    _pct(market_price),
            "est_pct":       _pct(our_estimate),
            "edge":          edge_v,
            "kelly":         kelly,
            "ev":            ev_s,
            "repeat_count":  rep_cnt,
            "is_repeat":     is_repeat,
            "title":         s.get("title", "") or "",
        })
    return picks


def _top_picks(signals: list[dict], n: int = 3) -> list[str]:
    """Compact executive summary of the top-N signals sorted by quality score."""
    picks = _rank_top_picks(signals, n=n)
    if not picks:
        return []

    out = []
    out.append(_rule("="))
    out.append(f"TOP PICKS  (best {min(n, len(picks))} signals by conviction + edge)")
    out.append(_rule("-"))
    for p in picks:
        str_l = f"  ★×{p['strength']}" if p["strength"] >= 2 else ""
        fp_l  = f"  [{p['flag_path']}]" if p["flag_path"] else ""

        kelly_s = f"  Kelly(1/4): {p['kelly'][1]*100:.1f}%" if p["kelly"] else ""
        ev_l    = f"  EV: {p['ev']}" if p["ev"] else ""

        rep_l = (f"  [REPEAT x{p['repeat_count']}]" if p["repeat_count"] >= 2
                 else ("  [REPEAT]" if p["is_repeat"] else ""))

        title_s = _trunc(p["title"], 70)

        out.append(f"{p['rank']}. {CONF_LABEL[p['confidence']]} / BUY {p['direction']}  /  "
                    f"{p['horizon']}{fp_l}{str_l}")
        ticker_close = (f"{p['ticker']}  ·  {p['close_fmt']}{p['urgency']}{rep_l}"
                        if p["close_fmt"] else f"{p['ticker']}{p['urgency']}{rep_l}")
        out.append(f"   {ticker_close}")
        if title_s:
            out.append(f"   {title_s}")
        out.append(f"   Market: {p['market_pct']}  Est: {p['est_pct']}  "
                    f"Edge: {p['edge']*100:+.1f} pp{ev_l}{kelly_s}")
        if p["rank"] < len(picks):
            out.append("")
    out.append(_rule("="))
    out.append("")
    return out


def _betting_queue_data(db_path: str | None = None, top_n: int = 5, config: dict | None = None) -> dict:
    """
    Queries and computes the BETTING QUEUE contents: pending signals sorted
    by urgency = (edge*0.6) + (1/days_to_close * 0.4), excluding tickers
    already in real_fill and market_price >= 0.85. Candidates below the EV
    floor (unit_size * min_ev_pct_of_unit after fees) are removed entirely
    and counted separately.

    This is the SINGLE source of the betting queue's DB query and filtering
    logic: both the text renderer (_betting_queue) and the HTML renderer
    (render_html) call this function — there is exactly one query, so the
    two bodies can never show different queue contents or numbers for the
    same run.

    Returns {"error": str} on query failure (callers render an
    "unavailable" message), otherwise:
      {"rows": [...], "below_floor_count": int, "already_placed": [tickers],
       "unit_size": float, "min_ev_pct": float, "ev_floor": float}
    Each row: ticker, event_ticker, series_ticker, direction, conf, title,
    mp, edge, days, urgency, ev_after (float or None), ev_s (formatted), kelly (raw tuple
    or None), kelly_s (formatted).
    """
    import sqlite3 as _sq
    from pathlib import Path as _P

    if db_path is None:
        db_path = str(_P(__file__).parent.parent / "data" / "leviathan.db")

    _bet_cfg   = (config or {}).get("betting", {})
    unit_size  = _bet_cfg.get("unit_size", 10)
    min_ev_pct = _bet_cfg.get("min_ev_pct_of_unit", 0.50)
    ev_floor   = unit_size * min_ev_pct  # e.g. $5.00 at defaults

    try:
        conn = _sq.connect(f"file:{db_path}?mode=ro", uri=True)
        cur  = conn.cursor()
        cur.execute(
            "SELECT ticker FROM signals WHERE source = 'real_fill'"
        )
        placed = {r[0] for r in cur.fetchall()}

        cur.execute(
            "SELECT ticker, direction, market_price, our_estimate, edge, close_time, "
            "confidence, title, event_ticker, series_ticker "
            "FROM signals "
            "WHERE result = '' AND source != 'real_fill' AND direction != 'PASS' "
            "ORDER BY timestamp DESC"
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        return {"error": str(e)}

    now = datetime.now(timezone.utc)
    candidates = []
    already_placed = []
    below_floor_count = 0
    for row in rows:
        ticker, direction, mp, est, edge, close_time, conf, title, event_ticker, series_ticker = row
        if ticker in placed:
            already_placed.append(ticker)
            continue
        try:
            mp_f = float(mp or 0)
        except (TypeError, ValueError):
            mp_f = 0.0
        if mp_f >= 0.85:
            continue
        try:
            edge_f = float(edge or 0)
        except (TypeError, ValueError):
            edge_f = 0.0

        # Fee-adjusted EV — use this for the floor filter per PART C goal
        ev_free  = _ev_float(direction, mp_f, est, unit_size)
        fee      = kalshi_fee(mp_f, unit_size) if mp_f > 0 else 0.0
        ev_after = (ev_free - fee) if ev_free is not None else None

        # EV floor filter: candidates below floor are removed entirely (not just sorted lower)
        if ev_after is None or abs(ev_after) < ev_floor:
            below_floor_count += 1
            continue

        days_left = None
        if close_time:
            try:
                dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                days_left = max((dt - now).total_seconds() / 86400, 0.01)
            except Exception:
                pass
        urgency  = (edge_f * 0.6) + ((1.0 / days_left * 0.4) if days_left else 0.0)
        ev_s     = f"${ev_after:+.2f}" if ev_after is not None else "—"
        kelly    = _kelly_fraction(direction, mp_f, est)
        kelly_s  = f"{kelly[1]*100:.1f}%" if kelly else "—"
        candidates.append({
            "ticker":        ticker,
            "event_ticker":  event_ticker or "",
            "series_ticker": series_ticker or "",
            "direction":    direction,
            "conf":         conf or "?",
            "title":        (title or "").strip(),
            "mp":           mp_f,
            "edge":         edge_f,
            "days":         days_left,
            "urgency":      urgency,
            "ev_after":     ev_after,
            "ev_s":         ev_s,
            "kelly":        kelly,
            "kelly_s":      kelly_s,
        })

    # Deduplicate by ticker — keep highest-urgency row per ticker
    seen: dict[str, dict] = {}
    for c in candidates:
        t = c["ticker"]
        if t not in seen or c["urgency"] > seen[t]["urgency"]:
            seen[t] = c
    top = sorted(seen.values(), key=lambda x: -x["urgency"])[:top_n]

    return {
        "rows":               top,
        "below_floor_count":  below_floor_count,
        "already_placed":     already_placed,
        "unit_size":          unit_size,
        "min_ev_pct":         min_ev_pct,
        "ev_floor":           ev_floor,
    }


def _betting_queue(db_path: str | None = None, top_n: int = 5, config: dict | None = None) -> list[str]:
    """
    Returns lines for the BETTING QUEUE block. See _betting_queue_data for
    the query/filtering logic — this function only formats its output.
    """
    out = []
    out.append(_rule("="))
    out.append("BETTING QUEUE  (top 5 unplaced signals by urgency x edge, after-fee EV floor applied)")
    out.append(_rule("="))
    out.append("")

    data = _betting_queue_data(db_path=db_path, top_n=top_n, config=config)
    if "error" in data:
        out.append(f"  (Queue unavailable: {data['error']})")
        out.append("")
        return out

    top        = data["rows"]
    unit_size  = data["unit_size"]
    min_ev_pct = data["min_ev_pct"]

    # Column layout under 100 chars:
    # indent(2) + #(1) Dir(3) Conf(4) Ticker(20) Dys(3) Price(5) Edge(5) EV(7) K%(5) Title(25)
    # separators: 9×2=18  content: 78  total: 2+18+78 = 98
    _BQ_HDR = ["#", "Dir", "Conf", "Ticker", "Dys", "Price", "Edge", "EV(adj)", "K%", "Title"]
    _BQ_WID = [1, 3, 4, 20, 3, 5, 5, 7, 5, 25]

    if not top:
        out.append("  No unplaced signals in queue.")
    else:
        bq_rows = []
        for i, c in enumerate(top, 1):
            days_s  = f"{c['days']:.0f}" if c["days"] is not None else "—"
            title_s = c["title"] if c["title"] else c["ticker"]  # ticker fallback if title absent
            bq_rows.append([
                str(i),
                c["direction"][:3],
                c["conf"][:4],
                c["ticker"],
                days_s,
                f"{c['mp']*100:.1f}%",
                f"{c['edge']*100:.1f}%",
                c["ev_s"],
                c["kelly_s"],
                title_s,
            ])
        out.extend(_render_table(_BQ_HDR, bq_rows, widths=_BQ_WID))

    pct_label = f"{min_ev_pct*100:.0f}%"
    out.append("")
    out.append(f"  Filtered (EV < {pct_label} of ${unit_size} unit): {data['below_floor_count']}")

    if data["already_placed"]:
        unique_placed = sorted(set(data["already_placed"]))
        placed_s = ", ".join(unique_placed[:8])
        if len(unique_placed) > 8:
            placed_s += f", +{len(unique_placed)-8} more"
        out.append("")
        out.append(f"  Already placed (excluded): {placed_s}")

    out.append("")
    return out


# ── Daily report ──────────────────────────────────────────────────────────────

def _header_data(signals, whale_only, run_meta, config,
                  new_signals=None, repeat_signals=None,
                  smart_money_result=None) -> dict:
    """
    Computes the header/summary-strip values shared by the text renderer
    (compile_report) and the HTML renderer (render_html): New/Repeat/Whale
    counts, markets scanned, smart-money cross-ref count, and the next
    resolution date. Single source — both bodies read this, never
    recompute it independently, so they cannot diverge.
    """
    threshold_rank = CONFIDENCE_ORDER.get(
        config.get("scoring", {}).get("confidence_threshold", "MED"), 1
    )
    min_lv = int(config.get("scoring", {}).get("min_report_lv", 0))

    new_q    = _qualifying(new_signals or [], threshold_rank, min_lv)
    repeat_q = _qualifying(repeat_signals or [], threshold_rank, min_lv)

    sm_xref = len(smart_money_result.get("kalshi_signals", [])) if smart_money_result else 0

    from .logger import get_next_resolution_date as _get_nrd
    next_resolution_date = _get_nrd()
    next_resolution_days = None
    if next_resolution_date:
        try:
            res_dt = date.fromisoformat(next_resolution_date)
            next_resolution_days = (res_dt - date.today()).days
        except Exception:
            next_resolution_days = 0

    return {
        "new_count":             len(new_q),
        "repeat_count":          len(repeat_q),
        "whale_count":           len(whale_only),
        "markets_scanned":       run_meta.get("markets_scanned", 0),
        "smart_money_xref_count": sm_xref,
        "next_resolution_date":  next_resolution_date,
        "next_resolution_days":  next_resolution_days,
    }


def compile_report(
    signals, whale_only, stats, run_meta, config,
    all_filtered=None, new_signals=None, repeat_signals=None,
    smart_money_result=None, probe_stats=None, flag_path_stats=None,
    lv_stats=None, db_path=None, now_utc=None,
) -> str:
    threshold_rank = CONFIDENCE_ORDER.get(
        config.get("scoring", {}).get("confidence_threshold", "MED"), 1
    )
    min_lv     = int(config.get("scoring", {}).get("min_report_lv", 0))
    unit_size  = config.get("betting", {}).get("unit_size", 10)
    now_utc    = now_utc or datetime.now(timezone.utc)
    date_str   = now_utc.strftime("%B %d, %Y")
    time_str   = now_utc.strftime("%H:%M UTC")
    env        = config.get("environment", "prod").upper()
    qualifying = _qualifying(signals, threshold_rank, min_lv)
    new_q      = _qualifying(new_signals or [], threshold_rank, min_lv)
    repeat_q   = _qualifying(repeat_signals or [], threshold_rank, min_lv)
    n_mkt      = run_meta.get("markets_scanned", 0)
    runtime_s  = run_meta.get("runtime_ms", 0) / 1000

    from .logger import get_upcoming_resolutions as _get_upcoming

    hdr = _header_data(signals, whale_only, run_meta, config,
                        new_signals=new_signals, repeat_signals=repeat_signals,
                        smart_money_result=smart_money_result)

    out = []

    # ── Header ────────────────────────────────────────────────────────────
    out.append(_rule("="))
    out.append(f"LEVIATHAN  ·  INTELLIGENCE REPORT")
    out.append(f"{date_str}  ·  {time_str}  ·  {env}")
    out.append(_rule("="))
    out.append("")
    out.append(f"  New Signals:    {hdr['new_count']}")
    out.append(f"  Repeat Signals: {hdr['repeat_count']}")
    out.append(f"  Whale Flags:    {hdr['whale_count']}")
    out.append(f"  Markets Scanned:{hdr['markets_scanned']}")
    out.append(f"  Smart Money:    {hdr['smart_money_xref_count']} Kalshi x-refs from top Polymarket traders")
    if hdr["next_resolution_date"]:
        out.append(f"  Next resolution: {hdr['next_resolution_date']}  ({hdr['next_resolution_days']} days)")
    out.append("")

    # ── Top picks executive summary ───────────────────────────────────────
    all_q = _qualifying(signals, threshold_rank, min_lv)
    if all_q:
        out.extend(_top_picks(all_q, n=3))

    # ── Betting queue ─────────────────────────────────────────────────────
    out.extend(_betting_queue(db_path=db_path, config=config))

    # ── New signals ───────────────────────────────────────────────────────
    out.append(_rule("="))
    out.append("NEW SIGNALS")
    out.append(_rule("="))

    if not new_q:
        out.append("")
        out.append("  No new signals this run.")
        out.append("")
    else:
        from .scanner import BUCKETS as _BUCKETS
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
                out.extend(_signal_block(s, index=idx, unit_size=unit_size))
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
            title  = _trunc(s.get("title") or "", 60)
            conf   = CONF_LABEL.get(s.get("confidence", "LOW"), "?")
            dir_   = s.get("direction", "?")
            mkt    = _pct(s.get("market_price"))
            est    = _pct(s.get("our_estimate"))
            edge   = f"{float(s.get('edge') or 0)*100:+.1f} pp"
            ev_s   = _ev_per_contract(dir_, s.get("market_price"), s.get("our_estimate"), unit_size=unit_size) or ""
            ev_part = f"  ·  EV {ev_s}" if ev_s else ""
            out.append(f"  {ticker}  {conf} / BUY {dir_}  ·  Mkt {mkt}  ->  Est {est}  ·  Edge {edge}{ev_part}")
            out.extend(_wrap(title, indent=4))
            reasoning = (s.get("reasoning") or "").strip()
            if reasoning:
                summary = reasoning.split("\n")[0][:90]
                out.extend(_wrap(f"Analysis: {summary}", indent=4))
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
        _stw_rows = []
        for m in short_term[:15]:
            yes_bid = float(m.get("yes_bid_dollars") or 0)
            yes_ask = float(m.get("yes_ask_dollars") or 0)
            mid     = (yes_bid + yes_ask) / 2 if (yes_bid + yes_ask) else None
            mid_s   = f"{mid*100:.0f}%" if mid else "—"
            vol_s   = f"{float(m.get('volume_fp') or 0):.0f}"
            notes_l = []
            if m.get("drift_flag"):
                notes_l.append(f"drift {(m.get('price_drift') or 0)*100:+.0f}%")
            if m.get("spread_wide"):
                notes_l.append("wide spread")
            _stw_rows.append([
                _trunc(m.get("title") or "", 30),
                HORIZON_LABEL.get(m.get("time_horizon", ""), "")[:9],
                mid_s,
                vol_s,
                ", ".join(notes_l),
            ])
        out.extend(_render_table(
            ["Title", "Horizon", "Mid", "Vol", "Notes"],
            _stw_rows,
            widths=[30, 9, 5, 6, 18],
        ))
    out.append("")

    # ── Smart money watchlist ─────────────────────────────────────────────────
    # show_detail must reflect whether the smart-money scan itself found
    # anything (kalshi_signals) — NOT the scanner's unrelated qualifying
    # count, which used to hide trader detail during scanner dry spells
    # even when smart money had real cross-references to show.
    _sm_has_signals = bool((smart_money_result or {}).get("kalshi_signals"))
    out.extend(_smart_money_section(smart_money_result, show_detail=_sm_has_signals))

    # ── Whale activity ────────────────────────────────────────────────────
    out.append(_rule("="))
    out.append("WHALE ACTIVITY  (no qualifying signal)")
    out.append(_rule("="))
    out.append("")
    if not whale_only:
        out.append("  No unusual whale activity this run.")
    else:
        _wh_rows = []
        for w in whale_only:
            avg   = w.get("avg_trade_size", 0)
            ratio = f"{w.get('max_trade_size', 0)/avg:.1f}x" if avg else "—"
            _wh_rows.append([
                _trunc(w.get("ticker", ""), 22),
                w.get("whale_direction", "?"),
                ratio,
                _trunc(w.get("title", ""), 32),
            ])
        out.extend(_render_table(
            ["Ticker", "Direction", "Size vs Avg", "Title"],
            _wh_rows,
            widths=[22, 10, 11, 32],
        ))
    out.append("")

    # ── Upcoming resolutions ──────────────────────────────────────────────
    upcoming = _get_upcoming(days=14)
    out.append(_rule("="))
    out.append("UPCOMING RESOLUTIONS  (closing within 14 days)")
    out.append(_rule("="))
    out.append("")
    if not upcoming:
        out.append("  No picks closing within 14 days.")
    else:
        out.append(f"  {'Close Date':<12}  {'Ticker':<28}  {'Dir':>3}  {'Conf':>4}  Price")
        out.append(f"  {'-'*12}  {'-'*28}  {'-'*3}  {'-'*4}  -----")
        for row in upcoming:
            close_raw = row.get("close_time", "")
            try:
                close_dt = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
                close_s  = close_dt.strftime("%b %d, %Y")
            except Exception:
                close_s = close_raw[:12]
            ticker  = _trunc(row.get("ticker") or "", 28, ellipsis=False)
            dir_    = row.get("direction", "?")
            conf    = row.get("confidence") or "?"
            price   = row.get("market_price")
            price_s = f"{float(price)*100:.0f}%" if price else "—"
            out.append(f"  {close_s:<12}  {ticker:<28}  {dir_:>3}  {conf:>4}  {price_s}")
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
    out.append(_wilson_ci(wr if wr is not None else 0.0, res))
    out.append(f"  Avg Edge:       {_pct(ae) if ae is not None else '—'}")
    out.append(f"  Hypothetical P&L (${unit_size}/contract): {f'${pnl:.2f}' if pnl is not None else '—'}")
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
        out.append("  " + _wilson_ci(p_hr if p_hr is not None else 0.0, p_res))
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
    model  = run_meta.get("model_used", "—").replace("claude-", "")
    tokens = run_meta.get("tokens_used", 0) or 0
    out.append(f"  Markets Scanned:   {n_mkt}")
    out.append(f"  Signals Generated: {run_meta.get('signals_generated', 0)}")
    out.append(f"  Filtered (high price): {run_meta.get('high_price_filtered', 0)}")
    out.append(f"  Model:             {model}")
    if tokens:
        out.append(f"  Tokens (est.):     {tokens:,}")
    out.append(f"  Cost (est.):       {_usd(run_meta.get('cost_usd'))}  (API equiv. — Pro sub)")
    out.append(f"  Runtime:           {runtime_s:.0f}s")
    out.append("")
    out.append(_rule("="))

    return "\n".join(out)


# ── HTML email (leviathan_report_email_v2.html — see docs/PROGRESS.md) ───────

_HTML_STAT_TILE = (
    '<td style="padding-top:14px;">'
    '<div class="plex" style="font-family:\'IBM Plex Mono\',ui-monospace,Consolas,Menlo,monospace;'
    'font-size:9px;letter-spacing:1px;text-transform:uppercase;color:#8695ac;">{label}</div>'
    '<div class="plex" style="font-family:\'IBM Plex Mono\',ui-monospace,Consolas,Menlo,monospace;'
    'font-size:15px;font-weight:600;color:{color};padding-top:3px;">{value}</div></td>'
)


def _pick_card_html(pick: dict) -> str:
    """Renders one TOP PICKS card matching leviathan_report_email_v2.html."""
    dir_color = "#3ddc9f" if pick["direction"] == "YES" else "#f9bd74"
    dir_bg    = "#0f2a1f" if pick["direction"] == "YES" else "#33260f"
    stars     = f"{'★' * min(pick['strength'], 3)}" if pick["strength"] >= 2 else ""
    star_html = (f'<td class="plex" style="font-family:\'IBM Plex Mono\',ui-monospace,Consolas,Menlo,monospace;'
                 f'font-size:12px;color:#f5c451;letter-spacing:1px;">{stars}</td>') if stars else ""
    fp_html = ""
    if pick["flag_path"]:
        fp_html = (
            '<td style="padding-right:7px;"><span class="plex" style="font-family:\'IBM Plex Mono\','
            'ui-monospace,Consolas,Menlo,monospace;font-size:10px;color:#aab6ca;'
            f'background-color:#1a2334;padding:4px 9px;border-radius:5px;">{_esc(pick["flag_path"])}</span></td>'
        )

    kalshi_link = _kalshi_link_or_bare(pick["ticker"], pick["series_ticker"], pick["event_ticker"],
                                       label="Trade on Kalshi&nbsp;↗")
    ticker_link = _kalshi_link_or_bare(pick["ticker"], pick["series_ticker"], pick["event_ticker"])

    rep_s = (f" · REPEAT ×{pick['repeat_count']}" if pick["repeat_count"] >= 2
             else (" · REPEAT" if pick["is_repeat"] else ""))
    meta_bits = " · ".join(x for x in [pick["horizon"], pick.get("_close_html", "")] if x)

    kelly_html = ""
    if pick["kelly"]:
        kelly_html = _HTML_STAT_TILE.format(label="Kelly¼", color="#f2f5fa",
                                             value=f"{pick['kelly'][1]*100:.1f}%")
    ev_html = ""
    if pick["ev"]:
        ev_html = _HTML_STAT_TILE.format(label="EV/ct", color="#3ddc9f", value=_esc(pick["ev"]))

    return f'''
    <tr><td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0f1521" style="background-color:#0f1521;border:1px solid #273246;border-radius:10px;">
        <tr>
          <td width="3" bgcolor="#f7ad57" style="background-color:#f7ad57;font-size:0;line-height:0;border-radius:10px 0 0 10px;">&nbsp;</td>
          <td style="padding:18px 22px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
              <td>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
                  <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;font-weight:600;color:#8695ac;padding-right:10px;">{pick['rank']:02d}</td>
                  <td style="padding-right:7px;"><span class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:10px;font-weight:700;color:{dir_color};background-color:{dir_bg};padding:4px 9px;border-radius:5px;">BUY&nbsp;{_esc(pick['direction'])}</span></td>
                  <td style="padding-right:7px;"><span class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:10px;font-weight:600;color:#93bdf7;background-color:#152a48;padding:4px 9px;border-radius:5px;">{_esc(pick['confidence'])}</span></td>
                  {fp_html}
                  {star_html}
                </tr></table>
              </td>
              <td align="right" valign="top" class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;">{kalshi_link}</td>
            </tr></table>
            <div style="font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:15.5px;font-weight:500;color:#f2f5fa;line-height:1.45;padding:14px 0 4px;">{_esc(pick['title'])}</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;padding-bottom:15px;">{ticker_link} <span style="color:#7c8aa1;">&nbsp;·&nbsp; {_esc(meta_bits)}{_esc(rep_s)}</span></div>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-top:1px solid #1e2838;"><tr>
              {_HTML_STAT_TILE.format(label="Market", color="#f2f5fa", value=_esc(pick["market_pct"]))}
              {_HTML_STAT_TILE.format(label="Est", color="#f2f5fa", value=_esc(pick["est_pct"]))}
              {_HTML_STAT_TILE.format(label="Edge", color="#3ddc9f", value=_esc(f"{pick['edge']*100:+.1f}"))}
              {ev_html}
              {kelly_html}
            </tr></table>
          </td>
        </tr>
      </table>
    </td></tr>
    <tr><td height="13" style="font-size:0;line-height:0;">&nbsp;</td></tr>'''


def _betting_row_html(row: dict) -> str:
    """Renders one BETTING QUEUE table row matching leviathan_report_email_v2.html."""
    dir_color = "#3ddc9f" if row["direction"] == "YES" else "#f9bd74"
    ev_s = row["ev_s"] if row["ev_s"] != "—" else "—"
    link = _kalshi_link_or_bare(row["ticker"], row["series_ticker"], row["event_ticker"],
                                label=f"{_esc(row['ticker'])}&nbsp;↗")
    title_s = _esc(row["title"]) if row["title"] else ""
    return f'''
        <tr>
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;color:{dir_color};font-weight:600;padding:13px 8px 13px 16px;border-bottom:1px solid #1e2838;vertical-align:top;">{_esc(row['direction'])}</td>
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;color:#f2f5fa;padding:13px 8px;border-bottom:1px solid #1e2838;vertical-align:top;">{_esc(row['conf'])}</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;color:#3ddc9f;padding:13px 8px;border-bottom:1px solid #1e2838;vertical-align:top;white-space:nowrap;">{row['edge']*100:.1f}%</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;color:#3ddc9f;padding:13px 8px;border-bottom:1px solid #1e2838;vertical-align:top;white-space:nowrap;">{_esc(ev_s)}</td>
          <td style="padding:13px 16px 13px 8px;border-bottom:1px solid #1e2838;vertical-align:top;">
            {link}
            <div style="font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;font-size:12px;color:#b3bdd0;line-height:1.4;padding-top:3px;">{title_s}</div>
          </td>
        </tr>'''


def render_html(
    signals, whale_only, stats, run_meta, config,
    all_filtered=None, new_signals=None, repeat_signals=None,
    smart_money_result=None, probe_stats=None, flag_path_stats=None,
    lv_stats=None, db_path=None, now_utc=None,
) -> str:
    """
    Renders the daily report as email-safe HTML matching
    leviathan_report_email_v2.html (dark theme, table-based, inline CSS,
    600px container). Presentation-layer only — every value here comes
    from the SAME shared computations compile_report uses (_header_data,
    _rank_top_picks, _betting_queue_data): the two bodies of one email can
    never show different numbers for the same run. No Track Record section
    (intentionally dropped — lives in Power BI).
    """
    threshold_rank = CONFIDENCE_ORDER.get(
        config.get("scoring", {}).get("confidence_threshold", "MED"), 1
    )
    min_lv   = int(config.get("scoring", {}).get("min_report_lv", 0))
    now_utc  = now_utc or datetime.now(timezone.utc)
    date_str = now_utc.strftime("%B %d, %Y")
    time_str = now_utc.strftime("%H:%M UTC")
    env      = config.get("environment", "prod").upper()
    n_mkt    = run_meta.get("markets_scanned", 0)
    runtime_s = run_meta.get("runtime_ms", 0) / 1000
    model    = run_meta.get("model_used", "—").replace("claude-", "")

    hdr = _header_data(signals, whale_only, run_meta, config,
                       new_signals=new_signals, repeat_signals=repeat_signals,
                       smart_money_result=smart_money_result)

    all_q = _qualifying(signals, threshold_rank, min_lv)
    picks = _rank_top_picks(all_q, n=3)
    for p in picks:
        p["_close_html"] = _html_close_date({"close_time": p["close_time_raw"]})

    bq_data = _betting_queue_data(db_path=db_path, config=config)
    bq_rows = bq_data.get("rows", []) if "error" not in bq_data else []
    bq_below_floor = bq_data.get("below_floor_count", 0) if "error" not in bq_data else 0
    unit_size  = bq_data.get("unit_size", config.get("betting", {}).get("unit_size", 10))
    min_ev_pct = bq_data.get("min_ev_pct", config.get("betting", {}).get("min_ev_pct_of_unit", 0.50))

    preheader_signals = hdr["new_count"] + hdr["repeat_count"]
    next_res_short = ""  # "Aug 1" — preheader form (matches v2, no day-count)
    next_res_s = ""       # "Aug 1 · 13d" — summary-tile form
    if hdr["next_resolution_date"]:
        try:
            _d = date.fromisoformat(hdr["next_resolution_date"])
            next_res_short = f"{_d.strftime('%b')} {_d.day}"
            next_res_s = f"{next_res_short} · {hdr['next_resolution_days']}d"
        except Exception:
            next_res_short = next_res_s = hdr["next_resolution_date"]

    picks_html = "".join(_pick_card_html(p) for p in picks) if picks else (
        '<tr><td style="padding:24px 0;color:#8695ac;" class="plex">'
        'No qualifying picks this run.</td></tr>'
    )
    bq_rows_html = "".join(_betting_row_html(r) for r in bq_rows) if bq_rows else (
        '<tr><td colspan="5" style="padding:16px;color:#8695ac;" class="plex">'
        'No unplaced signals in queue.</td></tr>'
    )

    preheader = (f"{preheader_signals} signals · {hdr['whale_count']} whale flags · "
                 f"next resolution {next_res_short or '—'} · {len(picks)} picks live on Kalshi")

    html_doc = f'''<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="color-scheme" content="dark light">
<meta name="supported-color-schemes" content="dark light">
<title>Leviathan — Intelligence Report</title>
<!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&display=swap');
  body,table,td{{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;}}
  a{{color:#84b6fb;}}
  .plex{{font-family:'IBM Plex Mono','SFMono-Regular',ui-monospace,Consolas,Menlo,monospace !important;}}
  .klink{{color:#84b6fb !important;text-decoration:none;}}
  .klink:hover{{text-decoration:underline;}}
  @media only screen and (max-width:620px){{
    .container{{width:100% !important;}}
    .stack{{display:block !important;width:100% !important;box-sizing:border-box !important;}}
    .px{{padding-left:20px !important;padding-right:20px !important;}}
  }}
</style>
</head>
<body style="margin:0;padding:0;background-color:#070a12;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#070a12;font-size:1px;line-height:1px;">{_esc(preheader)}</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#070a12" style="background-color:#070a12;">
<tr><td align="center" style="padding:34px 12px 56px;">

  <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">

    <!-- HEADER -->
    <tr><td bgcolor="#0f1521" style="background-color:#0f1521;border:1px solid #273246;border-radius:12px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr><td class="px" style="padding:24px 28px 8px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
            <td align="left" class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:22px;font-weight:700;letter-spacing:3px;color:#f2f5fa;">LEVIATHAN<span style="color:#4a90f2;">//</span></td>
            <td align="right" class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:10px;font-weight:500;letter-spacing:3px;color:#aab6ca;text-transform:uppercase;">Intelligence&nbsp;Report</td>
          </tr></table>
        </td></tr>
        <tr><td class="px" style="padding:16px 28px 0;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
            <td width="50" height="2" bgcolor="#4a90f2" style="background-color:#4a90f2;font-size:0;line-height:0;">&nbsp;</td>
            <td height="2" bgcolor="#273246" style="background-color:#273246;font-size:0;line-height:0;">&nbsp;</td>
          </tr></table>
        </td></tr>
        <tr><td class="px plex" style="padding:15px 28px 24px;font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11.5px;color:#aeb9cd;line-height:1.7;">
          <span style="color:#3ddc9f;">●</span> <span style="color:#f2f5fa;">{_esc(env)}</span>&nbsp;&nbsp;·&nbsp;&nbsp;<span style="color:#f2f5fa;">{_esc(date_str)}</span>&nbsp;&nbsp;·&nbsp;&nbsp;{_esc(time_str)}&nbsp;&nbsp;·&nbsp;&nbsp;scanned <span style="color:#f2f5fa;">{n_mkt:,}</span>&nbsp;&nbsp;·&nbsp;&nbsp;runtime <span style="color:#f2f5fa;">{runtime_s:.0f}s</span>
        </td></tr>
      </table>
    </td></tr>

    <tr><td height="18" style="font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- SUMMARY -->
    <tr><td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#273246" style="background-color:#273246;border:1px solid #273246;border-radius:12px;">
        <tr>
          <td class="stack" width="33.33%" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-radius:12px 0 0 0;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">New</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:20px;font-weight:600;color:#f2f5fa;padding-top:4px;">{hdr['new_count']}</div>
          </td>
          <td class="stack" width="33.33%" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-left:1px solid #273246;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Repeat</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:20px;font-weight:600;color:#f2f5fa;padding-top:4px;">{hdr['repeat_count']}</div>
          </td>
          <td class="stack" width="33.33%" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-left:1px solid #273246;border-radius:0 12px 0 0;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Whale Flags</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:20px;font-weight:600;color:#f2f5fa;padding-top:4px;">{hdr['whale_count']}</div>
          </td>
        </tr>
        <tr>
          <td class="stack" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-top:1px solid #273246;border-radius:0 0 0 12px;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Smart-Money X-refs</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:15px;font-weight:500;color:#c6cfde;padding-top:5px;">{hdr['smart_money_xref_count']} active</div>
          </td>
          <td class="stack" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-top:1px solid #273246;border-left:1px solid #273246;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Next Resolution</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:15px;font-weight:500;color:#c6cfde;padding-top:5px;">{_esc(next_res_s or "—")}</div>
          </td>
          <td class="stack" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-top:1px solid #273246;border-left:1px solid #273246;border-radius:0 0 12px 0;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Model</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:15px;font-weight:500;color:#c6cfde;padding-top:5px;">{_esc(model)}</div>
          </td>
        </tr>
      </table>
    </td></tr>

    <tr><td height="34" style="font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- TOP PICKS -->
    <tr><td class="px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#f2f5fa;white-space:nowrap;padding-right:14px;">Top Picks</td>
        <td width="100%" style="border-bottom:1px solid #273246;">&nbsp;</td>
        <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:10px;color:#8695ac;white-space:nowrap;padding-left:14px;">best {len(picks)} · conviction × edge</td>
      </tr></table>
    </td></tr>
    <tr><td height="16" style="font-size:0;line-height:0;">&nbsp;</td></tr>
    {picks_html}

    <tr><td height="34" style="font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- BETTING QUEUE -->
    <tr><td class="px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#f2f5fa;white-space:nowrap;padding-right:14px;">Betting Queue</td>
        <td width="100%" style="border-bottom:1px solid #273246;">&nbsp;</td>
        <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:10px;color:#8695ac;white-space:nowrap;padding-left:14px;">urgency × edge · after-fee floor</td>
      </tr></table>
    </td></tr>
    <tr><td height="16" style="font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0f1521" style="background-color:#0f1521;border:1px solid #273246;border-radius:10px;">
        <tr bgcolor="#151d2c" style="background-color:#151d2c;">
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px 11px 16px;border-bottom:1px solid #273246;">Dir</td>
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Conf</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Edge</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">EV</td>
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 16px 11px 8px;border-bottom:1px solid #273246;">Market</td>
        </tr>
        {bq_rows_html}
      </table>
    </td></tr>
    <tr><td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#8695ac;padding:10px 2px 0;">— {bq_below_floor} candidates filtered (EV &lt; {min_ev_pct*100:.0f}% of ${unit_size:.0f} unit)</td></tr>

    <tr><td height="30" style="font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- FOOTER -->
    <tr><td class="px" style="border-top:1px solid #273246;padding-top:18px;">
      <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:10.5px;color:#8695ac;line-height:1.9;">
        signals generated <span style="color:#c6cfde;">{run_meta.get('signals_generated', 0)}</span> &nbsp;·&nbsp; filtered (high price) <span style="color:#c6cfde;">{run_meta.get('high_price_filtered', 0)}</span> &nbsp;·&nbsp; model <span style="color:#c6cfde;">{_esc(model)}</span> &nbsp;·&nbsp; cost <span style="color:#c6cfde;">${run_meta.get('cost_usd') or 0:.2f} · Pro</span>
      </div>
      <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:10px;color:#66738a;padding-top:11px;letter-spacing:1px;">LEVIATHAN // PREDICTION-MARKET INTELLIGENCE</div>
    </td></tr>

  </table>
</td></tr>
</table>
</body>
</html>
'''
    return html_doc


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
        ticker = _trunc(row.get("ticker", ""), 28, ellipsis=False)
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
        title  = _trunc(row.get("title") or "", 35)
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
    out.append(_wilson_ci(wr if wr is not None else 0.0, stats.get("resolved", 0)))
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
                subject_override: str = "", html_body: str | None = None) -> None:
    """
    Sends the report by email. With html_body omitted (the default), sends
    a single text/plain message exactly as before — every existing caller
    (weekly digest, etc.) is unaffected. With html_body provided, sends
    multipart/alternative: html_body as the text/html part (primary) and
    body as the text/plain part (fallback — never dropped, so text-only
    clients and the clip-view degrade cleanly). Subject and recipient
    logic are unchanged either way.
    """
    from . import subscribers as _subs

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

            if html_body is not None:
                msg = MIMEMultipart("alternative")
                msg.attach(MIMEText(full_body, "plain", "utf-8"))
                msg.attach(MIMEText(html_body, "html", "utf-8"))
            else:
                msg = MIMEText(full_body, "plain", "utf-8")
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


# ── --dry-run CLI (PART D) ────────────────────────────────────────────────────

def _synthetic_dry_run_signals() -> list[dict]:
    """Fallback signal data for --dry-run when the real DB has nothing recent."""
    return [
        {"ticker": "KXSPCELAUNCH-COMM-27JAN01", "event_ticker": "KXSPCELAUNCH-COMM-27JAN01",
         "series_ticker": "KXSPCELAUNCH",
         "title": "When will Virgin Galactic launch its next commercial Delta-class SpaceShip flight?",
         "direction": "NO", "confidence": "MED", "flag_path": "HEURISTIC",
         "market_price": 0.595, "our_estimate": 0.40, "edge": 0.195,
         "time_horizon": "LONG", "close_time": "2027-01-01T15:00:00Z",
         "is_repeat": True, "repeat_count": 2},
        {"ticker": "KXALBUMRELEASEDATEBEY-NEW-JAN01-27", "event_ticker": "KXALBUMRELEASEDATEBEY-NEW-JAN01-27",
         "series_ticker": "KXALBUMRELEASEDATEBEY",
         "title": "Will Beyoncé release a new album before Jan 1, 2027?",
         "direction": "NO", "confidence": "MED", "flag_path": "DRIFT",
         "market_price": 0.59, "our_estimate": 0.40, "edge": 0.19,
         "time_horizon": "LONG", "close_time": "2027-01-01T15:00:00Z",
         "is_repeat": True, "repeat_count": 2},
        {"ticker": "KXMANAGEROUTDATE-28TUCHEL-26AUG01", "event_ticker": "KXMANAGEROUTDATE-28TUCHEL-26AUG01",
         "series_ticker": "KXMANAGEROUTDATE",
         "title": "Will Thomas Tuchel be out before Aug 1, 2026?",
         "direction": "NO", "confidence": "MED", "flag_path": "DRIFT",
         "market_price": 0.195, "our_estimate": 0.07, "edge": 0.125,
         "time_horizon": "MONTHLY", "close_time": "2026-08-01T00:00:00Z",
         "is_repeat": True, "repeat_count": 1},
    ]


def _dry_run(output_path: str) -> None:
    """
    Renders both bodies for the SAME synthetic-or-real run (shared now_utc,
    so date/time cannot diverge either), writes the HTML to output_path,
    prints both to stdout, prints a shared-values check, and does NOT call
    send_report / SMTP.
    """
    import json as _json
    from pathlib import Path as _Path

    root = _Path(__file__).parent.parent
    cfg_path = root / "config.json"
    if not cfg_path.exists():
        cfg_path = root / "config.example.json"
    with open(cfg_path, encoding="utf-8") as f:
        config = _json.load(f)

    signals = _synthetic_dry_run_signals()
    run_meta = {
        "markets_scanned":     2583,
        "runtime_ms":          939000,
        "model_used":          config.get("scoring", {}).get("scorer_model", "claude-sonnet-4-6"),
        "signals_generated":   len(signals),
        "high_price_filtered": 0,
        "cost_usd":            0.0,
        "tokens_used":         0,
        "whale_flags":         0,
    }
    now_utc = datetime.now(timezone.utc)  # shared explicitly — see PART A

    text_body = compile_report(signals, [], {}, run_meta, config,
                               new_signals=[], repeat_signals=signals,
                               db_path=None, now_utc=now_utc)
    html_body = render_html(signals, [], {}, run_meta, config,
                            new_signals=[], repeat_signals=signals,
                            db_path=None, now_utc=now_utc)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_body)

    hdr = _header_data(signals, [], run_meta, config,
                       new_signals=[], repeat_signals=signals)
    picks = _rank_top_picks(_qualifying(signals, 1, 0), n=3)

    print("=== TEXT BODY ===")
    print(text_body)
    print()
    print(f"=== HTML BODY written to {output_path} ({len(html_body)} chars, "
          f"{len(html_body.encode('utf-8'))} bytes) ===")
    print()
    print("=== SHARED VALUES CHECK (both bodies rendered from these, in one call each) ===")
    print(f"  New: {hdr['new_count']}  Repeat: {hdr['repeat_count']}  "
          f"Whale: {hdr['whale_count']}  Markets scanned: {hdr['markets_scanned']}")
    print(f"  Top picks: {len(picks)}")
    for p in picks:
        in_text = f"Edge: {p['edge']*100:+.1f} pp" in text_body
        in_html = f"{p['edge']*100:+.1f}" in html_body
        print(f"    {p['ticker']:<40} edge={p['edge']*100:+.1f}pp  "
              f"in_text={in_text}  in_html={in_html}")
    print()
    print("No SMTP call made (--dry-run).")


if __name__ == "__main__":
    import argparse as _argparse

    parser = _argparse.ArgumentParser(description="Leviathan report renderer")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render both bodies, write HTML to a file, print both, "
                             "and exit without sending (no SMTP, no GMAIL_APP_PASSWORD)")
    parser.add_argument("--output", default="dry_run_report.html",
                        help="Path to write the rendered HTML (default: dry_run_report.html)")
    args = parser.parse_args()

    if args.dry_run:
        _dry_run(args.output)
    else:
        parser.print_help()
