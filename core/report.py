import html as _html
import json
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
from core.llm import get_daily_cost_usd, DEFAULT_DAILY_COST_CEILING_USD

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

# ── Subscriber report (GOAL_subscriber_report.md) ──────────────────────────────
# Plain-English mappings for render_subscriber_html(). Kept next to the
# renderer, not exported for reuse elsewhere -- this is presentation copy,
# not a computed value.

SUBSCRIBER_CONVICTION = {"HIGH": "High conviction", "MED": "Medium conviction", "LOW": "Low conviction"}

SUBSCRIBER_WHY_FLAGGED = {
    "DRIFT": (
        "Price drift.",
        "The contract has drifted away from where the evidence points. "
        "The market hasn't repriced yet.",
    ),
    "HEURISTIC": (
        "Historical pattern.",
        "Markets like this one have moved a certain, predictable way often "
        "enough that the current price looks out of step with that history.",
    ),
    "EDGE": (
        "Clear mispricing.",
        "Our estimate differs sharply enough from the market price that the "
        "gap looks like a real opportunity, not noise.",
    ),
    "WATCHLIST": (
        "Smart money.",
        "Traders with a strong track record on a related platform are "
        "actively positioned on this question.",
    ),
    "CROSS_MARKET": (
        "Cross-platform gap.",
        "An equivalent question on another exchange is priced meaningfully "
        "differently than this one.",
    ),
}
_SUBSCRIBER_WHY_FALLBACK = ("Model estimate.", "Our own read of the evidence differs from the market's price.")

# subscriber-report-rework-2026-08: plain-English gloss for a HEURISTIC-flagged
# pick's specific heuristic_label, so "Why flagged" says something more
# specific than a fully generic sentence when we know which pattern actually
# matched (heuristic_label is reliably populated for new signals as of
# db-audit-2026-08's main.py fix -- see BACKLOG.md). Deliberately NOT a
# complete list of every _HEURISTIC_RULES label in core/scanner.py -- falls
# back to the bare label text (already fairly plain English, e.g. "IPO
# announcement") for anything not enumerated here, rather than blocking this
# feature on keeping two files in permanent lockstep.
_SUBSCRIBER_HEURISTIC_GLOSS = {
    "competition win":              "many-entrant competitions",
    "competition/award ranking":    "competition and award questions",
    "entertainment award":          "entertainment award questions",
    "sports award":                 "sports award questions",
    "first named storm":            "“first named storm” questions",
    "hurricane category ladder":    "storm-intensity questions",
    "IPO announcement":             "IPO timing questions",
}


def _subscriber_why_flagged(flag_path: str, heuristic_label: "str | None") -> tuple[str, str]:
    """
    Returns (why_label, why_text) for the "Why flagged" band. Same lookup as
    SUBSCRIBER_WHY_FLAGGED, except the HEURISTIC case is made specific to the
    actual heuristic_label when one is known, instead of a single sentence
    covering every heuristic pattern in the table alike.
    """
    why_label, why_text = SUBSCRIBER_WHY_FLAGGED.get(flag_path, _SUBSCRIBER_WHY_FALLBACK)
    if flag_path == "HEURISTIC" and heuristic_label:
        gloss = _SUBSCRIBER_HEURISTIC_GLOSS.get(heuristic_label, heuristic_label)
        why_text = (
            f"Markets in {gloss} have moved a certain, predictable way often "
            f"enough that the current price looks out of step with that history."
        )
    return why_label, why_text


def _subscriber_corroboration_note(
    call_direction: "str | None",
    whale_detected: bool,
    whale_direction: "str | None",
    smart_money_count: int,
    smart_money_dir: "str | None",
) -> "dict | None":
    """
    subscriber-report-rework-2026-08: surfaces whale/smart-money corroboration
    on subscriber picks and watch items -- previously computed on every
    signal (main.py) but never carried into the subscriber view model at all.

    Deliberately does NOT quote a dollar position size: whale_max_trade_size
    is a contract count relative to that market's own average trade size
    (see core/report.py's existing "Nx average" usage), not a reliable
    dollar figure -- asserting a $ amount here would be a fabricated-precision
    claim the GOAL doc's guardrails exist to prevent. Direction agreement vs.
    conflict against our own call is the reliable, genuinely useful signal.

    call_direction is None for "on the watch" items (no call was made, so
    there is nothing to agree or conflict with) -- states the fact plainly
    instead. Prefers whale data (a concrete single large trade) over the
    smart-money cross-reference (an aggregate of tracked wallets) when both
    are present, rather than combining into one more complex sentence.
    Returns None when neither signal fired.
    """
    if whale_detected and whale_direction in ("YES", "NO"):
        source, direction = "A large trader", whale_direction
    elif smart_money_count > 0 and smart_money_dir in ("YES", "NO"):
        n = smart_money_count
        source = f"{n} historically sharp trader{'s' if n != 1 else ''}"
        direction = smart_money_dir
    else:
        return None

    if call_direction is None:
        text = (f"{source} moved {direction} on this one recently — it "
                 f"hasn't cleared our own bar to call yet.")
    elif direction == call_direction:
        text = f"{source} is positioned {direction} here too — the same side as this call."
    else:
        text = (f"{source} is positioned {direction} here — the opposite "
                 f"side of this call, worth watching closely.")
    return {"label": "Smart money", "text": text}


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
      + Whale:         whale_detected +4 (regardless of ob_flag)
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

    # whale-flag-lv-guarantee (2026-08-04): previously required ob_flag too
    # (a corroboration requirement, not a bug at the time), which meant a
    # whale-only flag never got any LV bonus at all -- with BASE=40 and
    # min_pre_claude_lv=20 in core/scorer.py's pre-Claude gate, a whale flag
    # with weak/negative other signals (e.g. net_edge<=0 and pass_count>=3)
    # could silently drop below the gate and never reach Claude scoring, so
    # it never became a signal, a logged PASS, or a whale_only report row --
    # its only trace was the raw pre-gate count in the run header. Flat +4
    # (matching watchlist_signal's bonus) makes whale_detected alone clear
    # the gate with the same safety margin watchlist already had.
    whale = s.get("whale_data") or {}
    if whale.get("whale_detected"):
        pts += 4

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
    if s.get("liquidity_thin"):        warn.append("[THIN LIQUIDITY]")
    elif s.get("confidence_downgraded"): warn.append("[conf downgraded: edge<10pp]")
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


def _coerce_sources(raw) -> list[dict]:
    """
    `sources` arrives two ways: a live in-memory signal dict from the current
    run already has a real list of {url, title, age} dicts (set by core/llm.py's
    _extract_web_search_sources); a signal read back from the DB (Phase 3's
    `sources TEXT` column) has it JSON-encoded as a string instead, since
    SQLite has no native array type. Accept either so callers -- the harness,
    a future Track Record page, anything reading historical signals -- never
    need to remember which path they're on.
    """
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            parsed = []
        return parsed if isinstance(parsed, list) else []
    return raw or []


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
            # GOAL_subscriber_report.md Phase 1/2: carried through so text/HTML/
            # subscriber renderers share one source of truth, same as every
            # other field above. reasoning is already in a live run's
            # in-memory signal dict (Claude's own response schema includes it)
            # even before Phase 3 persists it to the DB; sources is populated
            # by core/llm.py's _extract_web_search_sources with real structured
            # {url, title, age} entries when a web search actually ran for this
            # signal's scoring call, else empty -- never the freeform
            # sources_checked strings, which must never be rendered as a link.
            "reasoning":     s.get("reasoning", "") or "",
            "sources":       _coerce_sources(s.get("sources")),
            # subscriber-report-rework-2026-08: heuristic_label and the whale/
            # smart-money fields are all computed upstream in scanner.score_market()
            # / main.py's signal dict exactly like every field above, but were
            # never copied into this dict -- same class of gap as main.py's own
            # heuristic_label omission fixed earlier the same day (db-audit-2026-08).
            "heuristic_label":     s.get("heuristic_label"),
            "whale_detected":      bool(s.get("whale_detected")),
            "whale_direction":     s.get("whale_direction"),
            "whale_max_trade_size": s.get("whale_max_trade_size"),
            "smart_money_count":   s.get("smart_money_count") or 0,
            "smart_money_dir":     s.get("smart_money_dir"),
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


# ── Subscriber report renderer (GOAL_subscriber_report.md, Phases 1-2) ─────────
#
# Ported from the Phase 0 harness (scripts/render_subscriber_preview.py),
# which is now a thin wrapper calling this function -- exactly one
# implementation of the subscriber layout, same discipline as every other
# shared renderer function in this file (_rank_top_picks, _betting_queue_data,
# _header_data). Guardrails from the GOAL doc, enforced here: sources_checked
# freeform strings are never rendered as a link (only structured `sources`
# entries -- real web_search_tool_result URLs, core/llm.py's
# _extract_web_search_sources -- ever become an <a href>); no jargon tokens
# (Kelly, EV/ct, flag_path) appear anywhere in the rendered copy.

def _subscriber_fmt_close(close_time_raw: str) -> str:
    """'Aug 2' style -- deliberately different from _close_and_urgency's
    'Closes Aug 2, 2026' (that format is analyst-report jargon-adjacent;
    subscribers get a bare, short date). Blank if unparseable."""
    if not close_time_raw:
        return ""
    try:
        dt = datetime.fromisoformat(close_time_raw.replace("Z", "+00:00"))
        return f"{dt.strftime('%b')} {dt.day}"
    except (ValueError, AttributeError):
        return ""


