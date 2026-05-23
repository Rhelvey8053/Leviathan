import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

# ── Design tokens ─────────────────────────────────────────────────────────────
BG       = "#0d0f14"
SURFACE  = "#141921"
SURFACE2 = "#1a2235"
BORDER   = "#1e2a3c"
TEXT     = "#e2e8f0"
TEXT2    = "#6b7e96"
TEXT3    = "#2d3f52"
BRAND    = "#7c6af7"
C_HIGH   = "#34d399"
C_MED    = "#fbbf24"
C_LOW    = "#f87171"
C_YES    = "#34d399"
C_NO     = "#f87171"

CONFIDENCE_ORDER = {"HIGH": 0, "MED": 1, "LOW": 2}
CONF_COLOR = {"HIGH": C_HIGH, "MED": C_MED, "LOW": C_LOW}
CONF_LABEL = {"HIGH": "HIGH", "MED": "MED", "LOW": "LOW"}


# ── Formatters ────────────────────────────────────────────────────────────────

def _fmt_pct(value) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_usd(value) -> str:
    try:
        return f"${float(value):.4f}"
    except (TypeError, ValueError):
        return "—"


# ── Plain-text fallback ───────────────────────────────────────────────────────

def _qualifying(signals: list[dict], threshold_rank: int) -> list[dict]:
    out = [
        s for s in signals
        if CONFIDENCE_ORDER.get(s.get("confidence", "LOW"), 2) <= threshold_rank
        and s.get("direction", "PASS") != "PASS"
    ]
    out.sort(key=lambda s: (
        CONFIDENCE_ORDER.get(s.get("confidence", "LOW"), 2),
        -(abs(float(s.get("edge") or 0)))
    ))
    return out


def _signal_parts(s: dict) -> list[str]:
    parts = []
    if s.get("drift_flag"):
        parts.append(f"drift {(s.get('price_drift') or 0)*100:+.1f}%")
    if s.get("spread_wide"):
        parts.append(f"spread {(s.get('spread_pct') or 0)*100:.1f}%")
    if s.get("whale_reversal"):
        parts.append("whale reversal")
    if s.get("ob_flag"):
        parts.append(f"ob {(s.get('ob_imbalance') or 0)*100:.0f}% {s.get('ob_direction','?')}")
    poly = s.get("poly")
    if poly and poly.get("price_gap") and abs(poly["price_gap"]) >= 0.05:
        gap = poly["price_gap"] * 100
        parts.append(f"poly gap {gap:+.1f}%")
    if s.get("smart_money"):
        parts.append(f"{len(s['smart_money'])} smart wallet(s)")
    return parts


