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
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data import CONFIDENCE_ORDER, DataLoadError, data_freshness, load_runs, load_signals
from theme import CATEGORICAL_SEQUENCE, LOSS_COLOR, PLOTLY_TEMPLATE, WIN_COLOR, inject_css, page_header

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

lookback_choice = st.radio(
    "Lookback window", ["All time", "Last 30 days", "Last 7 days"],
    index=0, horizontal=True, label_visibility="collapsed",
)
LOOKBACK_DAYS = {"All time": None, "Last 30 days": 30, "Last 7 days": 7}
window_days = LOOKBACK_DAYS[lookback_choice]
if window_days is not None:
    # signals["date"] is parsed via pd.to_datetime() with no utc=True (data.py) --
    # tz-naive, since the source CSV column is a plain "YYYY-MM-DD" string. The
    # cutoff below has to match that, or the comparison raises a dtype TypeError.
    # DateOffset, not Timedelta(days=window_days) -- a plain-int Timedelta(days=)
    # raises a NumPy "generic unit" DeprecationWarning on this pinned pandas/numpy
    # combination (confirmed 2026-08-25); DateOffset doesn't hit that path.
    cutoff = pd.Timestamp.now().normalize() - pd.DateOffset(days=window_days)
    signals = signals[signals["date"] >= cutoff]
    if signals.empty:
        st.info(f"No signals in the {lookback_choice.lower()} window -- showing an empty view rather than falling back to all-time.")
        st.stop()

st.caption(
    f"Overview -- {lookback_choice.lower()}. Each row below is one bet Leviathan "
    "decided to place on a Kalshi market -- hover the (?) on any number for a "
    "plain-English explanation."
)

col1, col2, col3, col4, col5 = st.columns(5)

active_count = int((signals["is_resolved"] == 0).sum())
col1.metric("Active Signals", active_count,
            help="A 'signal' is a market Leviathan decided to bet on. Active = we're still "
                 "waiting to find out if we were right.")

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
                delta=scanned_delta, delta_color="off",
                help="How many Kalshi markets the most recent run looked at in total -- "
                     "whether or not it found a bet worth making in each one.")
else:
    col2.metric("Markets Scanned (Kalshi)", "no data")

edge_vals = signals["edge"].dropna()
if len(edge_vals) > 0:
    col3.metric("Median Edge", f"{edge_vals.median():.3f}",
                help="'Edge' is the SIZE of the gap between what Leviathan thinks the real "
                     "odds are and what the market is charging -- e.g. 0.10 is a 10 "
                     "percentage point disagreement. It doesn't say which way; 'direction' "
                     "(YES/NO, shown per bet) is what tells you that. A bigger edge is a "
                     "bigger disagreement with the market, not proof we're right. Edge is "
                     "meant to always be zero or positive -- a rare negative reading here "
                     "reflects the model's own self-reported number on that one call, not a "
                     f"meaningful 'against us' signal. (Mean: {edge_vals.mean():.3f}, n={len(edge_vals)})")
else:
    col3.metric("Median Edge", "no data")

# CLV drift (market_drift_pp) is the credibility metric per the dashboard
# spec -- deliberately not win rate. Small-n right now; n is always shown
# alongside it so it isn't presented as more robust than it is.
drift_vals = signals["market_drift_pp"].dropna()
CLV_HELP = (
    "CLV = 'Closing Line Value.' After we place a bet, does the market's own price later "
    "drift toward our prediction, before the outcome is even known? If yes, that suggests "
    "we spotted something real before the market caught up -- a stronger sign of skill "
    "than just winning the bet, since a bet can win by luck but a market moving your way "
    "is the market itself agreeing with you. 'pp' = percentage points. "
)
if len(drift_vals) > 0:
    col4.metric("Avg CLV Drift (pp)", f"{drift_vals.mean():+.2f}",
                help=CLV_HELP + f"(n={len(drift_vals)} resolved signals with drift data so far -- still a small sample.)")
else:
    col4.metric("Avg CLV Drift (pp)", "no data yet",
                help=CLV_HELP + "No resolved signals with this data yet.")

if not runs.empty:
    last_run_ts = runs.sort_values("timestamp").iloc[-1]["timestamp"]
    col5.metric("Last Run", "unknown" if pd.isna(last_run_ts) else last_run_ts.strftime("%Y-%m-%d %H:%M UTC"))
else:
    col5.metric("Last Run", "no data")

st.divider()