def _subscriber_pick_view_model(pick: dict) -> dict:
    """Adds the subscriber-facing derived fields on top of _rank_top_picks' output."""
    direction = pick["direction"]
    mp  = float(pick.get("market_price") or 0)
    est = float(pick.get("our_estimate") or 0)
    mkt_pct = round(mp * 100)
    est_pct = round(est * 100)
    gap = abs(est_pct - mkt_pct)
    lo, hi = min(mkt_pct, est_pct), max(mkt_pct, est_pct)
    fp = pick.get("flag_path") or ""
    why_label, why_text = _subscriber_why_flagged(fp, pick.get("heuristic_label"))
    corroboration = _subscriber_corroboration_note(
        call_direction=direction,
        whale_detected=pick.get("whale_detected", False),
        whale_direction=pick.get("whale_direction"),
        smart_money_count=pick.get("smart_money_count", 0),
        smart_money_dir=pick.get("smart_money_dir"),
    )

    url = None
    try:
        url = kalshi_market_url(pick.get("series_ticker"), pick.get("event_ticker"))
    except Exception:
        url = None

    reasoning = (pick.get("reasoning") or "").strip()
    if reasoning:
        analysis = _html.escape(reasoning)
    else:
        # subscriber-report-rework-2026-08: was "Full written analysis renders
        # here once reasoning is persisted per signal" -- an internal
        # implementation note (referencing the DB persistence mechanism)
        # leaking into subscriber-facing copy. This still says nothing was
        # saved for this specific call, but reads like a product, not a TODO.
        analysis = (
            f"We don't have a saved write-up for this one yet — the numbers "
            f"above are the read: the market prices this at {mkt_pct}% while "
            f"our model estimates {est_pct}%, a {gap}-point gap in the "
            f"{direction} direction."
        )

    return {
        **pick,
        "mkt_pct": mkt_pct, "est_pct": est_pct, "gap": gap,
        "fill_left": lo, "fill_width": hi - lo,
        "why_label": why_label, "why_text": why_text,
        "corroboration": corroboration,
        "conviction": SUBSCRIBER_CONVICTION.get(pick.get("confidence", "LOW"), "Low conviction"),
        "tag_class": "tag-yes" if direction == "YES" else "tag-no",
        "tag_label": f"Buy {direction}",
        "close_fmt": _subscriber_fmt_close(pick.get("close_time_raw", "")),
        "kalshi_url": url or "#",
        "question": _html.escape(pick.get("title") or ""),
        "analysis": analysis,
    }


def _render_subscriber_pick(p: dict) -> str:
    # sources_checked (freeform, model self-report) is never linked -- only
    # structured `sources` entries (real web_search_tool_result URLs, core/
    # llm.py's _extract_web_search_sources) ever become an href.
    sources = p.get("sources") or []
    if sources:
        src_html = "\n".join(
            f'    <div class="src-item"><a href="{_html.escape(s.get("url", ""))}">{_html.escape(s.get("title") or s.get("url", ""))}</a></div>'
            for s in sources[:4] if s.get("url")
        )
    else:
        src_html = '    <div class="src-pending">No sources cited for this call.</div>'

    corrob = p.get("corroboration")
    corrob_pill = '\n      <span class="tag tag-whale">Smart money</span>' if corrob else ""
    corrob_band = (
        f'\n\n    <div class="why why-whale">\n'
        f'      <div class="wl">{corrob["label"]}</div>\n'
        f'      <div class="wt">{corrob["text"]}</div>\n'
        f'    </div>'
    ) if corrob else ""

    return f"""
  <article class="pick">
    <div class="pick-head">
      <span class="rank">{p['rank']:02d}</span>
      <span class="tag {p['tag_class']}">{p['tag_label']}</span>
      <span class="tag tag-conf">{p['conviction']}</span>{corrob_pill}
      <span class="resolves">Resolves {p['close_fmt']}</span>
    </div>

    <h2 class="question">{p['question']}</h2>

    <div class="meter">
      <div class="meter-reads">
        <div class="read mkt"><div class="rl">Market price</div><div class="rv">{p['mkt_pct']}%</div></div>
        <div class="read est"><div class="rl">Our estimate</div><div class="rv">{p['est_pct']}%</div></div>
      </div>
      <div class="track">
        <div class="mid"></div>
        <div class="fill" style="left:{p['fill_left']}%; width:{p['fill_width']}%;"></div>
        <div class="tick mkt" style="left:{p['mkt_pct']}%;"></div>
        <div class="tick est" style="left:{p['est_pct']}%;"></div>
      </div>
      <div class="scale"><span>0%</span><span>50%</span><span>100%</span></div>
      <div class="gap-note">The market prices this at {p['mkt_pct']}%. We estimate {p['est_pct']}% — a <span class="big">{p['gap']}-point</span> gap.</div>
    </div>

    <div class="why">
      <div class="wl">Why flagged</div>
      <div class="wt"><b>{p['why_label']}</b> {p['why_text']}</div>
    </div>{corrob_band}

    <p class="analysis">{p['analysis']}</p>

    <div class="src-head">Sources</div>
{src_html}

    <a class="cta" href="{p['kalshi_url']}">Trade on Kalshi <span class="arrow">&rarr;</span></a>
  </article>"""


def _render_subscriber_watch(w: dict) -> str:
    mp  = float(w.get("market_price") or 0)
    est = float(w.get("our_estimate") or 0)
    mkt_pct = round(mp * 100)
    est_pct = round(est * 100)
    close_fmt = _subscriber_fmt_close(w.get("close_time") or w.get("close_time_raw") or "")
    question = _html.escape(w.get("title") or "")

    # subscriber-report-rework-2026-08: watch items are raw signal dicts (not
    # routed through _rank_top_picks), so whale/smart-money fields are
    # already present here -- no carry-through fix needed, unlike picks.
    # call_direction=None: no call was made on a watch item, so there's
    # nothing to agree or conflict with -- states the fact plainly instead.
    corrob = _subscriber_corroboration_note(
        call_direction=None,
        whale_detected=bool(w.get("whale_detected")),
        whale_direction=w.get("whale_direction"),
        smart_money_count=w.get("smart_money_count") or 0,
        smart_money_dir=w.get("smart_money_dir"),
    )
    corrob_pill = '\n      <span class="tag tag-whale">Smart money</span>' if corrob else ""
    corrob_line = f'\n    <div class="watch-note">{corrob["text"]}</div>' if corrob else ""

    return f"""
  <div class="watch">
    <div class="wmeta">
      <span class="tag tag-no">No position</span>{corrob_pill}
      <span class="resolves" style="margin-left:0;">Resolves {close_fmt}</span>
    </div>
    <h3 class="wq">{question}</h3>
    <div class="watch-note">Market's at {mkt_pct}%, we lean {est_pct}%. The edge isn't clean enough to call yet — <b>holding off</b> until the picture sharpens.</div>{corrob_line}
  </div>"""


