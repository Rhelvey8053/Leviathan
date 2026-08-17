"""Leviathan dashboard -- Signal Log page. See dashboard/data.py for the data contract."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data import DataLoadError, load_signals

st.set_page_config(page_title="Leviathan -- Signal Log", layout="wide")
st.title("Signal Log")
st.caption("Each row is one signal for one market, carrying its own source/flag_path -- provenance is per-market, not batch-level.")

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

DEFAULT_COLS = [
    "date", "ticker", "title", "source", "direction", "confidence",
    "flag_path", "category", "edge", "net_edge", "leviathan_score",
    "lv_band", "whale_detected", "is_resolved", "result",
]
default_cols = [c for c in DEFAULT_COLS if c in filtered.columns]
chosen_cols = st.multiselect("Columns to show", list(filtered.columns), default=default_cols)

if filtered.empty:
    st.info("No signals match the current filters.")
else:
    st.write(f"{len(filtered)} signal(s)")
    display_cols = chosen_cols or default_cols
    st.dataframe(filtered[display_cols], use_container_width=True, hide_index=True)
    st.download_button(
        "Download filtered view as CSV",
        data=filtered[display_cols].to_csv(index=False).encode("utf-8"),
        file_name="leviathan_signals_filtered.csv",
        mime="text/csv",
    )