trend_col, toggle_col = st.columns([5, 1])
trend_col.subheader("Bets & Resolutions Over Time")
trend_col.caption("'Resolved' means the market's real-world outcome is now known -- the bet won or lost.")
trend_view = toggle_col.selectbox("View", ["Cumulative", "Daily new"], label_visibility="collapsed")

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
    if trend_view == "Cumulative":
        fig.add_trace(go.Scatter(
            x=daily["date"], y=daily["cum_bets"], name="Total bets",
            mode="lines", line=dict(color=CATEGORICAL_SEQUENCE[0], width=2.5),
            fill="tozeroy", fillcolor="rgba(21,101,192,0.08)",
        ))
        fig.add_trace(go.Scatter(
            x=daily["date"], y=daily["cum_resolved"], name="Resolved",
            mode="lines", line=dict(color=WIN_COLOR, width=2.5, dash="dot"),
        ))
        y_title = "Cumulative count"
    else:
        fig.add_trace(go.Bar(x=daily["date"], y=daily["new_bets"], name="New bets",
                              marker_color=CATEGORICAL_SEQUENCE[0]))
        fig.add_trace(go.Bar(x=daily["date"], y=daily["new_resolved"], name="Resolved that day",
                              marker_color=WIN_COLOR))
        fig.update_layout(barmode="group")
        y_title = "Count"
    fig.update_layout(PLOTLY_TEMPLATE["layout"], height=340,
                       xaxis_title="Date", yaxis_title=y_title)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"{int(daily['cum_bets'].iloc[-1])} total bets logged, "
        f"{int(daily['cum_resolved'].iloc[-1])} resolved so far."
    )

st.divider()

brk_col1, brk_col2 = st.columns(2)

with brk_col1:
    st.subheader("Bets by Direction")
    st.caption("YES = we bet the thing happens. NO = we bet it doesn't.")
    dir_counts = signals["direction"].value_counts()
    if dir_counts.empty:
        st.info("No direction data to show.")
    else:
        fig = px.pie(
            names=dir_counts.index, values=dir_counts.values, hole=0.45,
            color=dir_counts.index, color_discrete_map={"YES": WIN_COLOR, "NO": LOSS_COLOR},
        )
        fig.update_layout(PLOTLY_TEMPLATE["layout"], height=280,
                           legend=dict(orientation="h", y=-0.1))
        fig.update_traces(textinfo="value+percent")
        st.plotly_chart(fig, use_container_width=True)

with brk_col2:
    st.subheader("Bets by Confidence")
    st.caption("How sure Leviathan's own scoring was when it made each pick -- not how the bet turned out.")
    conf_counts = signals["confidence"].value_counts().reindex(CONFIDENCE_ORDER).fillna(0)
    if conf_counts.sum() == 0:
        st.info("No confidence data to show.")
    else:
        fig = px.bar(x=conf_counts.index, y=conf_counts.values,
                      labels={"x": "confidence", "y": "count"})
        fig.update_layout(PLOTLY_TEMPLATE["layout"], height=280, showlegend=False)
        fig.update_traces(marker_color=CATEGORICAL_SEQUENCE[0])
        st.plotly_chart(fig, use_container_width=True)

st.divider()

st.subheader("Win Rate by Category")
st.caption(
    "Category is what kind of question the market asks (Politics, Sports, Economics, "
    "etc.) -- Kalshi assigns it, Leviathan just reads it. "
    "'Uncategorized' below is unusually large right now: this is a known, tracked gap "
    "in how Leviathan captures Kalshi's category data on some runs (backlog: "
    "signal-category-mostly-blank-despite-real-data) -- not missing or hidden bets, "
    "and not something wrong with any individual pick."
)
resolved_for_cat = signals[signals["is_win"].notna()].copy()
if resolved_for_cat.empty:
    st.info("No resolved bets yet to compute a win rate by category.")
else:
    resolved_for_cat["category_label"] = resolved_for_cat["category"].fillna("Uncategorized")
    cat_stats = resolved_for_cat.groupby("category_label").agg(
        n=("is_win", "size"), win_rate=("is_win", "mean"),
    ).sort_values("n", ascending=False)
    cat_stats["win_rate_pct"] = cat_stats["win_rate"] * 100
    fig = px.bar(
        cat_stats, x=cat_stats.index, y="win_rate_pct",
        labels={"x": "category", "win_rate_pct": "win rate %"},
        text=cat_stats["n"].astype(int).map(lambda n: f"n={n}"),
    )
    fig.add_hline(y=50, line_dash="dot", line_color="#9E9E9E",
                  annotation_text="50% = coin flip", annotation_position="top left")
    fig.update_layout(PLOTLY_TEMPLATE["layout"], showlegend=False, height=320)
    fig.update_traces(marker_color=CATEGORICAL_SEQUENCE[0], textposition="outside")
    st.plotly_chart(fig, use_container_width=True)
    thin = int(cat_stats["n"].lt(10).sum())
    if thin:
        st.caption(
            f"{thin} of {len(cat_stats)} categories shown have fewer than 10 resolved bets -- "
            "with that few results, a single win or loss swings the bar a lot, so treat those "
            "as too early to read anything into yet, not a real pattern."
        )

st.caption(
    "signals.csv holds real bets only (direction YES/NO) -- PASS decisions "
    "live in scan_log.csv, not read by this dashboard. "
    "net_edge does not model fees -- see net_edge_after_fee for that."
)