_SUBSCRIBER_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Leviathan — Intelligence Briefing</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{{
    --paper:#FBFAF7; --ink:#15181E; --ink-soft:#525A67; --ink-faint:#949AA5;
    --line:#E7E4DC; --line-soft:#EFEDE7; --slate:#1C2A3A;
    --edge:#0B6E52; --edge-soft:#E7F0EB; --amber:#9A5A12; --amber-soft:#F4ECDE;
    --whale:#2F4C8C; --whale-soft:#E8ECF5;
    --serif:"Newsreader",Georgia,serif; --sans:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    --mono:"IBM Plex Mono",ui-monospace,Consolas,Menlo,monospace;
    --sp-3:24px; --sp-6:48px;
  }}
  *{{box-sizing:border-box;}}
  body{{margin:0; background:var(--paper); color:var(--ink); font-family:var(--sans);
    font-size:16px; line-height:1.6; -webkit-font-smoothing:antialiased;}}
  a{{color:inherit;}}
  .wrap{{max-width:640px; margin:0 auto; padding:0 28px;}}
  .masthead{{border-top:2px solid var(--ink); padding-top:14px; margin-top:40px;
    display:flex; align-items:baseline; justify-content:space-between;}}
  .wordmark{{font-family:var(--mono); font-size:12px; font-weight:600; letter-spacing:4px; text-transform:uppercase;}}
  .issue{{font-family:var(--mono); font-size:11px; letter-spacing:.5px; color:var(--ink-faint);}}
  .lede{{padding:38px 0 30px; border-bottom:1px solid var(--line);}}
  .lede h1{{font-family:var(--serif); font-weight:500; font-size:34px; line-height:1.12; letter-spacing:-.5px; margin:0;}}
  .lede h1 em{{font-style:italic; color:var(--ink-soft);}}
  .lede .sub{{font-size:14.5px; color:var(--ink-soft); margin-top:12px; max-width:46ch;}}
  .digest{{display:flex; flex-wrap:wrap; gap:26px; padding:20px 0 6px; border-bottom:1px solid var(--line); margin-bottom:var(--sp-6);}}
  .digest .item .n{{font-family:var(--mono); font-size:19px; font-weight:600; letter-spacing:-.5px;}}
  .digest .item .l{{font-family:var(--mono); font-size:10px; letter-spacing:1.4px; text-transform:uppercase; color:var(--ink-faint); margin-top:3px;}}
  .eyebrow{{font-family:var(--mono); font-size:11px; letter-spacing:2.5px; text-transform:uppercase; color:var(--ink-faint); margin:0 0 var(--sp-3); display:flex; align-items:center; gap:12px;}}
  .eyebrow::after{{content:""; flex:1; height:1px; background:var(--line);}}
  .pick{{padding-bottom:var(--sp-6); margin-bottom:var(--sp-6); border-bottom:1px solid var(--line);}}
  .pick:last-of-type{{border-bottom:none;}}
  .pick-head{{display:flex; align-items:center; gap:10px; margin-bottom:18px;}}
  .rank{{font-family:var(--mono); font-size:12px; font-weight:600; color:var(--ink-faint); letter-spacing:1px;}}
  .rank::after{{content:""; display:inline-block; width:16px; height:1px; background:var(--line); vertical-align:middle; margin-left:10px;}}
  .tag{{font-family:var(--mono); font-size:10.5px; font-weight:600; letter-spacing:.5px; padding:4px 10px; border-radius:4px; text-transform:uppercase;}}
  .tag-yes{{color:var(--edge); background:var(--edge-soft);}}
  .tag-no{{color:var(--amber); background:var(--amber-soft);}}
  .tag-conf{{color:var(--ink-soft); background:var(--line-soft); border:1px solid var(--line);}}
  .tag-whale{{color:var(--whale); background:var(--whale-soft);}}
  .resolves{{margin-left:auto; font-family:var(--mono); font-size:11px; color:var(--ink-faint); letter-spacing:.3px;}}
  .question{{font-family:var(--serif); font-weight:500; font-size:25px; line-height:1.28; letter-spacing:-.3px; margin:0 0 24px;}}
  .meter{{margin:0 0 26px;}}
  .meter-reads{{display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:14px;}}
  .read .rl{{font-family:var(--mono); font-size:10px; letter-spacing:1.2px; text-transform:uppercase; color:var(--ink-faint);}}
  .read .rv{{font-family:var(--serif); font-size:30px; font-weight:500; letter-spacing:-1px; line-height:1; margin-top:5px;}}
  .read.est{{text-align:right;}}
  .read.est .rv{{color:var(--edge);}}
  .track{{position:relative; height:4px; background:var(--line); border-radius:3px; margin:4px 0 10px;}}
  .track .mid{{position:absolute; left:50%; top:-4px; bottom:-4px; width:1px; background:var(--line);}}
  .track .fill{{position:absolute; top:0; bottom:0; background:var(--edge); border-radius:3px; opacity:.85;}}
  .track .tick{{position:absolute; top:-5px; width:2px; height:14px; border-radius:2px;}}
  .track .tick.mkt{{background:var(--ink-soft);}}
  .track .tick.est{{background:var(--edge);}}
  .scale{{display:flex; justify-content:space-between; font-family:var(--mono); font-size:9.5px; color:var(--ink-faint); letter-spacing:.5px;}}
  .gap-note{{font-size:15px; color:var(--ink-soft); margin-top:16px; line-height:1.55;}}
  .gap-note b{{color:var(--ink); font-weight:600;}}
  .gap-note .big{{font-family:var(--mono); color:var(--edge); font-weight:600;}}
  .why{{display:flex; gap:14px; align-items:baseline; padding:16px 0; border-top:1px solid var(--line-soft); border-bottom:1px solid var(--line-soft); margin-bottom:22px;}}
  .why .wl{{flex-shrink:0; font-family:var(--mono); font-size:10px; font-weight:600; letter-spacing:1.2px; text-transform:uppercase; color:var(--ink); width:96px; padding-top:2px;}}
  .why .wt{{font-size:14.5px; color:var(--ink-soft); line-height:1.5;}}
  .why-whale{{border-top:none; margin-top:-22px; padding-top:0;}}
  .why-whale .wl{{color:var(--whale);}}
  .why .wt b{{color:var(--ink); font-weight:500;}}
  .analysis{{font-size:16px; line-height:1.68; color:var(--ink); margin-bottom:26px;}}
  .src-head{{font-family:var(--mono); font-size:10px; letter-spacing:1.5px; text-transform:uppercase; color:var(--ink-faint); margin-bottom:2px;}}
  .src-pending{{font-size:13px; color:var(--ink-faint); font-style:italic; padding:12px 0; border-top:1px solid var(--line-soft); border-bottom:1px solid var(--line-soft);}}
  .src-item{{font-size:13.5px; padding:6px 0; border-top:1px solid var(--line-soft);}}
  .src-item a{{color:var(--ink-soft); text-decoration:none; border-bottom:1px solid var(--line);}}
  .cta{{display:inline-flex; align-items:center; gap:8px; text-decoration:none; margin-top:26px; font-family:var(--mono); font-size:13px; font-weight:600; letter-spacing:.5px; color:var(--paper); background:var(--slate); padding:13px 22px; border-radius:6px;}}
  .cta:hover{{background:var(--ink);}}
  .watch{{padding:18px 0 0;}}
  .watch .wq{{font-family:var(--serif); font-size:19px; font-weight:500; line-height:1.3; margin:0 0 8px;}}
  .watch .wmeta{{display:flex; gap:10px; align-items:center; margin-bottom:8px;}}
  .watch-note{{font-size:14.5px; color:var(--ink-soft); line-height:1.55;}}
  .watch-note b{{color:var(--ink); font-weight:600;}}
  .recap-item, .mover{{padding:16px 0; border-top:1px solid var(--line-soft);}}
  .recap-item:first-of-type, .mover:first-of-type{{border-top:none;}}
  .recap-meta, .mover-meta{{display:flex; gap:10px; align-items:center; margin-bottom:6px;}}
  .rq, .mq{{font-family:var(--serif); font-size:17px; font-weight:500; line-height:1.3; margin:0 0 6px;}}
  .recap-note, .mover-note{{font-size:14px; color:var(--ink-soft); line-height:1.5;}}
  .methodology{{margin:var(--sp-6) 0 0; padding-top:22px; border-top:1px solid var(--line);}}
  .methodology p{{font-size:13.5px; color:var(--ink-soft); line-height:1.7; max-width:56ch; margin:8px 0 0;}}
  .foot{{border-top:2px solid var(--ink); margin-top:var(--sp-6); padding:22px 0 60px; font-size:12.5px; color:var(--ink-faint); line-height:1.9;}}
  .foot .discl{{color:var(--ink-soft); max-width:52ch;}}
  .foot a{{color:var(--ink-soft); text-decoration:none; border-bottom:1px solid var(--line);}}
  @media (max-width:520px){{.lede h1{{font-size:28px;}} .question{{font-size:21px;}} .read .rv{{font-size:25px;}} .why{{flex-direction:column; gap:6px;}} .why .wl{{width:auto;}}}}
</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <div class="wordmark">Leviathan</div>
    <div class="issue">{issue_date}</div>
  </header>
  <div class="lede">
    <h1>Where price and<br><em>reality</em> diverge.</h1>
    <div class="sub">A daily read on prediction markets that have drifted from the evidence. The calls we'd act on, and the ones we're still watching.</div>
  </div>
  <div class="digest">
    <div class="item"><div class="n">{n_calls}</div><div class="l">Calls</div></div>
    <div class="item"><div class="n">{n_watch}</div><div class="l">Watching</div></div>
    <div class="item"><div class="n">{markets_scanned}</div><div class="l">Markets scanned</div></div>
    <div class="item"><div class="n">{next_resolve}</div><div class="l">Next to resolve</div></div>
  </div>
  <div class="eyebrow">The calls</div>
{picks_html}
  <div class="eyebrow">On the watch</div>
{watch_html}
  <div class="eyebrow">How last week's calls landed</div>
{recap_html}
  <div class="eyebrow">Market movers</div>
{movers_html}
  <footer class="foot">
    <div class="discl">Leviathan is research, not financial advice. Prediction markets carry risk — bet only what you can afford to lose.</div>
    <div class="methodology">
      <div class="src-head">Methodology</div>
      <p>Estimates come from Claude, cross-referencing live web search, Kalshi's own order book and trade history, Polymarket's prices on the same or related events, and a tracked set of historically sharp Kalshi wallets. Every call above shows the specific sources checked for that market.</p>
    </div>
    <div class="links" style="margin-top:12px;"><a href="{track_record_href}">Full track record</a> &nbsp; <a href="#">Manage subscription</a> &nbsp; <a href="#">Unsubscribe</a></div>
  </footer>