def compile_report(signals, whale_only, stats, run_meta, config) -> str:
    threshold_rank = CONFIDENCE_ORDER.get(
        config.get("scoring", {}).get("confidence_threshold", "MED"), 1
    )
    date_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    qualifying = _qualifying(signals, threshold_rank)

    lines = [
        f"LEVIATHAN — {date_str}",
        f"{len(qualifying)} signal(s)  |  {len(whale_only)} whale flag(s)",
        "=" * 60, "",
        "SIGNALS",
    ]

    if not qualifying:
        lines.append("  No qualifying signals this run.")
    else:
        for s in qualifying:
            conf      = s.get("confidence", "LOW")
            direction = s.get("direction", "")
            sig_parts = _signal_parts(s)
            lines += [
                "",
                f"  [{CONF_LABEL.get(conf, conf)}] BUY {direction}",
                f"  {s.get('ticker', '')} — {s.get('title', '')}",
                f"  Market: {_fmt_pct(s.get('market_price'))}  Estimate: {_fmt_pct(s.get('our_estimate'))}  Edge: {_fmt_pct(s.get('edge'))}",
            ]
            if sig_parts:
                lines.append(f"  Signals: {', '.join(sig_parts)}")
            if s.get("whale_detected"):
                lines.append(f"  Whale: buying {s.get('whale_direction', '?')}")
            lines.append(f"  {s.get('reasoning', '')}")
            if s.get("sources_checked"):
                lines.append(f"  Sources: {', '.join(s['sources_checked'][:3])}")

    lines += ["", "WHALE ACTIVITY (no signal)"]
    lines += ["  None."] if not whale_only else [
        f"  {w.get('ticker','')} — {w.get('title','')} — {w.get('whale_direction','?')}"
        for w in whale_only
    ]

    wr  = stats.get("win_rate")
    ae  = stats.get("avg_edge_captured")
    pnl = stats.get("total_hypothetical_pnl")
    lines += [
        "", "TRACK RECORD",
        f"  Calls: {stats.get('total_calls',0)}  Resolved: {stats.get('resolved',0)}  "
        f"Win rate: {f'{wr:.1f}%' if wr is not None else '—'}",
        f"  Avg edge: {_fmt_pct(ae) if ae is not None else '—'}  "
        f"Hypo P&L: {f'${pnl:.2f}' if pnl is not None else '—'}",
        "", "RUN STATS",
        f"  Markets: {run_meta.get('markets_scanned',0)}  Signals: {run_meta.get('signals_generated',0)}  "
        f"Cost: {_fmt_usd(run_meta.get('cost_usd'))}  Runtime: {run_meta.get('runtime_ms',0)/1000:.1f}s",
    ]

    return "\n".join(lines)


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _pill(text: str, color: str, bg: str = "") -> str:
    bg = bg or color + "1a"  # 10% opacity background from color
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:4px;'
        f'background:{bg};color:{color};font-size:10px;font-weight:700;'
        f'letter-spacing:0.8px;white-space:nowrap;border:1px solid {color}40">'
        f'{text}</span>'
    )


def _dot(color: str) -> str:
    return f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{color};margin-right:6px;vertical-align:middle"></span>'


def _section_header(title: str) -> str:
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:32px 0 16px">'
        f'<tr>'
        f'<td style="font-size:10px;font-weight:700;color:{TEXT2};text-transform:uppercase;'
        f'letter-spacing:2px;white-space:nowrap;padding-right:16px">{title}</td>'
        f'<td style="border-top:1px solid {BORDER};width:100%"></td>'
        f'</tr>'
        f'</table>'
    )


def _signal_pills(s: dict) -> str:
    pills = []
    if s.get("drift_flag"):
        drift = (s.get("price_drift") or 0) * 100
        pills.append(_pill(f"DRIFT {drift:+.0f}%", "#a78bfa"))
    if s.get("spread_wide"):
        spread = (s.get("spread_pct") or 0) * 100
        pills.append(_pill(f"SPREAD {spread:.0f}%", "#38bdf8"))
    if s.get("whale_reversal"):
        pills.append(_pill("WHALE REVERSAL", "#fb923c"))
    if s.get("ob_flag"):
        ob_dir = s.get("ob_direction", "")
        pills.append(_pill(f"OB → {ob_dir}", "#22d3ee"))
    if s.get("smart_money"):
        pills.append(_pill("SMART MONEY", "#e879f9"))
    # Cross-market pill: count all external sources with meaningful gaps
    n_cross = len([e for e in (s.get("ext_markets") or []) if abs(e.get("price_gap") or 0) >= 0.04])
    if s.get("poly") and abs((s.get("poly") or {}).get("price_gap") or 0) >= 0.04:
        n_cross += 1
    if n_cross:
        pills.append(_pill(f"CROSS ×{n_cross}", "#34d399"))
    return "&nbsp;".join(pills)


HORIZON_STYLE = {
    "INTRADAY":  ("#f97316", "⚡"),   # orange / lightning
    "WEEKLY":    ("#fbbf24", "📅"),   # amber
    "MONTHLY":   ("#60a5fa", "📆"),   # blue
    "QUARTERLY": ("#a78bfa", "🗓"),   # violet
    "LONG":      ("#94a3b8", "📊"),   # slate
}


