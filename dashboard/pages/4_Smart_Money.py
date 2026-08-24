"""
Leviathan dashboard -- Smart Money Discovery page.

backlog: smart-money-discovery-dashboard. Unlike Signal_Breakdown/Signal_Log
(which read the offline signals.csv/runs.csv export), this page reads two
data sources that are NOT gated by Leviathan's own resolved-bet count:

  1. sources.accounts.diagnose_discovery() -- a funnel diagnostic over live
     Polymarket wallet data (cached 5 min via st.cache_data so repeated
     Streamlit reruns from filter/tab changes don't hammer the API --
     see the rate-limit pacing fix in sources/accounts.py's _get()).
  2. core.whales.load_whale_streak() -- persisted daily Kalshi whale-
     direction streak data (data/whale_history/streak.json), a plain file
     read, no network call.

The whale streak leaderboard also does a small, local read against
data/leviathan.db via core.logger.get_titles_for_tickers() (2026-08-24) --
streak.json only ever stored the bare ticker, which tells a reader nothing
about what the bet actually is (e.g. "KXFDAAPPROVE-MDMA-27JAN01"). Looks up
the real question text for just the 20 rows actually shown, falls back to
the raw ticker if a ticker has no signals row on file.

Requires config.json (falls back to config.example.json if absent) for
sources.accounts.diagnose_discovery()'s accounts.* thresholds -- the only
page in this dashboard that reads project config rather than just the CSV
export, since it's the only page making a live external-data call.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from sources import accounts as _accounts
from core import whales as _whales
from core import logger as _logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from theme import PLOTLY_TEMPLATE, inject_css, page_header

st.set_page_config(page_title="Leviathan -- Smart Money Discovery", layout="wide")
inject_css()
page_header("Smart Money Discovery", "wallet discovery diagnostics + whale activity")
st.caption(
    "Neither panel below is gated by Leviathan's own resolved-bet count -- "
    "the discovery funnel reads live Polymarket wallet data (5 min cache), "
    "the whale leaderboard reads persisted Kalshi order-book history."
)


# The live pipeline's discovery_sample_size (1000 as of this writing) is
# tuned for an overnight run that can tolerate several minutes on this one
# step -- fine there, bad UX for someone opening a dashboard tab expecting
# a quick look. Capped here to keep an interactive page load reasonably
# fast; the 5 min cache means repeat views are instant regardless. Tuned
# empirically 2026-08-23: even 300 timed out past 120s against real
# Polymarket network latency (not just this project's own 0.1s/request
# pacing) -- 100 unique wallets is the practical ceiling for a page a
# person is actively waiting on.
_DASHBOARD_MAX_SAMPLE = 100


def _load_config() -> dict:
    path = ROOT / "config.json"
    if not path.exists():
        path = ROOT / "config.example.json"
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    accounts_cfg = dict(config.get("accounts", {}))
    accounts_cfg["discovery_sample_size"] = min(
        accounts_cfg.get("discovery_sample_size", _DASHBOARD_MAX_SAMPLE), _DASHBOARD_MAX_SAMPLE,
    )
    config = {**config, "accounts": accounts_cfg}
    return config


@st.cache_data(ttl=300)
def _cached_diagnose_discovery(config: dict) -> dict:
    return _accounts.diagnose_discovery(config)


# ── Panel 1: Wallet discovery funnel ────────────────────────────────────────

st.subheader("Wallet Discovery Funnel")
try:
    with st.spinner("Running live discovery diagnostic against Polymarket (paced to stay under their rate limit -- can take up to a minute on first load; cached for 5 min after)..."):
        result = _cached_diagnose_discovery(_load_config())
except Exception as exc:
    st.error(f"Discovery diagnostic failed: {exc}")
    result = None

if result:
    st.caption(
        f"Sample requested: {result['n_trades_requested']}  |  "
        f"Sample fetched: {result['n_trades_fetched']}  |  "
        f"Winners found: {result['n_winners']}"
    )
    funnel_df = pd.DataFrame(result["funnel"], columns=["stage", "survivors"])
    fig = px.bar(funnel_df, x="survivors", y="stage", orientation="h",
                 labels={"survivors": "wallets surviving", "stage": ""})
    fig.update_layout(PLOTLY_TEMPLATE["layout"], height=340, showlegend=False)
    fig.update_traces(marker_color=PLOTLY_TEMPLATE["layout"]["colorway"][0])
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(fig, use_container_width=True)

    st.caption("Gating metric distributions (among wallets that reached each gate) -- explains WHY the funnel narrows, not just that it does.")
    dist_rows = []
    for gate, d in result["distributions"].items():
        dist_rows.append({
            "gate": gate, "n_reached": d["n_reached"], "excluded (None)": d["excluded"],
            "min": d["min"], "median": d["median"], "p90": d["p90"], "max": d["max"],
            "% passing": d["pct_passing"],
        })
    st.dataframe(pd.DataFrame(dist_rows), use_container_width=True, hide_index=True)

st.divider()

# ── Panel 2: Whale streak leaderboard ───────────────────────────────────────

st.subheader("Whale Streak Leaderboard")
st.caption("Tickers with the longest current run of same-direction whale-size trades across scans. Real-time Kalshi order-book activity -- independent of any resolved outcome.")

streak_data = _whales.load_whale_streak()
if not streak_data:
    st.info("No whale streak data recorded yet.")
else:
    now = pd.Timestamp.now(tz="UTC")
    rows = []
    for ticker, v in streak_data.items():
        last_updated = pd.to_datetime(v.get("last_updated"), utc=True, errors="coerce")
        days_ago = (now - last_updated).days if pd.notna(last_updated) else None
        rows.append({
            "ticker": ticker, "direction": v.get("direction"),
            "streak": v.get("streak", 0), "last updated": last_updated,
            "days ago": days_ago,
        })
    streak_df = pd.DataFrame(rows).sort_values("streak", ascending=False).head(20)

    # A bare ticker (e.g. "KXFDAAPPROVE-MDMA-27JAN01") doesn't tell a reader
    # what the bet actually is -- look up each shown ticker's real question
    # text from signals, once, only for the 20 rows actually displayed.
    titles = _logger.get_titles_for_tickers(streak_df["ticker"].tolist())
    streak_df.insert(0, "market", streak_df["ticker"].map(lambda t: titles.get(t, t)))

    st.dataframe(
        streak_df, use_container_width=True, hide_index=True,
        column_config={
            "market": st.column_config.TextColumn("market", width="large"),
            "ticker": st.column_config.TextColumn("ticker", width="small"),
            "last updated": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
        },
    )
    missing_titles = int((streak_df["market"] == streak_df["ticker"]).sum())
    if missing_titles:
        st.caption(f"{missing_titles} of {len(streak_df)} shown have no title on file (ticker no longer in signals) -- showing the raw ticker for those.")
    stale = int((streak_df["days ago"].fillna(0) > 7).sum())
    if stale:
        st.caption(f"{stale} of the {len(streak_df)} shown haven't updated in over a week -- likely a closed or no-longer-scanned market, not an active streak.")