</div>
</body>
</html>
"""


def _render_resolved_recap_item(r: dict) -> str:
    """
    One settled call from the last 7 days -- direction, our estimate, what
    actually happened, and market_drift_pp (Phase 4) if this row has one.
    "How last week's calls landed" -- GOAL doc calls this the biggest trust
    lever, so it shows WINs and LOSSes identically, no cherry-picking.
    """
    direction = (r.get("direction") or "").upper()
    result    = (r.get("result") or "").upper()
    win       = result == "WIN"
    est_pct   = round(float(r.get("our_estimate") or 0) * 100)
    question  = _html.escape(r.get("title") or "")
    outcome   = (r.get("outcome") or "").upper()

    drift = r.get("market_drift_pp")
    drift_note = ""
    if drift is not None:
        toward = "toward us" if drift > 0 else "away from us"
        drift_note = f" Market drifted {abs(drift):.0f}pt {toward} before it settled."

    tag_class = "tag-yes" if win else "tag-no"
    tag_label = "WIN" if win else "LOSS"
    outcome_word = f"Resolved {outcome}." if outcome else "Resolved."

    return f"""
  <div class="recap-item">
    <div class="recap-meta">
      <span class="tag {tag_class}">{tag_label}</span>
      <span class="resolves" style="margin-left:0;">Called {direction} at {est_pct}%</span>
    </div>
    <h4 class="rq">{question}</h4>
    <div class="recap-note">{outcome_word}{drift_note}</div>
  </div>"""


def _resolved_recap_html(resolved_recap: list[dict] | None, top_n: int = 5) -> str:
    rows = (resolved_recap or [])[:top_n]
    if not rows:
        return '\n  <p style="color:var(--ink-faint); font-style:italic;">No calls settled in the last 7 days.</p>'
    return "\n".join(_render_resolved_recap_item(r) for r in rows)


def _mover_reason(s: dict) -> str:
    """Plain-English reason a market made the movers list -- reuses the same
    drift_flag/spread_wide/ob_flag fields the scanner already computes
    (core/scanner.py's compute_drift_signal/compute_spread_signal/
    compute_orderbook_signal), not a new detector."""
    parts = []
    price_drift = s.get("price_drift")
    if s.get("drift_flag") and price_drift is not None:
        pct = abs(price_drift) * 100
        way = "up" if price_drift > 0 else "down"
        parts.append(f"Price moved {pct:.0f}% {way} recently")
    ob_imbalance = s.get("ob_imbalance")
    if s.get("ob_flag") and ob_imbalance is not None:
        ob_dir = s.get("ob_direction") or "one side"
        lean_pct = round(ob_imbalance * 100) if ob_dir == "YES" else round((1 - ob_imbalance) * 100)
        parts.append(f"order book leaning {lean_pct}% toward {ob_dir}")
    spread_pct = s.get("spread_pct")
    if s.get("spread_wide") and spread_pct is not None:
        parts.append(f"unusually wide spread ({spread_pct * 100:.0f}%)")
    return " — ".join(parts) if parts else "Notable market activity."


def _render_market_mover(s: dict) -> str:
    question  = _html.escape(s.get("title") or "")
    close_fmt = _subscriber_fmt_close(s.get("close_time") or s.get("close_time_raw") or "")
    reason    = _mover_reason(s)
    return f"""
  <div class="mover">
    <div class="mover-meta">
      <span class="resolves" style="margin-left:0;">Resolves {close_fmt}</span>
    </div>
    <h4 class="mq">{question}</h4>
    <div class="mover-note">{reason}</div>
  </div>"""


def _market_movers(signals: list[dict], exclude_tickers: set, top_n: int = 3) -> list[dict]:
    """
    Short list of markets showing a structural anomaly (price drift, wide
    spread, or order-book imbalance) that aren't already a call -- "worth
    watching" markets on a different axis than the confidence-based "On the
    watch" section. GOAL_subscriber_report.md Phase 5.
    """
    candidates = []
    for s in signals:
        if s.get("ticker", "") in exclude_tickers:
            continue
        if not (s.get("drift_flag") or s.get("spread_wide") or s.get("ob_flag")):
            continue
        magnitude = max(
            abs(s.get("price_drift") or 0),
            abs(s.get("spread_pct") or 0),
            abs((s.get("ob_imbalance") if s.get("ob_imbalance") is not None else 0.5) - 0.5),
        )
        candidates.append((magnitude, s))
    candidates.sort(key=lambda t: -t[0])
    return [s for _, s in candidates[:top_n]]


def _track_record_href(config: dict) -> str:
    """
    GOAL_phase2-6_decisions.md Decision 3: config.report.base_url is the
    single hosting switch for every cross-page link the digest emits.
    Empty (default) keeps today's relative-path behavior, which only
    resolves when both HTML files sit in the same local folder (the
    harness's own preview use case) -- once real hosting/email delivery
    exists, setting base_url makes this (and any future cross-page link)
    an absolute URL with no other code change.
    """
    base_url = (config.get("report", {}).get("base_url") or "").rstrip("/")
    return f"{base_url}/track_record.html" if base_url else "track_record.html"


def _split_calls_watch(signals: list[dict], config: dict) -> tuple[list[dict], list[dict]]:
    """
    "calls" (direction YES/NO, confidence at or above config.scoring.
    confidence_threshold) vs "watch" (PASS, or below that threshold) --
    matching the GOAL doc's Phase 1 spec exactly, not just literal PASS.
    Shared by render_subscriber_html and determine_subscriber_shortlist so
    there is exactly one definition of "what counts as a published call",
    not two that could drift apart.
    """
    threshold_rank = CONFIDENCE_ORDER.get(
        config.get("scoring", {}).get("confidence_threshold", "MED"), 1
    )
    calls, watch = [], []
    for s in signals:
        direction = s.get("direction", "PASS")
        conf_rank = CONFIDENCE_ORDER.get(s.get("confidence", "LOW"), 2)
        if direction in ("YES", "NO") and conf_rank <= threshold_rank:
            calls.append(s)
        else:
            watch.append(s)
    return calls, watch


def determine_subscriber_shortlist(signals: list[dict], config: dict, n: int = 3) -> list[dict]:
    """
    The original signal dicts (not the rendered pick view-model) for exactly
    the markets render_subscriber_html would publish as calls -- used by
    main.py (GOAL_phase2-6_decisions.md Decision 1) to know which handful of
    markets need a clean, single-market re-score (core.scorer.
    rescore_single_market) before their `sources` are trustworthy as "the
    sources behind THIS pick" rather than a batch-shared list.

    Same calls-filter (_split_calls_watch) and _rank_top_picks ranking
    render_subscriber_html itself uses internally -- one implementation of
    "what gets published", not two that could quietly disagree.
    """
    calls, _watch = _split_calls_watch(signals, config)
    ranked = _rank_top_picks(calls, n=n)
    by_ticker = {s.get("ticker", ""): s for s in calls}
    # _rank_top_picks' dicts are a view (mkt_pct/est_pct/etc), not the
    # original signal -- map back to the real object by ticker so the
    # caller gets something it can mutate (sig["sources"] = ...) and later
    # log. Preserves _rank_top_picks' ranking order, not calls' insertion order.
    return [by_ticker[p["ticker"]] for p in ranked if p["ticker"] in by_ticker]


def render_subscriber_html(
    signals: list[dict],
    run_meta: dict,
    config: dict,
    now_utc: "datetime | None" = None,
    resolved_recap: list[dict] | None = None,
) -> str:
    """
    Subscriber-facing HTML briefing (GOAL_subscriber_report.md Phase 1) --
    plain-English rewrite of the analyst render_html, additive alongside it.
    render_html/compile_report are untouched and still the caller's default
    until main.py is switched deliberately (per the GOAL doc's own guardrail).

    signals: same population render_html/compile_report already receive
    (main.py's final_signals on a live run). Split via _split_calls_watch --
    see determine_subscriber_shortlist for why that split is a shared helper
    rather than duplicated here.

    resolved_recap (Phase 5, optional): settled paper signals from the last
    7 days, e.g. core.logger.get_resolved_track_record(days=7) -- the caller
    queries the DB and passes the result in, keeping this function a pure
    view over its inputs rather than reaching into the DB itself (unlike
    _betting_queue_data). Collapses to an honest placeholder when None/empty,
    same pattern as the calls/watch sections -- never omitted outright.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    calls, watch = _split_calls_watch(signals, config)

    ranked_calls = _rank_top_picks(calls, n=3)
    picks = [_subscriber_pick_view_model(p) for p in ranked_calls]
    watch_sorted = sorted(watch, key=lambda s: -compute_leviathan_score(s))[:3]

    # Must be the SAME 3 calls as `picks` (ranked_calls), not calls[:3] --
    # calls is in scan/flag order, not ranked order, so slicing it directly
    # here could reference a close date for a market that isn't one of the
    # published picks at all whenever more than 3 calls qualify. ranked_calls
    # dicts (from _rank_top_picks) carry close_time_raw; watch_sorted dicts
    # are the original raw signals and carry close_time/expiration_time.
    close_times = [
        ct for ct in (
            [s.get("close_time_raw") for s in ranked_calls]
            + [(s.get("close_time") or s.get("expiration_time")) for s in watch_sorted]
        ) if ct
    ]
    next_resolve = _subscriber_fmt_close(min(close_times)) if close_times else "—"

    picks_html = "\n".join(_render_subscriber_pick(p) for p in picks) if picks else \
        '\n  <p style="color:var(--ink-faint); font-style:italic;">No qualifying calls right now.</p>'
    watch_html = "\n".join(_render_subscriber_watch(w) for w in watch_sorted) if watch_sorted else \
        '\n  <p style="color:var(--ink-faint); font-style:italic;">Nothing on the watch list right now.</p>'

    recap_html = _resolved_recap_html(resolved_recap)

    # Movers are drawn from watch (not calls) so a market never appears
    # twice -- something already shown as a call has no reason to also
    # headline as a "worth watching" mover.
    call_tickers = {p.get("ticker", "") for p in picks}
    movers = _market_movers(watch, exclude_tickers=call_tickers)
    movers_html = "\n".join(_render_market_mover(m) for m in movers) if movers else \
        '\n  <p style="color:var(--ink-faint); font-style:italic;">No unusual market moves outside today\'s calls.</p>'

    return _SUBSCRIBER_TEMPLATE.format(
        issue_date=now_utc.strftime("%d %b %Y").upper(),
        n_calls=len(picks),
        n_watch=len(watch_sorted),
        markets_scanned=f"{run_meta.get('markets_scanned', 0):,}",
        next_resolve=next_resolve,
        picks_html=picks_html,
        watch_html=watch_html,
        recap_html=recap_html,
        movers_html=movers_html,
        track_record_href=_track_record_href(config),
    )


# ── Track Record page (GOAL_subscriber_report.md, Phase 6) ────────────────────
#
# Standing page the digest links to -- unlike render_subscriber_html (a view
# over data the caller already queried), this one queries the DB itself
# (same precedent as _betting_queue_data below) since every number on it is
# always DB-derived, never tied to "the current run". Four-up in order of
# prominence: market drift (CLV) + N, edge realized (ROI/yield) + N, hit
# rate + N, equity curve + N -- every metric paired with its sample size,
# per the doc's explicit guardrail (never print an accuracy/win-rate/drift
# number alone). Publishes the FULL resolved signal log below the headline
# metrics, wins and losses alike -- the doc's own framing: beat the "80%
# asterisk" competitors by being verifiable and honestly sized.

def _metric_card_html(label: str, value: str, n: int, note: str = "", variant: str = "normal") -> str:
    """
    variant (GOAL_phase2-6_decisions.md Choice A): "hero" for market drift
    (the defensible number, given the lead slot both in DOM order and
    visual weight) vs "secondary" for ROI (kept, but explicitly de-
    emphasized -- noisy on this sample size, must never read as the
    headline). "normal" for hit rate / equity curve, same visual weight as
    before this change. Every variant still requires n -- the guardrail is
    about prominence, not about which metrics get a sample size.
    """
    n_note = f"n={n}" if n else "n=0 — no data yet"
    sub = f'<div class="mc-note">{note}</div>' if note else ""
    css_class = {"hero": "mcard mcard-hero", "secondary": "mcard mcard-secondary"}.get(variant, "mcard")
    return f"""
    <div class="{css_class}">
      <div class="mc-label">{label}</div>
      <div class="mc-value">{value}</div>
      <div class="mc-n">{n_note}</div>
      {sub}
    </div>"""


def _equity_sparkline_svg(points: list, is_real: list | None = None) -> str:
    """
    Minimal hand-rolled SVG polyline -- no charting library, this is
    server-rendered static HTML. Empty string (not a broken chart) when
    there are fewer than 2 points to draw a line between.

    Real-fill points get a small solid marker distinguishing them from
    paper/hypothetical points on the same line (GOAL_phase2-6_decisions.md
    Choice B: a curve that silently mixes real and hypothetical dollars is
    the exact "asterisk" move this project means to beat competitors on).
    """
    if len(points) < 2:
        return ""
    is_real = is_real or [False] * len(points)
    w, h, pad = 280, 56, 4
    lo, hi = min(points), max(points)
    rng = (hi - lo) or 1.0
    step = (w - 2 * pad) / (len(points) - 1)
    coords = []
    xy = []
    for i, p in enumerate(points):
        x = pad + i * step
        y = pad + (1 - (p - lo) / rng) * (h - 2 * pad)
        coords.append(f"{x:.1f},{y:.1f}")
        xy.append((x, y))
    color = "var(--edge)" if points[-1] >= 0 else "var(--amber)"
    path = " ".join(coords)
    markers = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="var(--ink)" stroke="var(--paper)" stroke-width="1"/>'
        for (x, y), real in zip(xy, is_real) if real
    )
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" '
        f'preserveAspectRatio="none" style="display:block;margin-top:8px;">'
        f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2"/>'
        f"{markers}"
        f"</svg>"
    )


def _render_track_record_log_row(r: dict) -> str:
    ts        = (r.get("timestamp") or "")[:10]
    direction = _html.escape(r.get("direction") or "")
    title     = _html.escape(r.get("title") or r.get("ticker") or "")
    conf      = _html.escape(r.get("confidence") or "")
    mp        = r.get("market_price")
    est       = r.get("our_estimate")
    mp_s      = f"{mp * 100:.0f}%" if mp is not None else "—"
    est_s     = f"{est * 100:.0f}%" if est is not None else "—"
    result    = (r.get("result") or "").upper()
    pnl       = r.get("pnl_if_traded")
    pnl_s     = f"{pnl:+.2f}" if pnl is not None else "—"
    drift     = r.get("market_drift_pp")
    drift_s   = f"{drift:+.0f}pt" if drift is not None else "—"
    row_class = "tr-win" if result == "WIN" else ("tr-loss" if result == "LOSS" else "")
    return (
        f'<tr class="{row_class}"><td>{ts}</td><td>{title}</td><td>{direction}</td>'
        f"<td>{conf}</td><td>{mp_s}</td><td>{est_s}</td><td>{result}</td>"
        f"<td>{pnl_s}</td><td>{drift_s}</td></tr>"
    )


_TRACK_RECORD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Leviathan — Track Record</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{{
    --paper:#FBFAF7; --ink:#15181E; --ink-soft:#525A67; --ink-faint:#949AA5;
    --line:#E7E4DC; --line-soft:#EFEDE7;
    --edge:#0B6E52; --edge-soft:#E7F0EB; --amber:#9A5A12; --amber-soft:#F4ECDE;
    --serif:"Newsreader",Georgia,serif; --sans:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    --mono:"IBM Plex Mono",ui-monospace,Consolas,Menlo,monospace;
  }}
  *{{box-sizing:border-box;}}
  body{{margin:0; background:var(--paper); color:var(--ink); font-family:var(--sans);
    font-size:16px; line-height:1.6;}}
  .wrap{{max-width:860px; margin:0 auto; padding:0 28px 60px;}}
  h1{{font-family:var(--serif); font-weight:500; font-size:32px; margin:44px 0 6px;}}
  .sub{{color:var(--ink-soft); font-size:14.5px; margin-bottom:8px;}}
  .process-note{{font-size:13px; color:var(--ink-faint); font-style:italic; margin-bottom:30px; max-width:60ch;}}
  .hero-row{{margin-bottom:16px;}}
  .cards{{display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); gap:16px; margin-bottom:40px;}}
  .mcard{{border:1px solid var(--line); border-radius:8px; padding:16px 18px;}}
  .mcard-hero{{border:1px solid var(--edge); border-left:4px solid var(--edge); border-radius:8px; padding:22px 24px; background:var(--edge-soft);}}
  .mcard-hero .mc-label{{color:var(--edge);}}
  .mcard-hero .mc-value{{font-size:38px;}}
  .mcard-secondary{{border:1px solid var(--line); border-radius:8px; padding:12px 16px; opacity:.75;}}
  .mcard-secondary .mc-value{{font-size:19px;}}
  .mc-label{{font-family:var(--mono); font-size:10.5px; letter-spacing:1.2px; text-transform:uppercase; color:var(--ink-faint);}}
  .mc-value{{font-family:var(--serif); font-size:26px; font-weight:500; margin-top:8px;}}
  .mc-n{{font-family:var(--mono); font-size:11px; color:var(--ink-faint); margin-top:4px;}}
  .mc-note{{font-size:12px; color:var(--ink-soft); margin-top:6px;}}
  .eyebrow{{font-family:var(--mono); font-size:11px; letter-spacing:2.5px; text-transform:uppercase; color:var(--ink-faint); margin:0 0 16px; display:flex; align-items:center; gap:12px;}}
  .eyebrow::after{{content:""; flex:1; height:1px; background:var(--line);}}
  table{{width:100%; border-collapse:collapse; font-size:13px;}}
  th{{text-align:left; font-family:var(--mono); font-size:10px; letter-spacing:1px; text-transform:uppercase; color:var(--ink-faint); padding:8px 6px; border-bottom:1px solid var(--line);}}
  td{{padding:8px 6px; border-bottom:1px solid var(--line-soft); font-family:var(--mono); font-size:12.5px;}}
  tr.tr-win td:nth-child(7){{color:var(--edge);}}
  tr.tr-loss td:nth-child(7){{color:var(--amber);}}
  .table-wrap{{overflow-x:auto;}}
  .foot{{border-top:2px solid var(--ink); margin-top:48px; padding:22px 0 20px; font-size:12.5px; color:var(--ink-faint); line-height:1.9;}}
