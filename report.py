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

def _qualifying(signals: list[dict], threshold_rank: int) -> list[dict]:
    out = [
        s for s in signals
        if (
            CONFIDENCE_ORDER.get(s.get("confidence", "LOW"), 2) <= threshold_rank
            or s.get("second_pass")  # always include second-pass signals
        )
        and s.get("direction", "PASS") != "PASS"
    ]
    out.sort(key=lambda s: (
        CONFIDENCE_ORDER.get(s.get("confidence", "LOW"), 2),
        -(abs(float(s.get("edge") or 0)))
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
    close_fmt = ""
    if close_raw:
        try:
            dt = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
            close_fmt = dt.strftime("Closes %b %d, %Y").replace(" 0", " ")
        except Exception:
            close_fmt = close_raw[:10]

    mkt_p  = _pct(s.get("market_price"))
    est_p  = _pct(s.get("our_estimate"))
    edge_v = float(s.get("edge") or 0)
    edge_s = f"{edge_v*100:+.1f} pp"

    # Header line
    num        = f"[{index}]  " if index else ""
    pass_label = "  [SECOND PASS — LOW CONVICTION]" if s.get("second_pass") else ""
    lines.append(f"{num}{CONF_LABEL[conf]} CONFIDENCE  /  BUY {direction}  /  {horizon}{pass_label}")
    lines.append(f"{ticker}  ·  {close_fmt}" if close_fmt else ticker)
    lines.append("")

    # Title
    lines.extend(_wrap(title))
    lines.append("")

    # Prices
    lines.append(f"  Market:       {mkt_p}")
    lines.append(f"  Our Estimate: {est_p}")
    lines.append(f"  Edge:         {edge_s}")

    # Signals fired
    fired = []
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
    if s.get("smart_money"):
        dirs = set(sm.get("direction") for sm in s["smart_money"] if sm.get("direction"))
        fired.append(f"Smart Money x{len(s['smart_money'])} ({'·'.join(dirs)})")

    if fired:
        lines.append(f"  Signals:      {' · '.join(fired)}")

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


# ── Daily report ──────────────────────────────────────────────────────────────

def compile_report(
    signals, whale_only, stats, run_meta, config,
    all_filtered=None, new_signals=None, repeat_signals=None
) -> str:
    threshold_rank = CONFIDENCE_ORDER.get(
        config.get("scoring", {}).get("confidence_threshold", "MED"), 1
    )
    now_utc    = datetime.now(timezone.utc)
    date_str   = now_utc.strftime("%B %d, %Y")
    time_str   = now_utc.strftime("%H:%M UTC")
    env        = config.get("environment", "prod").upper()
    qualifying = _qualifying(signals, threshold_rank)
    new_q      = _qualifying(new_signals or [], threshold_rank)
    repeat_q   = _qualifying(repeat_signals or [], threshold_rank)
    n_mkt      = run_meta.get("markets_scanned", 0)
    runtime_s  = run_meta.get("runtime_ms", 0) / 1000

    out = []

    # ── Header ────────────────────────────────────────────────────────────
    out.append(_rule("="))
    out.append(f"LEVIATHAN  ·  INTELLIGENCE REPORT")
    out.append(f"{date_str}  ·  {time_str}  ·  {env}")
    out.append(_rule("="))
    out.append("")
    out.append(f"  New Signals:    {len(new_q)}")
    out.append(f"  Repeat Signals: {len(repeat_q)}")
    out.append(f"  Whale Flags:    {len(whale_only)}")
    out.append(f"  Markets Scanned:{n_mkt}")
    out.append(f"  Smart Money:    active — winning wallets cached & scanning")
    out.append("")

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

def compile_weekly_digest(week_signals: list[dict], stats: dict, config: dict) -> str:
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
    out.append(f"  {'First Seen':<12}  {'Ticker':<26}  {'Conf':<4}  {'Dir':<3}  {'Edge':>7}  Title")
    out.append(f"  {'-'*12}  {'-'*26}  {'-'*4}  {'-'*3}  {'-'*7}  -----")

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
            edge_s = "—"
        title  = (row.get("title") or "")[:40]
        out.append(f"  {ts_s:<12}  {ticker:<26}  {conf:<4}  {dir_:<3}  {edge_s:>7}  {title}")

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
    out.append("")
    out.append(_rule("="))
    out.append("Leviathan v1  ·  Weekly Summary  ·  For informational purposes only")
    out.append(_rule("="))

    return "\n".join(out)


# ── Send ──────────────────────────────────────────────────────────────────────

def send_report(body: str, signals: list[dict], whale_flags: int, config: dict,
                subject_override: str = "") -> None:
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

    msg            = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = email_from
    msg["To"]      = email_to

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(email_from, app_password)
        server.sendmail(email_from, email_to, msg.as_string())

    print(f"  [report] Email sent to {email_to}")
