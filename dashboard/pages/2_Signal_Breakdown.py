"""Leviathan dashboard -- Signal Breakdown page. See dashboard/data.py for the data contract."""

import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data import CONFIDENCE_ORDER, DataLoadError, load_signals

st.set_page_config(page_title="Leviathan -- Signal Breakdown", layout="wide")
st.title("Signal Breakdown")

try:
    signals = load_signals()
except DataLoadError as exc:
    st.error(f"Failed to load pipeline data: {exc}")
    st.stop()

if signals.empty:
    st.warning("signals.csv exists but has no rows yet -- nothing to break down.")
    st.stop()

real_signals = signals[signals["direction"].isin(["YES", "NO"])].copy()

st.sidebar.header("Filters")
sources = st.sidebar.multiselect("Source", sorted(real_signals["source"].dropna().unique()), default=list(real_signals["source"].dropna().unique()))
min_date = real_signals["date"].min()
max_date = real_signals["date"].max()
date_range = st.sidebar.date_input("Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date)
min_edge = st.sidebar.slider("Min |edge|", 0.0, float(real_signals["edge"].abs().max() or 0.1), 0.0, step=0.005)
min_conf = st.sidebar.select_slider("Min confidence", options=CONFIDENCE_ORDER, value="LOW")

filtered = real_signals[real_signals["source"].isin(sources)]
if isinstance(date_range, tuple) and len(date_range) == 2:
    start, end = date_range
    filtered = filtered[(filtered["date"].dt.date >= start) & (filtered["date"].dt.date <= end)]
filtered = filtered[filtered["edge"].abs().fillna(0) >= min_edge]
conf_rank = {c: i for i, c in enumerate(CONFIDENCE_ORDER)}
filtered = filtered[filtered["confidence"].map(conf_rank).fillna(-1) >= conf_rank[min_conf]]

if filtered.empty:
    st.info("No signals match the current filters.")
    st.stop()

col1, col2 = st.columns(2)

with col1:
    st.subheader("Edge Distribution")
    edge_vals = filtered["edge"].dropna()
    if edge_vals.empty:
        st.info("No edge data for the current filter.")
    else:
        st.plotly_chart(px.histogram(edge_vals, nbins=30, labels={"value": "edge"}), use_container_width=True)
        st.caption("net_edge does not model fees -- shown as raw edge, not net EV.")

with col2:
    st.subheader("Confidence Distribution")
    conf_counts = filtered["confidence"].value_counts().reindex(CONFIDENCE_ORDER).fillna(0)
    if conf_counts.sum() == 0:
        st.info("No confidence data for the current filter.")
    else:
        st.plotly_chart(px.bar(x=conf_counts.index, y=conf_counts.values, labels={"x": "confidence", "y": "count"}), use_container_width=True)

col3, col4 = st.columns(2)

with col3:
    # TODO: the spec asks for a Kalshi-vs-Polymarket source breakdown; no such
    # column exists (see dashboard/data.py contract). flag_path is the
    # nearest real proxy -- CROSS_MARKET is the Polymarket-corroborated path.
    st.subheader("By Detection Path (flag_path)")
    fp_counts = filtered["flag_path"].fillna("(none)").value_counts()
    if fp_counts.empty:
        st.info("No flag_path data for the current filter.")
    else:
        st.plotly_chart(px.bar(x=fp_counts.index, y=fp_counts.values, labels={"x": "flag_path", "y": "count"}), use_container_width=True)

with col4:
    st.subheader("By Category")
    cat_counts = filtered["category"].fillna("Uncategorized").value_counts()
    st.plotly_chart(px.bar(x=cat_counts.index, y=cat_counts.values, labels={"x": "category", "y": "count"}), use_container_width=True)

st.divider()
st.subheader("CLV Drift (credibility signal)")
drift = filtered["market_drift_pp"].dropna()
if drift.empty:
    st.info("No CLV drift data for the current filter yet -- market_drift_pp is only populated for resolved signals (13/317 across the full dataset right now).")
else:
    st.plotly_chart(px.histogram(drift, nbins=20, labels={"value": "market_drift_pp"}), use_container_width=True)
    st.caption(f"n={len(drift)}. This is the primary credibility read -- not win rate.")

with st.expander("Secondary: win rate (not the primary credibility metric)"):
    resolved = filtered[filtered["is_win"].notna()]
    if resolved.empty:
        st.info("No resolved signals in the current filter.")
    else:
        win_rate = resolved["is_win"].mean() * 100
        st.write(f"Win rate: {win_rate:.1f}% (n={len(resolved)})")