</style>
</head>
<body>
<div class="wrap">
  <h1>Track Record</h1>
  <div class="sub">Every call Leviathan has made, resolved or not, wins and losses alike.</div>
  <div class="process-note">Market drift is a process signal, not a guarantee -- it measures whether the market moved toward our number, independent of whether the coin-flip outcome landed our way. All figures below are sample-size-limited; read the N next to each one before drawing a conclusion.</div>
  <div class="hero-row">
{hero_card_html}
  </div>
  <div class="cards">
{secondary_cards_html}
  </div>
  <div class="eyebrow">Full signal log ({log_n} resolved)</div>
  <div class="table-wrap">
  <table>
    <thead><tr><th>Date</th><th>Market</th><th>Dir</th><th>Conf</th><th>Market %</th><th>Our %</th><th>Result</th><th>PnL/$1</th><th>Drift</th></tr></thead>
    <tbody>
{log_rows_html}
    </tbody>
  </table>
  </div>
  <footer class="foot">
    Leviathan is research, not financial advice. Prediction markets carry risk — bet only what you can afford to lose.
  </footer>
</div>
</body>
</html>
"""


def render_track_record_html(now_utc: "datetime | None" = None) -> str:
    """
    Public Track Record page (GOAL_subscriber_report.md Phase 6). Queries
    the DB directly via core.logger's public stats functions -- every number
    here is DB-derived, never tied to a specific run's in-memory signals.
    """
    from core import logger

    now_utc = now_utc or datetime.now(timezone.utc)

    drift = logger.get_market_drift_stats()
    stats = logger.get_stats()
    equity = logger.get_equity_curve_data()
    full_log = logger.get_resolved_track_record()

    resolved_n = stats.get("resolved") or 0
    total_pnl  = stats.get("total_hypothetical_pnl")
    roi_pct    = (total_pnl / resolved_n * 100) if (total_pnl is not None and resolved_n) else None
    win_rate   = stats.get("win_rate")

    drift_value = f"{drift['avg_drift_pp']:+.1f}pt" if drift.get("avg_drift_pp") is not None else "—"
    drift_note  = (f"{drift['pct_positive_drift']:.0f}% of picks drifted our way"
                   if drift.get("pct_positive_drift") is not None else "")
    roi_value   = f"{roi_pct:+.1f}%" if roi_pct is not None else "—"
    hit_value   = f"{win_rate:.0f}%" if win_rate is not None else "—"
    equity_value = f"{equity['final']:+.2f}" if equity.get("final") is not None else "—"
    equity_note  = _equity_sparkline_svg(equity.get("points") or [], equity.get("is_real") or [])
    real_n, paper_n = equity.get("real_n") or 0, equity.get("paper_n") or 0
    if equity.get("n"):
        # GOAL_phase2-6_decisions.md Choice B: never blend real and paper
        # dollars without disclosure -- the marker on the chart above is the
        # visual half of that, this count is the textual half, always shown
        # together (footnote is never omitted when there's a curve to show).
        equity_note += (
            f'<div style="margin-top:6px;">{real_n} real-money point'
            f'{"s" if real_n != 1 else ""} (solid marker), '
            f'{paper_n} paper (hypothetical)</div>'
        )

    # GOAL_phase2-6_decisions.md Choice A: drift is the defensible number on
    # this sample and stays in the lead/hero slot, both in DOM order and
    # visual weight; ROI is noisy here and must never read as the headline
    # -- kept, but explicitly de-emphasized (mcard-secondary), never promoted
    # to hero. Hit rate / equity curve keep their prior normal weight.
    hero_card_html = _metric_card_html(
        "Market drift toward estimate", drift_value, drift.get("n") or 0, drift_note, variant="hero",
    )
    secondary_cards_html = "\n".join([
        _metric_card_html("Edge realized (ROI per $1)", roi_value, resolved_n, variant="secondary"),
        _metric_card_html("Hit rate", hit_value, resolved_n),
        _metric_card_html("Equity curve (cumulative $1 units)", equity_value, equity.get("n") or 0, equity_note),
    ])

    log_rows_html = "\n".join(_render_track_record_log_row(r) for r in full_log) if full_log else \
        '<tr><td colspan="9" style="text-align:center; color:var(--ink-faint); font-style:italic;">No resolved signals yet.</td></tr>'

    return _TRACK_RECORD_TEMPLATE.format(
        hero_card_html=hero_card_html,
        secondary_cards_html=secondary_cards_html,
        log_n=len(full_log),
        log_rows_html=log_rows_html,
    )


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
    lv_stats=None, db_path=None, now_utc=None, heuristic_label_stats=None,
    whale_stats=None,
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
            # whale-flag-lv-guarantee (2026-08-05 crash): whale_direction is
            # explicitly None (not absent) whenever whales.detect_whale()
            # finds unusual volume/block trades with no clear directional
            # lean (core/whales.py sets whale_direction=None by default,
            # independent of whale_detected) -- dict.get(key, default) only
            # substitutes the default for a MISSING key, not a present key
            # whose value is None, so this crashed _render_table's _cell()
            # (`len(None)`) the first time a whale-only row with no
            # determinable direction survived into the report. Whale flags
            # reaching this table at all got much more likely once
            # whale_detected started guaranteeing the min_pre_claude_lv gate
            # (same commit) instead of silently dropping out beforehand --
            # this exact shape apparently never actually reached this
            # render path before. `or` (not the .get default) is the same
            # None-safe pattern already used for this identical field in
            # _week_whale_rows below.
            _wh_rows.append([
                _trunc(w.get("ticker") or "", 22),
                w.get("whale_direction") or "?",
                ratio,
                _trunc(w.get("title") or "", 32),
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

    # per-heuristic-scorecard: flag_path buckets multiple heuristic rules
    # together (e.g. every HEURISTIC-flagged market shares one flag_path
    # regardless of which of the ~30 named rules in core.scanner matched) --
    # this is the finer-grained breakdown, already computed by
    # get_stats_by_heuristic_label() but never surfaced anywhere besides a
    # manually-run analysis/calibration.py before this.
    if heuristic_label_stats:
        resolved_labels = [r for r in heuristic_label_stats if r.get("total", 0) > 0]
        if resolved_labels:
            out.append("  Win Rate by Heuristic Label  (resolved only):")
            out.append(f"    {'Label':<30}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L':>8}")
            out.append(f"    {'-'*30}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*8}")
            for r in resolved_labels:
                wr_s  = f"{r['win_rate']:.0f}%" if r["win_rate"] is not None else "—"
                pnl_s = f"${r['total_pnl']:.2f}" if r["total_pnl"] is not None else "—"
                label = _trunc(str(r.get("heuristic_label") or "?"), 30)
                out.append(f"    {label:<30}  {r['total']:>5}  {r['wins']:>4}  {wr_s:>6}  {pnl_s:>8}")
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

    # whale-actionability-scorecard: the WHALE ACTIVITY table above just
    # lists sightings (a market a whale traded, no track record attached) --
    # this answers the actual question, "has following whale activity been
    # worth anything," by comparing resolved-signal win rate/P&L for
    # whale-flagged vs non-whale-flagged, the same win-rate-by-bucket
    # pattern already used for flag_path/heuristic_label/LV grade above.
    if whale_stats:
        _w  = whale_stats.get("whale", {})
        _nw = whale_stats.get("no_whale", {})
        if _w.get("total", 0) > 0 or _nw.get("total", 0) > 0:
            out.append("  Win Rate: Whale-Flagged vs Not  (resolved only):")
            out.append(f"    {'Group':<16}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L':>8}  {'AvgEdge':>8}")
            out.append(f"    {'-'*16}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*8}  {'-'*8}")
            for label, d in (("Whale-flagged", _w), ("No whale flag", _nw)):
                if d.get("total", 0) == 0:
                    continue
                wr_s  = f"{d['win_rate']:.0f}%" if d.get("win_rate") is not None else "—"
                pnl_s = f"${d['total_pnl']:.2f}" if d.get("total_pnl") is not None else "—"
                ae_s  = f"{d['avg_edge']*100:.1f}pp" if d.get("avg_edge") is not None else "—"
                out.append(f"    {label:<16}  {d['total']:>5}  {d.get('wins',0):>4}  {wr_s:>6}  {pnl_s:>8}  {ae_s:>8}")
            if _w.get("win_rate") is not None and _nw.get("win_rate") is not None:
                delta = _w["win_rate"] - _nw["win_rate"]
                verdict = "whale flag predicts wins" if delta >= 10 else (
                          "whale flag underperforms -- no signal value shown yet" if delta <= -10 else
                          "no meaningful difference yet")
                out.append(f"    Whale vs no-whale win-rate delta: {delta:+.0f}pp  -> {verdict}")
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
    llm_ceiling = config.get("llm", {}).get("daily_cost_ceiling_usd", DEFAULT_DAILY_COST_CEILING_USD)
    out.append(f"  LLM Daily Spend:   ${get_daily_cost_usd():.2f} / ${float(llm_ceiling):.2f}  (real API, resets daily)")
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
        signals generated <span style="color:#c6cfde;">{run_meta.get('signals_generated', 0)}</span> &nbsp;·&nbsp; filtered (high price) <span style="color:#c6cfde;">{run_meta.get('high_price_filtered', 0)}</span> &nbsp;·&nbsp; model <span style="color:#c6cfde;">{_esc(model)}</span> &nbsp;·&nbsp; cost <span style="color:#c6cfde;">${run_meta.get('cost_usd') or 0:.2f} · Pro</span> &nbsp;·&nbsp; LLM daily spend <span style="color:#c6cfde;">${get_daily_cost_usd():.2f} / ${float(config.get('llm', {}).get('daily_cost_ceiling_usd', DEFAULT_DAILY_COST_CEILING_USD)):.2f}</span>
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

def _week_whale_rows(week_signals: list[dict]) -> list[dict]:
    """
    Whale-flagged markets from this week's signals, deduplicated by ticker
    (latest occurrence wins). EV is computed by assuming the whale's own
    direction rather than Claude's final call — most whale-flagged markets
    end up as a Claude PASS (no confident edge), which would make an
    EV-by-Claude's-direction column empty for nearly every row; showing
    "if you'd followed the whale, using Claude's own probability estimate,
    the EV would be X" is the genuinely informative number, not a
    fabricated one — market_price/our_estimate are the same real,
    already-scored values used everywhere else, only the direction
    assumption changes.

    Position size (whale_max_trade_size) is only available on rows logged
    after the 2026-07-26 log_pass() fix — a prior version hardcoded
    whale_detected=0 for every PASS-logged row, discarding the real value
    before it ever reached the DB. Older rows show "—", never a fabricated
    number.
    """
    by_ticker: dict[str, dict] = {}
    for row in sorted(week_signals, key=lambda r: r.get("timestamp", "")):
        if row.get("whale_detected"):
            by_ticker[row.get("ticker", "")] = row  # latest occurrence wins

    rows = []
    for row in by_ticker.values():
        whale_dir = row.get("whale_direction") or "?"
        ev = _ev_per_contract(whale_dir, row.get("market_price"), row.get("our_estimate"))
        rows.append({
            "ticker":      row.get("ticker", ""),
            "title":       row.get("title", ""),
            "whale_dir":   whale_dir,
            "claude_call": row.get("direction", "?"),
            "confidence":  row.get("confidence", ""),
            "position":    row.get("whale_max_trade_size"),
            "ev":          ev,
        })
    rows.sort(key=lambda r: -(r["position"] or 0))
    return rows


def compile_weekly_digest(week_signals: list[dict], stats: dict, config: dict,
                          flag_path_stats: list | None = None,
                          brier: dict | None = None,
                          lv_stats: dict | None = None,
                          heuristic_label_stats: list | None = None,
                          whale_stats: dict | None = None) -> str:
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

    # Whale activity this week
    out.append(_rule("="))
    out.append("WHALE ACTIVITY THIS WEEK")
    out.append(_rule("="))
    out.append("")
    whale_rows = _week_whale_rows(week_signals)
    if not whale_rows:
        out.append("  No whale-flagged markets this week.")
    else:
        out.append(f"  {'Ticker':<26}  {'Whale':<5}  {'Claude':<6}  {'Conf':<4}  {'Position':>9}  {'EV (whale dir)':>15}  Title")
        out.append(f"  {'-'*26}  {'-'*5}  {'-'*6}  {'-'*4}  {'-'*9}  {'-'*15}  -----")
        for w in whale_rows:
            pos_s = f"{w['position']:.0f}" if w["position"] is not None else "—"
            ev_s  = w["ev"] if w["ev"] is not None else "—"
            out.append(
                f"  {_trunc(w['ticker'], 26, ellipsis=False):<26}  {w['whale_dir']:<5}  "
                f"{w['claude_call']:<6}  {CONF_LABEL.get(w['confidence'], '?'):<4}  "
                f"{pos_s:>9}  {ev_s:>15}  {_trunc(w['title'], 30)}"
            )
        out.append("")
        out.append("  Position = contracts in the whale's largest qualifying trade; blank on rows")
        out.append("  logged before whale position tracking was added (2026-07-26).")
        out.append("  EV assumes the whale's own direction, not Claude's final call — most")
        out.append("  whale-flagged markets end in a Claude PASS, so this is the number that")
        out.append("  actually differs from \"no edge\".")

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

    # per-heuristic-scorecard: see compile_report's identical section for why.
    if heuristic_label_stats:
        resolved_labels = [r for r in heuristic_label_stats if r.get("total", 0) > 0]
        if resolved_labels:
            out.append("  Win Rate by Heuristic Label  (resolved only):")
            out.append(f"    {'Label':<30}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L':>8}")
            out.append(f"    {'-'*30}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*8}")
            for r in resolved_labels:
                wr_s  = f"{r['win_rate']:.0f}%" if r["win_rate"] is not None else "—"
                pnl_s = f"${r['total_pnl']:.2f}" if r["total_pnl"] is not None else "—"
                label = _trunc(str(r.get("heuristic_label") or "?"), 30)
                out.append(f"    {label:<30}  {r['total']:>5}  {r['wins']:>4}  {wr_s:>6}  {pnl_s:>8}")
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

    # whale-actionability-scorecard: see compile_report's identical section for why.
    if whale_stats:
        _w  = whale_stats.get("whale", {})
        _nw = whale_stats.get("no_whale", {})
        if _w.get("total", 0) > 0 or _nw.get("total", 0) > 0:
            out.append("  Win Rate: Whale-Flagged vs Not  (resolved only):")
            out.append(f"    {'Group':<16}  {'Total':>5}  {'Wins':>4}  {'Win%':>6}  {'P&L':>8}  {'AvgEdge':>8}")
            out.append(f"    {'-'*16}  {'-'*5}  {'-'*4}  {'-'*6}  {'-'*8}  {'-'*8}")
            for label, d in (("Whale-flagged", _w), ("No whale flag", _nw)):
                if d.get("total", 0) == 0:
                    continue
                wr_s  = f"{d['win_rate']:.0f}%" if d.get("win_rate") is not None else "—"
                pnl_s = f"${d['total_pnl']:.2f}" if d.get("total_pnl") is not None else "—"
                ae_s  = f"{d['avg_edge']*100:.1f}pp" if d.get("avg_edge") is not None else "—"
                out.append(f"    {label:<16}  {d['total']:>5}  {d.get('wins',0):>4}  {wr_s:>6}  {pnl_s:>8}  {ae_s:>8}")
            if _w.get("win_rate") is not None and _nw.get("win_rate") is not None:
                delta = _w["win_rate"] - _nw["win_rate"]
                verdict = "whale flag predicts wins" if delta >= 10 else (
                          "whale flag underperforms -- no signal value shown yet" if delta <= -10 else
                          "no meaningful difference yet")
                out.append(f"    Whale vs no-whale win-rate delta: {delta:+.0f}pp  -> {verdict}")
            out.append("")

    out.append(_rule("="))
    out.append("Leviathan v1  ·  Weekly Summary  ·  For informational purposes only")
    out.append(_rule("="))

    return "\n".join(out)


def _weekly_market_row_html(row: dict) -> str:
    conf   = CONF_LABEL.get(row.get("confidence", "LOW"), "?")
    dir_   = row.get("direction", "?")
    dir_color = "#3ddc9f" if dir_ == "YES" else ("#f2726b" if dir_ == "NO" else "#8695ac")
    try:
        edge_s = f"{float(row.get('edge', 0))*100:+.1f}pp"
    except Exception:
        edge_s = "—"
    lv = compute_leviathan_score(row)
    band = "A" if lv >= 70 else "B" if lv >= 55 else "C" if lv >= 40 else "D"
    return f'''<tr>
      <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:10px 8px 10px 16px;border-bottom:1px solid #273246;">{_esc(_trunc(row.get("ticker",""), 24, ellipsis=False))}</td>
      <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;font-weight:600;color:{dir_color};padding:10px 8px;border-bottom:1px solid #273246;">{_esc(dir_)}</td>
      <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#9aa7bd;padding:10px 8px;border-bottom:1px solid #273246;">{_esc(conf)}</td>
      <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:10px 8px;border-bottom:1px solid #273246;">{_esc(edge_s)}</td>
      <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:10px 8px;border-bottom:1px solid #273246;">{lv}{band}</td>
      <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#8695ac;padding:10px 16px 10px 8px;border-bottom:1px solid #273246;">{_esc(_trunc(row.get("title") or "", 40))}</td>
    </tr>'''


def _weekly_whale_row_html(w: dict) -> str:
    pos_s = f"{w['position']:.0f}" if w["position"] is not None else "—"
    ev_s  = w["ev"] if w["ev"] is not None else "—"
    whale_dir = w.get("whale_dir", "?")
    dir_color = "#3ddc9f" if whale_dir == "YES" else ("#f2726b" if whale_dir == "NO" else "#8695ac")
    return f'''<tr>
      <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:10px 8px 10px 16px;border-bottom:1px solid #273246;">{_esc(_trunc(w.get("ticker",""), 24, ellipsis=False))}</td>
      <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;font-weight:600;color:{dir_color};padding:10px 8px;border-bottom:1px solid #273246;">{_esc(whale_dir)}</td>
      <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#9aa7bd;padding:10px 8px;border-bottom:1px solid #273246;">{_esc(w.get("claude_call","?"))}</td>
      <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:10px 8px;border-bottom:1px solid #273246;">{_esc(pos_s)}</td>
      <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:10px 8px;border-bottom:1px solid #273246;">{_esc(ev_s)}</td>
      <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#8695ac;padding:10px 16px 10px 8px;border-bottom:1px solid #273246;">{_esc(_trunc(w.get("title") or "", 34))}</td>
    </tr>'''


def render_weekly_html(week_signals: list[dict], stats: dict, config: dict,
                       flag_path_stats: list | None = None,
                       brier: dict | None = None,
                       lv_stats: dict | None = None,
                       now_utc: datetime | None = None,
                       heuristic_label_stats: list | None = None,
                       whale_stats: dict | None = None) -> str:
    """
    Renders the weekly digest as email-safe HTML matching the same visual
    system as render_html() (dark theme, IBM Plex Mono, table-based, inline
    CSS, 600px container) -- same header banner, same color palette, same
    card/table styling. Unlike render_html(), the Track Record section is
    KEPT here rather than dropped: the daily HTML omits it because Power BI
    already covers that ground for daily use, but the weekly digest's whole
    purpose IS a track-record-style summary, so it stays.

    Every number here comes from the exact same inputs/computations
    compile_weekly_digest() uses (_week_whale_rows, compute_leviathan_score,
    _ev_per_contract) -- the text and HTML bodies of one weekly email can
    never show different numbers for the same week.
    """
    now_utc  = now_utc or datetime.now(timezone.utc)
    date_str = now_utc.strftime("%B %d, %Y")
    env      = config.get("environment", "prod").upper()

    by_ticker: dict[str, dict] = {}
    for row in week_signals:
        t = row.get("ticker", "")
        if t not in by_ticker:
            by_ticker[t] = row
    unique_markets = list(by_ticker.values())
    n_calls = len(week_signals)
    n_mkts  = len(unique_markets)
    n_yes   = sum(1 for r in unique_markets if r.get("direction") == "YES")
    n_no    = sum(1 for r in unique_markets if r.get("direction") == "NO")
    n_high  = sum(1 for r in unique_markets if r.get("confidence") == "HIGH")

    whale_rows = _week_whale_rows(week_signals)
    whale_rows_html = "".join(_weekly_whale_row_html(w) for w in whale_rows) if whale_rows else (
        '<tr><td colspan="6" style="padding:16px;color:#8695ac;" class="plex">'
        'No whale-flagged markets this week.</td></tr>'
    )

    market_rows = sorted(unique_markets, key=lambda r: r.get("timestamp", ""), reverse=True)
    market_rows_html = "".join(_weekly_market_row_html(r) for r in market_rows) if market_rows else (
        '<tr><td colspan="6" style="padding:16px;color:#8695ac;" class="plex">'
        'No markets flagged this week.</td></tr>'
    )

    wr  = stats.get("win_rate")
    ae  = stats.get("avg_edge_captured")
    pnl = stats.get("total_hypothetical_pnl")
    wr_s  = f"{wr:.1f}%" if wr is not None else "—"
    ae_s  = _pct(ae) if ae is not None else "—"
    pnl_s = f"${pnl:.2f}" if pnl is not None else "—"
    brier_s = "PENDING"
    if brier and brier.get("brier_score") is not None:
        brier_s = f"{brier['brier_score']:.4f} ({brier.get('label','')}, n={brier.get('n',0)})"

    flag_rows_html = ""
    if flag_path_stats:
        resolved_paths = [r for r in flag_path_stats if r.get("total", 0) > 0]
        for r in resolved_paths:
            wr_p = f"{r['win_rate']:.0f}%" if r["win_rate"] is not None else "—"
            pnl_p = f"${r['total_pnl']:.2f}" if r["total_pnl"] is not None else "—"
            flag_rows_html += f'''<tr>
              <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px 8px 8px 16px;border-bottom:1px solid #273246;">{_esc(r['flag_path'])}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px;border-bottom:1px solid #273246;">{r['total']}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px;border-bottom:1px solid #273246;">{r['wins']}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px;border-bottom:1px solid #273246;">{_esc(wr_p)}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px 16px 8px 8px;border-bottom:1px solid #273246;">{_esc(pnl_p)}</td>
            </tr>'''

    flag_section_html = ""
    if flag_rows_html:
        flag_section_html = f'''
    <tr><td height="30" style="font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td class="px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#f2f5fa;white-space:nowrap;padding-right:14px;">Win Rate by Signal Path</td>
        <td width="100%" style="border-bottom:1px solid #273246;">&nbsp;</td>
      </tr></table>
    </td></tr>
    <tr><td height="16" style="font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0f1521" style="background-color:#0f1521;border:1px solid #273246;border-radius:10px;">
        <tr bgcolor="#151d2c" style="background-color:#151d2c;">
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px 11px 16px;border-bottom:1px solid #273246;">Path</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Total</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Wins</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Win%</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 16px 11px 8px;border-bottom:1px solid #273246;">P&amp;L</td>
        </tr>
        {flag_rows_html}
      </table>
    </td></tr>'''

    # per-heuristic-scorecard: same rationale as compile_report's identical section.
    heuristic_rows_html = ""
    if heuristic_label_stats:
        resolved_labels = [r for r in heuristic_label_stats if r.get("total", 0) > 0]
        for r in resolved_labels:
            wr_p = f"{r['win_rate']:.0f}%" if r["win_rate"] is not None else "—"
            pnl_p = f"${r['total_pnl']:.2f}" if r["total_pnl"] is not None else "—"
            heuristic_rows_html += f'''<tr>
              <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px 8px 8px 16px;border-bottom:1px solid #273246;">{_esc(_trunc(str(r.get("heuristic_label") or "?"), 30))}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px;border-bottom:1px solid #273246;">{r['total']}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px;border-bottom:1px solid #273246;">{r['wins']}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px;border-bottom:1px solid #273246;">{_esc(wr_p)}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px 16px 8px 8px;border-bottom:1px solid #273246;">{_esc(pnl_p)}</td>
            </tr>'''

    heuristic_section_html = ""
    if heuristic_rows_html:
        heuristic_section_html = f'''
    <tr><td height="30" style="font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td class="px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#f2f5fa;white-space:nowrap;padding-right:14px;">Win Rate by Heuristic Label</td>
        <td width="100%" style="border-bottom:1px solid #273246;">&nbsp;</td>
      </tr></table>
    </td></tr>
    <tr><td height="16" style="font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0f1521" style="background-color:#0f1521;border:1px solid #273246;border-radius:10px;">
        <tr bgcolor="#151d2c" style="background-color:#151d2c;">
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px 11px 16px;border-bottom:1px solid #273246;">Label</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Total</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Wins</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Win%</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 16px 11px 8px;border-bottom:1px solid #273246;">P&amp;L</td>
        </tr>
        {heuristic_rows_html}
      </table>
    </td></tr>'''

    # whale-actionability-scorecard: see compile_report's identical section for why.
    whale_stat_rows_html = ""
    if whale_stats:
        _w  = whale_stats.get("whale", {})
        _nw = whale_stats.get("no_whale", {})
        for label, d in (("Whale-flagged", _w), ("No whale flag", _nw)):
            if d.get("total", 0) == 0:
                continue
            wr_p = f"{d['win_rate']:.0f}%" if d.get("win_rate") is not None else "—"
            pnl_p = f"${d['total_pnl']:.2f}" if d.get("total_pnl") is not None else "—"
            ae_p = f"{d['avg_edge']*100:.1f}pp" if d.get("avg_edge") is not None else "—"
            whale_stat_rows_html += f'''<tr>
              <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px 8px 8px 16px;border-bottom:1px solid #273246;">{_esc(label)}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px;border-bottom:1px solid #273246;">{d['total']}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px;border-bottom:1px solid #273246;">{d.get('wins',0)}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px;border-bottom:1px solid #273246;">{_esc(wr_p)}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px;border-bottom:1px solid #273246;">{_esc(pnl_p)}</td>
              <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11px;color:#c6cfde;padding:8px 16px 8px 8px;border-bottom:1px solid #273246;">{_esc(ae_p)}</td>
            </tr>'''

    whale_stat_section_html = ""
    if whale_stat_rows_html:
        whale_stat_section_html = f'''
    <tr><td height="30" style="font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td class="px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#f2f5fa;white-space:nowrap;padding-right:14px;">Win Rate: Whale-Flagged vs Not</td>
        <td width="100%" style="border-bottom:1px solid #273246;">&nbsp;</td>
      </tr></table>
    </td></tr>
    <tr><td height="16" style="font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0f1521" style="background-color:#0f1521;border:1px solid #273246;border-radius:10px;">
        <tr bgcolor="#151d2c" style="background-color:#151d2c;">
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px 11px 16px;border-bottom:1px solid #273246;">Group</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Total</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Wins</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Win%</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">P&amp;L</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 16px 11px 8px;border-bottom:1px solid #273246;">AvgEdge</td>
        </tr>
        {whale_stat_rows_html}
      </table>
    </td></tr>'''

    preheader = f"{n_mkts} markets flagged · {len(whale_rows)} whale flags · win rate {wr_s} this week"

    html_doc = f'''<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta name="color-scheme" content="dark light">
<meta name="supported-color-schemes" content="dark light">
<title>Leviathan — Weekly Digest</title>
<!--[if mso]><noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript><![endif]-->
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&display=swap');
  body,table,td{{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;}}
  a{{color:#84b6fb;}}
  .plex{{font-family:'IBM Plex Mono','SFMono-Regular',ui-monospace,Consolas,Menlo,monospace !important;}}
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
            <td align="right" class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:10px;font-weight:500;letter-spacing:3px;color:#aab6ca;text-transform:uppercase;">Weekly&nbsp;Digest</td>
          </tr></table>
        </td></tr>
        <tr><td class="px" style="padding:16px 28px 0;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
            <td width="50" height="2" bgcolor="#4a90f2" style="background-color:#4a90f2;font-size:0;line-height:0;">&nbsp;</td>
            <td height="2" bgcolor="#273246" style="background-color:#273246;font-size:0;line-height:0;">&nbsp;</td>
          </tr></table>
        </td></tr>
        <tr><td class="px plex" style="padding:15px 28px 24px;font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:11.5px;color:#aeb9cd;line-height:1.7;">
          <span style="color:#3ddc9f;">●</span> <span style="color:#f2f5fa;">{_esc(env)}</span>&nbsp;&nbsp;·&nbsp;&nbsp;Week ending <span style="color:#f2f5fa;">{_esc(date_str)}</span>
        </td></tr>
      </table>
    </td></tr>

    <tr><td height="18" style="font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- SUMMARY -->
    <tr><td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#273246" style="background-color:#273246;border:1px solid #273246;border-radius:12px;">
        <tr>
          <td class="stack" width="25%" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-radius:12px 0 0 0;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Markets</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:20px;font-weight:600;color:#f2f5fa;padding-top:4px;">{n_mkts}</div>
          </td>
          <td class="stack" width="25%" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-left:1px solid #273246;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Instances</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:20px;font-weight:600;color:#f2f5fa;padding-top:4px;">{n_calls}</div>
          </td>
          <td class="stack" width="25%" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-left:1px solid #273246;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Yes / No</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:20px;font-weight:600;color:#f2f5fa;padding-top:4px;">{n_yes}/{n_no}</div>
          </td>
          <td class="stack" width="25%" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-left:1px solid #273246;border-radius:0 12px 0 0;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Whale Flags</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:20px;font-weight:600;color:#f2f5fa;padding-top:4px;">{len(whale_rows)}</div>
          </td>
        </tr>
      </table>
    </td></tr>

    <tr><td height="34" style="font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- WHALE ACTIVITY -->
    <tr><td class="px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#f2f5fa;white-space:nowrap;padding-right:14px;">Whale Activity This Week</td>
        <td width="100%" style="border-bottom:1px solid #273246;">&nbsp;</td>
      </tr></table>
    </td></tr>
    <tr><td height="16" style="font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0f1521" style="background-color:#0f1521;border:1px solid #273246;border-radius:10px;">
        <tr bgcolor="#151d2c" style="background-color:#151d2c;">
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px 11px 16px;border-bottom:1px solid #273246;">Ticker</td>
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Whale</td>
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Claude</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Position</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">EV</td>
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 16px 11px 8px;border-bottom:1px solid #273246;">Market</td>
        </tr>
        {whale_rows_html}
      </table>
    </td></tr>
    <tr><td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:10.5px;color:#8695ac;padding:10px 2px 0;line-height:1.6;">EV assumes the whale's own direction, not Claude's final call — most whale-flagged markets end in a PASS, so this is the number that actually differs from "no edge". Position blank on rows logged before whale position tracking was added.</td></tr>

    <tr><td height="34" style="font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- MARKETS FLAGGED -->
    <tr><td class="px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#f2f5fa;white-space:nowrap;padding-right:14px;">Markets Flagged This Week</td>
        <td width="100%" style="border-bottom:1px solid #273246;">&nbsp;</td>
      </tr></table>
    </td></tr>
    <tr><td height="16" style="font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0f1521" style="background-color:#0f1521;border:1px solid #273246;border-radius:10px;">
        <tr bgcolor="#151d2c" style="background-color:#151d2c;">
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px 11px 16px;border-bottom:1px solid #273246;">Ticker</td>
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Dir</td>
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Conf</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">Edge</td>
          <td class="plex" align="right" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 8px;border-bottom:1px solid #273246;">LV</td>
          <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;letter-spacing:.5px;text-transform:uppercase;color:#9aa7bd;padding:11px 16px 11px 8px;border-bottom:1px solid #273246;">Market</td>
        </tr>
        {market_rows_html}
      </table>
    </td></tr>

    <tr><td height="34" style="font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- TRACK RECORD -->
    <tr><td class="px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#f2f5fa;white-space:nowrap;padding-right:14px;">Track Record (All-Time)</td>
        <td width="100%" style="border-bottom:1px solid #273246;">&nbsp;</td>
      </tr></table>
    </td></tr>
    <tr><td height="16" style="font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#273246" style="background-color:#273246;border:1px solid #273246;border-radius:12px;">
        <tr>
          <td class="stack" width="33.33%" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-radius:12px 0 0 0;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Win Rate</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:20px;font-weight:600;color:#f2f5fa;padding-top:4px;">{_esc(wr_s)}</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9px;color:#8695ac;padding-top:3px;">{stats.get('resolved', 0)} resolved / {stats.get('total_calls', 0)} calls</div>
          </td>
          <td class="stack" width="33.33%" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-left:1px solid #273246;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Avg Edge</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:20px;font-weight:600;color:#f2f5fa;padding-top:4px;">{_esc(ae_s)}</div>
          </td>
          <td class="stack" width="33.33%" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-left:1px solid #273246;border-radius:0 12px 0 0;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Hypo P&amp;L</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:20px;font-weight:600;color:#f2f5fa;padding-top:4px;">{_esc(pnl_s)}</div>
          </td>
        </tr>
        <tr>
          <td colspan="3" bgcolor="#0f1521" style="background-color:#0f1521;padding:15px 18px;border-top:1px solid #273246;border-radius:0 0 12px 12px;">
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:#93a1b8;">Brier Score</div>
            <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:15px;font-weight:500;color:#c6cfde;padding-top:5px;">{_esc(brier_s)}</div>
          </td>
        </tr>
      </table>
    </td></tr>
    {flag_section_html}
    {heuristic_section_html}
    {whale_stat_section_html}

    <tr><td height="30" style="font-size:0;line-height:0;">&nbsp;</td></tr>

    <!-- FOOTER -->
    <tr><td class="px" style="border-top:1px solid #273246;padding-top:18px;">
      <div class="plex" style="font-family:'IBM Plex Mono',ui-monospace,Consolas,Menlo,monospace;font-size:10px;color:#66738a;letter-spacing:1px;">LEVIATHAN // PREDICTION-MARKET INTELLIGENCE · WEEKLY SUMMARY</div>
    </td></tr>

  </table>
</td></tr>
</table>
</body>
</html>
'''
    return html_doc


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