def _signal_card(s: dict) -> str:
    conf      = s.get("confidence", "LOW")
    direction = s.get("direction", "")
    conf_col  = CONF_COLOR.get(conf, TEXT2)
    dir_col   = C_YES if direction == "YES" else C_NO
    pills     = _signal_pills(s)
    ticker    = s.get("ticker", "")
    title     = s.get("title", "")
    horizon   = s.get("time_horizon", "MONTHLY")
    h_col, h_icon = HORIZON_STYLE.get(horizon, ("#94a3b8", ""))
    close_raw = s.get("close_time") or s.get("expiration_time", "")
    close_fmt = ""
    if close_raw:
        try:
            dt = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
            close_fmt = dt.strftime("Closes %b %-d, %Y")
        except Exception:
            close_fmt = close_raw[:10]

    market_p = _fmt_pct(s.get("market_price"))
    estimate = _fmt_pct(s.get("our_estimate"))
    edge_val = s.get("edge")
    edge_str = _fmt_pct(edge_val)
    edge_col = C_YES if (edge_val or 0) > 0 else C_NO

    reasoning = s.get("reasoning", "")
    sources   = s.get("sources_checked") or []

    # ── Inner blocks ──────────────────────────────────────────────────────────

    # Cross-market block (Manifold + PredictIt + Polymarket combined)
    ext_markets  = s.get("ext_markets") or []
    ext_cons     = s.get("ext_consensus") or {}
    poly         = s.get("poly")

    all_sources = []
    if poly and poly.get("price_gap") is not None:
        all_sources.append({
            "source":      "Polymarket",
            "probability": poly.get("poly_price", 0),
            "price_gap":   poly.get("price_gap", 0),
            "title":       poly.get("poly_question", ""),
            "match_score": poly.get("match_score", 0),
        })
    for e in ext_markets[:3]:
        all_sources.append(e)

    cross_block = ""
    if all_sources:
        source_rows = ""
        for src in all_sources:
            gap     = src.get("price_gap", 0) * 100
            prob    = src.get("probability", 0) * 100
            g_col   = C_YES if gap > 0 else (C_NO if gap < 0 else TEXT2)
            g_label = f"{gap:+.1f}%"
            name    = src.get("source", "")
            badge_col = {
                "Polymarket": "#60a5fa",
                "Manifold":   "#818cf8",
                "PredictIt":  "#fb7185",
            }.get(name, TEXT2)
            source_rows += (
                f'<tr>'
                f'<td style="padding:7px 0;border-bottom:1px solid {BORDER};white-space:nowrap">'
                f'<span style="font-size:10px;font-weight:700;color:{badge_col};background:{badge_col}18;'
                f'padding:2px 8px;border-radius:3px;border:1px solid {badge_col}40">{name}</span></td>'
                f'<td style="padding:7px 8px;border-bottom:1px solid {BORDER};font-size:13px;color:{TEXT2};max-width:220px">'
                f'{src.get("title","")[:55]}</td>'
                f'<td style="padding:7px 0;border-bottom:1px solid {BORDER};font-size:15px;font-weight:700;color:{TEXT};white-space:nowrap">{prob:.1f}%</td>'
                f'<td style="padding:7px 0 7px 10px;border-bottom:1px solid {BORDER};font-size:12px;font-weight:700;color:{g_col};white-space:nowrap">{g_label}</td>'
                f'</tr>'
            )

        cons_html = ""
        if ext_cons.get("consensus_dir") and len(all_sources) > 1:
            avg_p  = (ext_cons.get("avg_ext_price") or 0) * 100
            cgap   = (ext_cons.get("consensus_gap")  or 0) * 100
            c_dir  = ext_cons["consensus_dir"]
            c_col  = C_YES if c_dir == "YES" else C_NO
            high   = ext_cons.get("sources_higher", 0)
            low    = ext_cons.get("sources_lower",  0)
            cons_html = (
                f'<tr><td colspan="4" style="padding:10px 0 0">'
                f'<div style="background:{SURFACE};border-radius:5px;padding:8px 12px;'
                f'border:1px solid {c_col}30">'
                f'<span style="font-size:10px;font-weight:700;color:{c_col}">CONSENSUS</span>'
                f'<span style="font-size:12px;color:{TEXT2};margin-left:8px">'
                f'{high} source{"s" if high!=1 else ""} higher · {low} lower · avg {avg_p:.1f}% ({cgap:+.1f}%)</span>'
                f'</div>'
                f'</td></tr>'
            )

        cross_block = (
            f'<table width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin-top:14px;background:{SURFACE2};border-radius:6px;border:1px solid {BORDER}">'
            f'<tr><td style="padding:12px 16px">'
            f'<div style="font-size:9px;font-weight:700;color:{TEXT2};text-transform:uppercase;'
            f'letter-spacing:1.5px;margin-bottom:10px">Cross-Market</div>'
            f'<table width="100%" cellpadding="0" cellspacing="0">'
            f'{source_rows}{cons_html}'
            f'</table>'
            f'</td></tr></table>'
        )

    poly_block = ""  # superseded by cross_block which includes Polymarket

    smart_money = s.get("smart_money") or []
    sm_block = ""
    if smart_money:
        rows = ""
        yes_sm = [m for m in smart_money if m.get("direction") == "YES"]
        no_sm  = [m for m in smart_money if m.get("direction") == "NO"]
        for sm in smart_money[:4]:
            d_col = C_YES if sm.get("direction") == "YES" else C_NO
            rows += (
                f'<tr>'
                f'<td style="padding:5px 0;font-size:11px;font-family:monospace;color:{TEXT3}">{sm.get("address","")}</td>'
                f'<td style="padding:5px 8px;font-size:11px;font-weight:700;color:{d_col}">BUY {sm.get("direction","?")}</td>'
                f'<td style="padding:5px 0;font-size:11px;color:{TEXT2}">+{sm.get("avg_pct_pnl",0):.0f}% port</td>'
                f'<td style="padding:5px 0 5px 8px;font-size:11px;color:{TEXT3}">{sm.get("trade_count",0)} trades</td>'
                f'</tr>'
            )
        sm_summary = ""
        if yes_sm and no_sm:
            sm_summary = f'{len(yes_sm)} buying YES · {len(no_sm)} buying NO'
        elif yes_sm:
            sm_summary = f'{len(yes_sm)} wallet{"s" if len(yes_sm)>1 else ""} buying YES'
        else:
            sm_summary = f'{len(no_sm)} wallet{"s" if len(no_sm)>1 else ""} buying NO'
        sm_block = f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:14px;background:{SURFACE2};border-radius:6px;border:1px solid #86198f40">
  <tr>
    <td style="padding:12px 16px">
      <div style="font-size:9px;font-weight:700;color:#e879f9;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:8px">Smart Money &nbsp;·&nbsp; {sm_summary}</div>
      <table width="100%" cellpadding="0" cellspacing="0">{rows}</table>
    </td>
  </tr>
