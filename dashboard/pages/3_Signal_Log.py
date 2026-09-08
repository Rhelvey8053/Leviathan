"""Leviathan dashboard -- Signal Log page. See dashboard/data.py for the data contract."""

import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data import DataLoadError, load_signals
from theme import ACCENT_COLOR, PLOTLY_TEMPLATE, inject_css, page_header

st.set_page_config(page_title="Leviathan -- Signal Log", layout="wide")
inject_css()
page_header("Signal Log", "")
st.caption(
    "Every real bet Leviathan has made, one row each, with exactly how and why it was made "
    "(where it came from, how it was detected) attached to that specific bet -- not a "
    "batch-level summary. Markets that were scanned but not bet on live in a separate file, "
    "not shown here."
)

try:
    signals = load_signals()
except DataLoadError as exc:
    st.error(f"Failed to load pipeline data: {exc}")
    st.stop()

if signals.empty:
    st.warning("signals.csv exists but has no rows yet.")
    st.stop()

st.sidebar.header("Filters")
directions = st.sidebar.multiselect("Direction", sorted(signals["direction"].dropna().unique()), default=list(signals["direction"].dropna().unique()))
sources = st.sidebar.multiselect("Source", sorted(signals["source"].dropna().unique()), default=list(signals["source"].dropna().unique()))
min_date = signals["date"].min()
max_date = signals["date"].max()
date_range = st.sidebar.date_input("Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date)
search = st.sidebar.text_input("Search ticker / title")

filtered = signals[signals["direction"].isin(directions) & signals["source"].isin(sources)]
if isinstance(date_range, tuple) and len(date_range) == 2:
    start, end = date_range
    filtered = filtered[(filtered["date"].dt.date >= start) & (filtered["date"].dt.date <= end)]
if search:
    mask = filtered["ticker"].str.contains(search, case=False, na=False) | filtered["title"].str.contains(search, case=False, na=False)
    filtered = filtered[mask]

if filtered.empty:
    st.info("No signals match the current filters.")
    st.stop()

n_total = len(filtered)
n_resolved = int(filtered["is_resolved"].sum())
n_wins = int((filtered["is_win"] == 1).sum())
win_rate_str = f"{n_wins / n_resolved * 100:.0f}%" if n_resolved else "n/a"

s1, s2, s3, s4 = st.columns(4)
s1.metric("Bets in view", n_total)
s2.metric("Resolved", n_resolved)
s3.metric("Win rate", win_rate_str, help=f"n={n_resolved}" if n_resolved else "no resolved bets in view")
s4.metric("Whale-flagged", int(filtered["whale_detected"].sum()),
          help="A 'whale' is an unusually large order on Kalshi's order book at the time this "
               "bet was made -- real, serious money on one side. Kalshi's order book shows "
               "size and direction only, never who placed it, so this has NO identity behind "
               "it -- not a specific trader with a track record, just an anonymous signal that "
               "someone was willing to bet big on this particular market.")

with st.expander("Bets per day in this view", expanded=False):
    per_day = filtered.dropna(subset=["date"]).groupby(filtered["date"].dt.date).size().reset_index(name="count")
    if per_day.empty:
        st.info("No dated bets in the current filter.")
    else:
        fig = px.bar(per_day, x="date", y="count", labels={"date": "Date", "count": "Bets logged"})
        fig.update_layout(PLOTLY_TEMPLATE["layout"], height=260, showlegend=False)
        fig.update_traces(marker_color=ACCENT_COLOR)
        st.plotly_chart(fig, use_container_width=True)

DEFAULT_COLS = [
    "date", "ticker", "title", "source", "direction", "confidence",
    "flag_path", "category", "edge", "net_edge", "leviathan_score",
    "lv_band", "pre_scoring_era", "whale_detected", "is_resolved", "result",
]
default_cols = [c for c in DEFAULT_COLS if c in filtered.columns]
chosen_cols = st.multiselect("Columns to show", list(filtered.columns), default=default_cols)

display_cols = chosen_cols or default_cols
st.dataframe(filtered[display_cols], use_container_width=True, hide_index=True)
st.download_button(
    "Download filtered view as CSV",
    data=filtered[display_cols].to_csv(index=False).encode("utf-8"),
    file_name="leviathan_signals_filtered.csv",
    mime="text/csv",
)
