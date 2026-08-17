"""Leviathan dashboard -- Signal Breakdown page. See dashboard/data.py for the data contract."""

import sys
from pathlib import Path

import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data import CONFIDENCE_ORDER, DataLoadError, load_signals
from theme import LOSS_COLOR, PLOTLY_TEMPLATE, WIN_COLOR, inject_css, page_header, small_n_badge

st.set_page_config(page_title="Leviathan -- Signal Breakdown", layout="wide")
inject_css()
page_header("Signal Breakdown", "analysis")

try:
    signals = load_signals()
except DataLoadError as exc:
    st.error(f"Failed to load pipeline data: {exc}")
    st.stop()

if signals.empty:
    st.warning("signals.csv exists but has no rows yet -- nothing to break down.")
    st.stop()

st.sidebar.header("Filters")
sources = st.sidebar.multiselect("Source", sorted(signals["source"].dropna().unique()), default=list(signals["source"].dropna().unique()))
min_date = signals["date"].min()
max_date = signals["date"].max()
date_range = st.sidebar.date_input("Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date)
min_edge = st.sidebar.slider("Min |edge|", 0.0, float(signals["edge"].abs().max() or 0.1), 0.0, step=0.005)
min_conf = st.sidebar.select_slider("Min confidence", options=CONFIDENCE_ORDER, value="LOW")

filtered = signals[signals["source"].isin(sources)]
if isinstance(date_range, tuple) and len(date_range) == 2:
    start, end = date_range
    filtered = filtered[(filtered["date"].dt.date >= start) & (filtered["date"].dt.date <= end)]
filtered = filtered[filtered["edge"].abs().fillna(0) >= min_edge]
conf_rank = {c: i for i, c in enumerate(CONFIDENCE_ORDER)}
filtered = filtered[filtered["confidence"].map(conf_rank).fillna(-1) >= conf_rank[min_conf]]

if filtered.empty:
    st.info("No signals match the current filters.")
    st.stop()

legacy_n = int(filtered["pre_scoring_era"].sum())
if legacy_n > 0:
    st.caption(
        f"{legacy_n} of {len(filtered)} bets in this filter are pre-scoring-era "
        "(logged before leviathan_score existed, 2026-04-13 to 2026-07-27) -- "
        "leviathan_score/lv_band/flag_path are unavoidably blank for these, not a data-quality bug."
    )

# ── Click-to-filter: detection path bar chart drives every chart below it ──
# TODO: the spec asks for a Kalshi-vs-Polymarket source breakdown; no such
# column exists (see dashboard/data.py contract). flag_path is the nearest
# real proxy -- CROSS_MARKET is the Polymarket-corroborated path.
st.subheader("By Detection Path (flag_path)")
st.caption("Click a bar to filter every chart below by that detection path. Click it again, or the button, to clear.")
fp_counts = filtered["flag_path"].fillna("(none)").value_counts()
if fp_counts.empty:
    st.info("No flag_path data for the current filter.")
    view = filtered
else:
    fp_fig = px.bar(x=fp_counts.index, y=fp_counts.values, labels={"x": "flag_path", "y": "count"})
    fp_fig.update_layout(PLOTLY_TEMPLATE["layout"], height=280)
    fp_fig.update_traces(marker_color=PLOTLY_TEMPLATE["layout"]["colorway"][0])
    event = st.plotly_chart(
        fp_fig, use_container_width=True,
        on_select="rerun", selection_mode="points", key="fp_click_chart",
    )
    selected_fp = None
    points = (event or {}).get("selection", {}).get("points", [])
    if points:
        selected_fp = points[0].get("x")

    col_sel, col_clear = st.columns([3, 1])
    if selected_fp:
        col_sel.caption(f"Filtering on flag_path = **{selected_fp}**")
        view = filtered[filtered["flag_path"].fillna("(none)") == selected_fp]
    else:
        view = filtered
    if col_clear.button("Clear selection"):
        st.rerun()

col1, col2 = st.columns(2)

with col1:
    st.subheader("Edge Distribution")
    edge_vals = view["edge"].dropna()
    if edge_vals.empty:
        st.info("No edge data for the current filter.")
    else:
        fig = px.histogram(edge_vals, nbins=30, labels={"value": "edge"})
        fig.update_layout(PLOTLY_TEMPLATE["layout"], showlegend=False, height=300)
        st.plotly_chart(fig, use_container_width=True)
        st.caption("net_edge does not model fees -- shown as raw edge, not net EV.")

with col2:
    st.subheader("Confidence Distribution")
    conf_counts = view["confidence"].value_counts().reindex(CONFIDENCE_ORDER).fillna(0)
    if conf_counts.sum() == 0:
        st.info("No confidence data for the current filter.")
    else:
        fig = px.bar(x=conf_counts.index, y=conf_counts.values, labels={"x": "confidence", "y": "count"})
        fig.update_layout(PLOTLY_TEMPLATE["layout"], height=300)
        st.plotly_chart(fig, use_container_width=True)

st.subheader("By Category")
cat_counts = view["category"].fillna("Uncategorized").value_counts()
fig = px.bar(x=cat_counts.index, y=cat_counts.values, labels={"x": "category", "y": "count"})
fig.update_layout(PLOTLY_TEMPLATE["layout"], height=300)
st.plotly_chart(fig, use_container_width=True)

st.divider()
st.subheader("CLV Drift (credibility signal)")
drift = view["market_drift_pp"].dropna()
if drift.empty:
    st.info("No CLV drift data for the current filter yet -- market_drift_pp is only populated for resolved signals (13/46 real bets across the full dataset right now).")
else:
    fig = px.histogram(drift, nbins=20, labels={"value": "market_drift_pp"})
    fig.update_layout(PLOTLY_TEMPLATE["layout"], showlegend=False, height=280)
    st.plotly_chart(fig, use_container_width=True)
    st.markdown(f"n={len(drift)}. {small_n_badge(len(drift))} This is the primary credibility read -- not win rate.", unsafe_allow_html=True)

st.divider()
st.subheader("Resolved Bets: Confidence & Edge vs Outcome")
resolved = view[view["is_win"].notna()].copy()
if resolved.empty:
    st.info("No resolved bets in the current filter.")
else:
    resolved["outcome_label"] = resolved["is_win"].map({1.0: "WIN", 0.0: "LOSS"})
    n_resolved = len(resolved)
    st.markdown(
        f"n={n_resolved} resolved bets in this filter. {small_n_badge(n_resolved)} "
        "Individual outcomes shown, not a binned calibration curve -- too few resolved "
        "bets yet for binning to mean anything.",
        unsafe_allow_html=True,
    )
    outcome_colors = {"WIN": WIN_COLOR, "LOSS": LOSS_COLOR}

    colA, colB = st.columns(2)
    with colA:
        est = resolved.dropna(subset=["our_estimate", "direction"]).copy()
        if est.empty:
            st.info("No our_estimate data among resolved bets in this filter.")
        else:
            est["predicted_p_win"] = est.apply(
                lambda r: r["our_estimate"] if r["direction"] == "YES" else 1 - r["our_estimate"],
                axis=1,
            )
            fig = px.strip(est, x="predicted_p_win", y="outcome_label", color="outcome_label",
                            color_discrete_map=outcome_colors,
                            labels={"predicted_p_win": "Predicted P(win)", "outcome_label": ""})
            fig.add_vline(x=0.5, line_dash="dot", line_color="#9E9E9E")
            fig.update_layout(PLOTLY_TEMPLATE["layout"], showlegend=False, height=260)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Well-calibrated picks cluster right of 0.5 for WIN, left of 0.5 for LOSS.")

    with colB:
        ed = resolved.dropna(subset=["edge"])
        if ed.empty:
            st.info("No edge data among resolved bets in this filter.")
        else:
            fig = px.strip(ed, x="edge", y="outcome_label", color="outcome_label",
                            color_discrete_map=outcome_colors,
                            labels={"edge": "Edge", "outcome_label": ""})
            fig.update_layout(PLOTLY_TEMPLATE["layout"], showlegend=False, height=260)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("net_edge does not model fees -- raw edge shown.")

    win_rate = resolved["is_win"].mean() * 100
    st.markdown(f"Win rate: **{win_rate:.1f}%** (n={n_resolved}) {small_n_badge(n_resolved)} -- secondary read, not the primary credibility metric above.", unsafe_allow_html=True)