</table>"""

    whale_block = ""
    if s.get("whale_detected"):
        wdir = s.get("whale_direction") or "?"
        whale_block = (
            f'<div style="margin-top:12px">'
            f'{_dot("#a78bfa")}'
            f'<span style="font-size:12px;color:#a78bfa;font-weight:600">Whale activity · buying {wdir}</span>'
            f'</div>'
        )

    ob_block = ""
    if s.get("ob_flag"):
        imb    = (s.get("ob_imbalance") or 0) * 100
        ob_dir = s.get("ob_direction", "?")
        ob_col = C_YES if ob_dir == "YES" else C_NO
        ob_block = (
            f'<div style="margin-top:8px">'
            f'{_dot("#22d3ee")}'
            f'<span style="font-size:12px;color:#22d3ee;font-weight:600">Order book {imb:.0f}% {ob_dir} side</span>'
            f'</div>'
        )

    sources_block = ""
    if sources:
        links = " &nbsp;·&nbsp; ".join(
            f'<span style="color:{TEXT3}">{src[:60]}</span>'
            for src in sources[:3]
        )
        sources_block = f'<div style="margin-top:10px;font-size:11px;line-height:1.6">{links}</div>'

    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 16px;border-radius:10px;overflow:hidden;border:1px solid {BORDER};background:{SURFACE}">

  <!-- Card header: confidence + direction + pills -->
  <tr>
    <td style="background:{SURFACE2};padding:12px 20px;border-bottom:1px solid {BORDER}">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td>
            {_dot(conf_col)}
            <span style="font-size:11px;font-weight:700;color:{conf_col};text-transform:uppercase;letter-spacing:1px">{CONF_LABEL.get(conf,conf)} CONFIDENCE</span>
            &nbsp;&nbsp;
            <span style="background:{dir_col}22;color:{dir_col};font-size:11px;font-weight:700;padding:3px 12px;border-radius:4px;border:1px solid {dir_col}55">BUY {direction}</span>
            &nbsp;&nbsp;
            <span style="background:{h_col}18;color:{h_col};font-size:10px;font-weight:700;padding:2px 9px;border-radius:4px;border:1px solid {h_col}40;letter-spacing:0.5px">{horizon}</span>
          </td>
          <td align="right" style="white-space:nowrap">{pills}</td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Title + ticker + close -->
  <tr>
    <td style="padding:18px 20px 0">
      <div style="font-size:17px;font-weight:600;color:{TEXT};line-height:1.45;max-width:520px">{title}</div>
      <div style="margin-top:5px;font-size:11px;color:{TEXT3};font-family:monospace;letter-spacing:0.3px">
        {ticker}
        {'&nbsp;&nbsp;·&nbsp;&nbsp;' + f'<span style="color:{TEXT2}">{close_fmt}</span>' if close_fmt else ''}
      </div>
    </td>
  </tr>

  <!-- Price row -->
  <tr>
    <td style="padding:16px 20px">
      <table cellpadding="0" cellspacing="0" style="background:{SURFACE2};border-radius:8px;width:100%;border:1px solid {BORDER}">
        <tr>
          <td align="center" style="padding:16px 12px;width:33%">
            <div style="font-size:9px;font-weight:700;color:{TEXT3};text-transform:uppercase;letter-spacing:1.5px">Market Price</div>
            <div style="font-size:28px;font-weight:800;color:{TEXT2};margin-top:6px;letter-spacing:-0.5px">{market_p}</div>
          </td>
          <td align="center" style="padding:0 4px;color:{TEXT3};font-size:22px">&#8594;</td>
          <td align="center" style="padding:16px 12px;width:33%">
            <div style="font-size:9px;font-weight:700;color:{TEXT3};text-transform:uppercase;letter-spacing:1.5px">Our Estimate</div>
            <div style="font-size:28px;font-weight:800;color:{conf_col};margin-top:6px;letter-spacing:-0.5px">{estimate}</div>
          </td>
          <td style="background:{BORDER};width:1px;padding:0"></td>
          <td align="center" style="padding:16px 16px;width:33%">
            <div style="font-size:9px;font-weight:700;color:{TEXT3};text-transform:uppercase;letter-spacing:1.5px">Edge</div>
            <div style="font-size:28px;font-weight:800;color:{edge_col};margin-top:6px;letter-spacing:-0.5px">{edge_str}</div>
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- Supporting signals -->
  {'<tr><td style="padding:0 20px">' + whale_block + ob_block + '</td></tr>' if (whale_block or ob_block) else ''}
  {'<tr><td style="padding:0 20px">' + sm_block + '</td></tr>' if sm_block else ''}
  {'<tr><td style="padding:0 20px">' + cross_block + '</td></tr>' if cross_block else ''}

  <!-- Reasoning -->
  <tr>
    <td style="padding:16px 20px 20px">
      <div style="border-top:1px solid {BORDER};padding-top:14px">
        <div style="font-size:9px;font-weight:700;color:{TEXT3};text-transform:uppercase;letter-spacing:1.5px;margin-bottom:8px">Analysis</div>
        <div style="font-size:13px;color:{TEXT2};line-height:1.75">{reasoning}</div>
        {sources_block}
      </div>
    </td>
  </tr>

</table>"""


