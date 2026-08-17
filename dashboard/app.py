"""
Leviathan dashboard -- Overview page.

Reads the pipeline's PowerBI CSV export (data/powerbi_export/). Free stack:
streamlit + pandas + plotly. Run with: streamlit run dashboard/app.py

Additional pages (Signal Breakdown, Signal Log) live in dashboard/pages/ and
are reachable from the sidebar Streamlit generates automatically.

See dashboard/data.py for the full data contract this app is built against.
"""

import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data import DataLoadError, data_freshness, load_runs, load_signals
from theme import CATEGORICAL_SEQUENCE, PLOTLY_TEMPLATE, WIN_COLOR, inject_css, page_header

st.set_page_config(page_title="Leviathan Dashboard", page_icon=":material/query_stats:", layout="wide")
inject_css()

try:
    signals = load_signals()
    runs = load_runs()
except DataLoadError as exc:
    st.error(f"Failed to load pipeline data: {exc}")
    st.stop()

if signals.empty:
    st.warning("signals.csv exists but has no rows yet -- nothing to show.")
    st.stop()

fresh_at = data_freshness()
freshness_sub = ""
if fresh_at is not None:
    age = datetime.datetime.now(datetime.timezone.utc) - fresh_at
    age_minutes = age.total_seconds() / 60
    if age_minutes < 60:
        age_str = f"{age_minutes:.0f} min ago"
    elif age_minutes < 24 * 60:
        age_str = f"{age_minutes / 60:.1f} hr ago"
    else:
        age_str = f"{age_minutes / 1440:.1f} days ago"
    freshness_sub = f"data as of {fresh_at.strftime('%Y-%m-%d %H:%M UTC')} ({age_str})"

page_header("Leviathan", freshness_sub)
st.caption("Overview")

col1, col2, col3, col4, col5 = st.columns(5)

active_count = int((signals["is_resolved"] == 0).sum())
col1.metric("Active Signals", active_count)

# TODO: markets_scanned in runs.csv is a Kalshi-only combined count -- no
# separate Polymarket-scanned metric exists anywhere in the pipeline output.
# Delta vs the prior run is a real, robust comparison (unlike the other
# KPIs here, every run has a well-defined "previous run" to compare against).
if not runs.empty:
    runs_sorted = runs.sort_values("timestamp")
    latest_run = runs_sorted.iloc[-1]
    scanned_delta = None
    if len(runs_sorted) >= 2:
        prev_scanned = runs_sorted.iloc[-2]["markets_scanned"]
        scanned_delta = int(latest_run["markets_scanned"] - prev_scanned)
    col2.metric("Markets Scanned (Kalshi)", int(latest_run["markets_scanned"]),
                delta=scanned_delta, delta_color="off")
else:
    col2.metric("Markets Scanned (Kalshi)", "no data")

edge_vals = signals["edge"].dropna()
if len(edge_vals) > 0:
    col3.metric("Median Edge", f"{edge_vals.median():+.3f}", help=f"Mean: {edge_vals.mean():+.3f}, n={len(edge_vals)}")
else:
    col3.metric("Median Edge", "no data")

# CLV drift (market_drift_pp) is the credibility metric per the dashboard
# spec -- deliberately not win rate. Small-n right now; n is always shown
# alongside it so it isn't presented as more robust than it is.
drift_vals = signals["market_drift_pp"].dropna()
if len(drift_vals) > 0:
    col4.metric("Avg CLV Drift (pp)", f"{drift_vals.mean():+.2f}", help=f"n={len(drift_vals)} resolved signals with drift data")
else:
    col4.metric("Avg CLV Drift (pp)", "no data yet", help="market_drift_pp has no populated rows yet")

if not runs.empty:
    last_run_ts = runs.sort_values("timestamp").iloc[-1]["timestamp"]
    col5.metric("Last Run", "unknown" if pd.isna(last_run_ts) else last_run_ts.strftime("%Y-%m-%d %H:%M UTC"))
else:
    col5.metric("Last Run", "no data")

st.divider()

st.subheader("Cumulative Bets & Resolutions")
dated = signals.dropna(subset=["date"]).sort_values("date")
if dated.empty:
    st.info("No dated signals to trend yet.")
else:
    daily = dated.groupby(dated["date"].dt.date).agg(
        new_bets=("call_id", "count"),
        new_resolved=("is_resolved", "sum"),
    ).reset_index()
    daily["cum_bets"] = daily["new_bets"].cumsum()
    daily["cum_resolved"] = daily["new_resolved"].cumsum()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=daily["date"], y=daily["cum_bets"], name="Total bets",
        mode="lines", line=dict(color=CATEGORICAL_SEQUENCE[0], width=2.5),
        fill="tozeroy", fillcolor="rgba(21,101,192,0.08)",
    ))
    fig.add_trace(go.Scatter(
        x=daily["date"], y=daily["cum_resolved"], name="Resolved",
        mode="lines", line=dict(color=WIN_COLOR, width=2.5, dash="dot"),
    ))
    fig.update_layout(PLOTLY_TEMPLATE["layout"], height=340,
                       xaxis_title="Date", yaxis_title="Cumulative count")
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"{int(daily['cum_bets'].iloc[-1])} total bets logged, "
        f"{int(daily['cum_resolved'].iloc[-1])} resolved so far."
    )

st.caption(
    "signals.csv holds real bets only (direction YES/NO) -- PASS decisions "
    "live in scan_log.csv, not read by this dashboard. "
    "net_edge does not model fees -- see net_edge_after_fee for that."
)