def _html_report(signals, whale_only, stats, run_meta, config) -> str:
    threshold_rank = CONFIDENCE_ORDER.get(
        config.get("scoring", {}).get("confidence_threshold", "MED"), 1
    )
    qualifying = _qualifying(signals, threshold_rank)
    n_sig      = len(qualifying)
    n_whale    = len(whale_only)

    now_utc  = datetime.now(timezone.utc)
    date_str = now_utc.strftime("%B %d, %Y")
    time_str = now_utc.strftime("%H:%M UTC")
    env      = run_meta.get("environment", "PROD").upper()

    # ── Signal cards grouped by time horizon
    if qualifying:
        from scanner import BUCKETS as _BUCKETS
        bucket_order = [b[0] for b in _BUCKETS]
        grouped: dict[str, list] = {}
        for s in qualifying:
            h = s.get("time_horizon", "MONTHLY")
            grouped.setdefault(h, []).append(s)

        cards_html = ""
        for bucket in bucket_order:
            group = grouped.get(bucket)
            if not group:
                continue
            h_col, _ = HORIZON_STYLE.get(bucket, ("#94a3b8", ""))
            cards_html += (
                f'<div style="font-size:10px;font-weight:700;color:{h_col};'
                f'text-transform:uppercase;letter-spacing:1.5px;margin:20px 0 8px 2px">'
                f'{bucket} &nbsp;·&nbsp; {len(group)} signal{"s" if len(group)>1 else ""}</div>'
            )
            cards_html += "".join(_signal_card(s) for s in group)
    else:
        cards_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER};border-radius:10px;margin:0 0 16px">'
            f'<tr><td style="padding:32px;text-align:center">'
            f'<div style="font-size:32px;margin-bottom:12px">&#x25CB;</div>'
            f'<div style="font-size:14px;font-weight:600;color:{TEXT2}">No qualifying signals this run</div>'
            f'<div style="font-size:12px;color:{TEXT3};margin-top:6px">Markets were scanned but no edges above threshold were found</div>'
            f'</td></tr></table>'
        )

    # ── Whale section
    if whale_only:
        w_rows = ""
        for w in whale_only:
            avg   = w.get("avg_trade_size", 0)
            ratio = f"{w.get('max_trade_size',0)/avg:.1f}x avg" if avg else "—"
            d_col = "#a78bfa"
            w_rows += (
                f'<tr style="border-top:1px solid {BORDER}">'
                f'<td style="padding:12px 0;font-size:11px;font-family:monospace;color:{TEXT3};width:1%">{w.get("ticker","")}</td>'
                f'<td style="padding:12px 12px;font-size:13px;color:{TEXT}">{w.get("title","")[:50]}</td>'
                f'<td style="padding:12px 0;white-space:nowrap" align="right">{_pill(w.get("whale_direction","?"), d_col)}</td>'
                f'<td style="padding:12px 0 12px 12px;font-size:12px;color:{TEXT3};white-space:nowrap">{ratio}</td>'
                f'</tr>'
            )
        whale_html = f'<table width="100%" cellpadding="0" cellspacing="0">{w_rows}</table>'
    else:
        whale_html = f'<div style="font-size:13px;color:{TEXT3};padding:8px 0">No unusual whale activity detected.</div>'

    # ── Track record
    wr  = stats.get("win_rate")
    ae  = stats.get("avg_edge_captured")
    pnl = stats.get("total_hypothetical_pnl")

    def _stat(label, value, col=TEXT):
        return (
            f'<td align="center" style="padding:20px 8px">'
            f'<div style="font-size:9px;font-weight:700;color:{TEXT3};text-transform:uppercase;letter-spacing:1.5px">{label}</div>'
            f'<div style="font-size:24px;font-weight:800;color:{col};margin-top:6px;letter-spacing:-0.5px">{value}</div>'
            f'</td>'
        )

    wr_col  = C_YES if (wr or 0) >= 50 else TEXT
    pnl_col = C_YES if (pnl or 0) >= 0 else C_NO

    track_html = (
        f'<table width="100%" cellpadding="0" cellspacing="0" style="background:{SURFACE2};border-radius:8px;border:1px solid {BORDER}">'
        f'<tr>'
        f'{_stat("Calls", stats.get("total_calls",0))}'
        f'<td style="background:{BORDER};width:1px;padding:0"></td>'
        f'{_stat("Resolved", stats.get("resolved",0))}'
        f'<td style="background:{BORDER};width:1px;padding:0"></td>'
        f'{_stat("Win Rate", f"{wr:.1f}%" if wr is not None else "—", wr_col)}'
        f'<td style="background:{BORDER};width:1px;padding:0"></td>'
        f'{_stat("Avg Edge", _fmt_pct(ae) if ae is not None else "—", BRAND)}'
        f'<td style="background:{BORDER};width:1px;padding:0"></td>'
        f'{_stat("Hypo P&L", f"${pnl:.0f}" if pnl is not None else "—", pnl_col)}'
        f'</tr>'
        f'</table>'
    )

    # ── Run meta bar
    runtime_s = run_meta.get("runtime_ms", 0) / 1000
    meta_items = [
        ("Markets", str(run_meta.get("markets_scanned", 0))),
        ("Signals", str(run_meta.get("signals_generated", 0))),
        ("Model", run_meta.get("model_used", "—").replace("claude-", "")),
        ("Cost", _fmt_usd(run_meta.get("cost_usd"))),
        ("Runtime", f"{runtime_s:.0f}s"),
    ]
    meta_html = f' <span style="color:{TEXT3}">·</span> '.join(
        f'<span style="color:{TEXT3}">{k}</span> <span style="color:{TEXT2};font-weight:600">{v}</span>'
        for k, v in meta_items
    )

    # ── Header badge helper
    def _badge(n, label, col):
        return (
            f'<td style="padding-right:12px">'
            f'<table cellpadding="0" cellspacing="0" style="border:1px solid {col}40;border-radius:8px">'
            f'<tr><td style="padding:12px 20px;text-align:center">'
            f'<div style="font-size:30px;font-weight:800;color:{col};letter-spacing:-1px">{n}</div>'
            f'<div style="font-size:9px;font-weight:700;color:{col};text-transform:uppercase;letter-spacing:1.5px;margin-top:3px">{label}</div>'
            f'</td></tr>'
            f'</table>'
            f'</td>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>Leviathan — {date_str}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:{BG};-webkit-font-smoothing:antialiased;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">

<table width="100%" cellpadding="0" cellspacing="0" style="background:{BG}">
<tr><td align="center" style="padding:28px 16px 48px">

  <table cellpadding="0" cellspacing="0" style="width:100%;max-width:580px">

    <!-- ╔═══════════════════════════════════════╗ -->
    <!-- ║              HEADER                  ║ -->
    <!-- ╚═══════════════════════════════════════╝ -->
    <tr><td style="padding-bottom:8px">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:{SURFACE};border-radius:12px;border:1px solid {BORDER}">
        <tr>
          <!-- Left: wordmark + date -->
          <td style="padding:28px 28px 24px;vertical-align:top">
            <div style="font-size:10px;font-weight:700;color:{BRAND};text-transform:uppercase;letter-spacing:2.5px">Intelligence Report</div>
            <div style="font-size:32px;font-weight:800;color:{TEXT};margin-top:6px;letter-spacing:-1px;line-height:1">Leviathan</div>
            <div style="margin-top:10px;font-size:12px;color:{TEXT2}">{date_str} &nbsp;·&nbsp; {time_str} &nbsp;·&nbsp; {env}</div>
          </td>
        </tr>
        <!-- Badges row -->
        <tr>
          <td style="padding:0 28px 24px">
            <table cellpadding="0" cellspacing="0">
              <tr>
                {_badge(n_sig, "Signal" + ("s" if n_sig != 1 else ""), C_YES)}
                {_badge(n_whale, "Whale Flag" + ("s" if n_whale != 1 else ""), "#a78bfa")}
                {_badge(run_meta.get("markets_scanned",0), "Markets", TEXT2)}
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </td></tr>

    <!-- ╔═══════════════════════════════════════╗ -->
    <!-- ║             SIGNALS                  ║ -->
    <!-- ╚═══════════════════════════════════════╝ -->
    <tr><td>
      {_section_header("Signals")}
      {cards_html}
    </td></tr>

    <!-- ╔═══════════════════════════════════════╗ -->
    <!-- ║           WHALE ACTIVITY             ║ -->
    <!-- ╚═══════════════════════════════════════╝ -->
    <tr><td style="padding-top:8px">
      {_section_header("Whale Activity · No Qualifying Signal")}
      <table width="100%" cellpadding="0" cellspacing="0" style="background:{SURFACE};border-radius:10px;border:1px solid {BORDER}">
        <tr><td style="padding:16px 20px">{whale_html}</td></tr>
      </table>
    </td></tr>

    <!-- ╔═══════════════════════════════════════╗ -->
    <!-- ║           TRACK RECORD               ║ -->
    <!-- ╚═══════════════════════════════════════╝ -->
    <tr><td style="padding-top:8px">
      {_section_header("Track Record")}
      {track_html}
    </td></tr>

    <!-- ╔═══════════════════════════════════════╗ -->
    <!-- ║              FOOTER                  ║ -->
    <!-- ╚═══════════════════════════════════════╝ -->
    <tr><td style="padding-top:24px;text-align:center">
      <div style="font-size:11px;line-height:2;color:{TEXT3}">{meta_html}</div>
      <div style="font-size:10px;color:{TEXT3};margin-top:8px;opacity:0.6">
        Leviathan v1 &nbsp;·&nbsp; Read-only &nbsp;·&nbsp; For informational purposes only
      </div>
    </td></tr>

  </table>
</td></tr>
</table>

</body>
</html>"""


# ── Send ──────────────────────────────────────────────────────────────────────

def send_report(body: str, signals: list[dict], whale_flags: int, config: dict,
                run_meta: dict = None, whale_only: list = None, stats: dict = None) -> None:
    report_cfg   = config.get("report", {})
    email_to     = report_cfg.get("email_to", "")
    email_from   = report_cfg.get("email_from") or email_to
    smtp_host    = report_cfg.get("smtp_host", "smtp.gmail.com")
    smtp_port    = report_cfg.get("smtp_port", 587)
    app_password = os.getenv("GMAIL_APP_PASSWORD")

    if not app_password:
        raise RuntimeError("GMAIL_APP_PASSWORD not set in environment")
    if not email_to:
        raise RuntimeError("report.email_to not set in config.json")

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n_sig    = len(signals)
    subject  = (
        f"Leviathan — {date_str} | "
        f"{n_sig} signal{'s' if n_sig != 1 else ''} | "
        f"{whale_flags} whale flag{'s' if whale_flags != 1 else ''}"
    )

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = email_from
    msg["To"]      = email_to

    msg.attach(MIMEText(body, "plain", "utf-8"))

    if run_meta is not None and whale_only is not None and stats is not None:
        run_meta_with_env = {**run_meta, "environment": config.get("environment", "prod").upper()}
        html = _html_report(signals, whale_only, stats, run_meta_with_env, config)
        msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(email_from, app_password)
        server.sendmail(email_from, email_to, msg.as_string())

    print(f"  [report] Email sent to {email_to}")
