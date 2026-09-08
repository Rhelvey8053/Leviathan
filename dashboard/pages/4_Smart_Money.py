"""
Leviathan dashboard -- Smart Money page.

backlog: smart-money-discovery-dashboard, smart-money-winning-whales-panel.
Reads three data sources, none gated by Leviathan's own resolved-bet count:

  1. sources.accounts.read_cached_winners() -- a pure, read-only peek at
     data/winning_accounts.json (no live fetch, ever -- see that
     function's own docstring). This is the SAME wallet list the live
     daily pipeline (main.py) already discovers and caches once a day via
     discover_winners()/_is_winner() -- vetted Polymarket traders with a
     real resolved win-rate/P&L track record. Distinct from panel 3 below:
     these wallets have a proven history; whale-activity rows don't.
  2. sources.accounts.diagnose_discovery() -- a funnel diagnostic over a
     FRESH live Polymarket sample (cached 5 min via st.cache_data), kept
     as a supporting "why are there 0/N winners today" explainer for
     panel 1, not the primary view -- see the rate-limit pacing fix in
     sources/accounts.py's _get().
  3. core.whales.load_whale_streak() -- persisted daily Kalshi whale-
     direction streak data (data/whale_history/streak.json), a plain file
     read, no network call. This is order-book SIZE activity, not a
     track record -- panel 3's own caption says so explicitly, since
     conflating "big money" with "smart money" was a real point of
     confusion this redesign (2026-08-31) was built to fix.

2026-08-31 redesign, prompted directly by user feedback: the old page led
with aggregate funnel counts and a streak leaderboard that showed only
raw tickers with no way to tell what was actually most recent or click
through to act on it. Real, structural fixes, not just cosmetic:
  - Panel 1 (Winning Whales) is NEW -- the actual vetted-wallet list and
    their current live positions never had a UI at all before this;
    diagnose_discovery() only ever surfaced aggregate funnel counts.
  - Panel 3's raw ticker column is gone. core.logger.get_market_meta_for_tickers()
    (new) resolves series_ticker/event_ticker so every row gets a real
    clickable Kalshi link (core.kalshi.kalshi_market_url) instead of an
    unreadable string like "KXFDAAPPROVE-MDMA-27JAN01".
  - Panel 3 gets an explicit "Longest streak" vs "Most recent" sort
    toggle -- recency was previously impossible to see at a glance.

2026-09-07 addition -- Panel 4 (Fill Tracking): core.logger's new
smart_money_fills table (record_smart_money_fills() /
backfill_smart_money_resolutions(), built same day) is the first place
this project persists individual watchlist-wallet fills with resolution
outcomes over time, instead of computing everything live against
Polymarket's API on every page load the way Panels 1-3 do. It is a
DIFFERENT data source from Panel 1's read_cached_winners() (a broader,
separately-discovered winner pool refreshed once/day) -- Panel 4 tracks
only the 20 hand-picked config.json watchlist wallets specifically, and
only once record_smart_money_fills has actually observed a qualifying
position for one. Real state as of this addition: 0 rows in the table,
0 of 20 watchlist wallets currently pass the same verification gates
Panel 1's wallets pass (see sources.accounts._verify_watchlist_trader),
so Panel 4 is built to render an honest, explicit zero -- not a chart or
metric implying a track record that doesn't exist yet. This also feeds
the backlog's own per-wallet-track-record gate (resolved_count_per_wallet_max
>= 10, tracked in backlog/checker.py and shown on the Backlog page) --
Panel 4 surfaces the same live metric here, in context, rather than
requiring a trip to a different page to see how close that gate is to
clearing. Deliberately NOT a preview of the gated wallet-tracking-dashboard
backlog item (blocked on that same gate): this panel shows raw counts and
per-wallet tallies as they exist right now, not a ranked leaderboard or
trend-over-time view implying the numbers are already decision-grade.

Requires config.json (falls back to config.example.json if absent) for
sources.accounts.diagnose_discovery()'s accounts.* thresholds -- the only
panel on this page making a live external-data call.
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
from backlog import checker as _bl_checker
from backlog.engine import load_backlog as _load_backlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from theme import LOSS_COLOR, PLOTLY_TEMPLATE, WIN_COLOR, inject_css, page_header


def _kalshi_market_url(series_ticker: str | None, event_ticker: str | None) -> str | None:
    """
    Deliberately duplicated (not imported) from core.kalshi.kalshi_market_url's
    exact confirmed pattern -- core/kalshi.py unconditionally imports the
    kalshi-sdk package at module scope for its live-trading client, which
    isn't installed in whatever Python environment happens to be running
    this dashboard (streamlit isn't in the project's own venv either, so
    the two have never needed to coexist before this page). This is the
    entire function body, nothing more: never emits a guessed URL when a
    field is missing, never the confirmed-404 bare-ticker form.
    """
    if not series_ticker or not event_ticker:
        return None
    return f"https://kalshi.com/markets/{series_ticker.lower()}/{event_ticker.lower()}"

st.set_page_config(page_title="Leviathan -- Smart Money", layout="wide")
inject_css()
page_header("Smart Money", "vetted winning wallets, live picks, and whale order-book activity")
st.caption(
    "Two different signals live on this page, easy to conflate but not the same thing. "
    "**Winning Whales** are specific, identified Polymarket wallets with a real, resolved "
    "track record -- proven skill, not just size. **Whale Activity** further down has NO "
    "wallet behind it at all -- Kalshi's order book shows a trade's size and direction, never "
    "who placed it, so a 'streak' there is tied to a MARKET (has one side kept attracting big "
    "money, scan after scan?), not to any trader. Size isn't skill, and there's no one to "
    "credit or discredit; both signals are worth watching, for different reasons."
)


def _load_config() -> dict:
    path = ROOT / "config.json"
    if not path.exists():
        path = ROOT / "config.example.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_config = _load_config()

# ── KPI row ──────────────────────────────────────────────────────────────

_winners, _winners_updated_at = _accounts.read_cached_winners()
_streak_data = _whales.load_whale_streak()

k1, k2, k3, k4 = st.columns(4)
k1.metric("Winning Wallets Tracked", len(_winners),
          help="Polymarket wallets that cleared every skill gate (minimum resolved bets, "
               "win rate, position count, P&L) in the live daily pipeline's most recent scan.")
if _winners_updated_at:
    age_h = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(_winners_updated_at, unit="s", tz="UTC")).total_seconds() / 3600
    k2.metric("Wallet List Age", f"{age_h:.1f}h",
              help="How long ago the daily pipeline last refreshed the winning-wallet list. "
                   "Updated once a day, not on this page load.")
else:
    k2.metric("Wallet List Age", "no data")
k3.metric("Active Whale Streaks", len(_streak_data),
          help="Distinct Kalshi markets currently showing a persistent large-order direction.")
if _streak_data:
    most_recent = max(
        (pd.to_datetime(v.get("last_updated"), utc=True, errors="coerce") for v in _streak_data.values()),
        default=pd.NaT,
    )
    if pd.notna(most_recent):
        streak_age_h = (pd.Timestamp.now(tz="UTC") - most_recent).total_seconds() / 3600
        k4.metric("Newest Whale Activity", f"{streak_age_h:.1f}h ago")
    else:
        k4.metric("Newest Whale Activity", "no data")
else:
    k4.metric("Newest Whale Activity", "no data")

st.divider()

# ── Panel 1: Winning Whales ──────────────────────────────────────────────

st.subheader("Winning Whales")
st.caption(
    "Polymarket wallets with a genuine resolved track record -- ranked by win rate, then "
    "realized P&L. 'Live Picks' below the leaderboard is what these specific wallets are "
    "betting on RIGHT NOW, in plain language, with a link -- the actionable part."
)

if not _winners:
    st.info(
        "No wallets currently pass every skill gate. This is a real, honest zero, not a "
        "bug -- see the Discovery Funnel panel below for exactly which gate is filtering "
        "everyone out. The list refreshes once a day from the live pipeline."
    )
else:
    board_rows = []
    for w in _winners:
        label = w.get("display_name") or w.get("name") or w.get("pseudonym") \
            or (w.get("address", "")[:10] + "…" if w.get("address") else "unknown")
        board_rows.append({
            "wallet": label,
            "win rate": w.get("win_rate"),
            "resolved bets": w.get("resolved_count"),
            "P&L ($)": w.get("resolved_cash_pnl"),
            "avg P&L (%)": w.get("resolved_avg_pct_pnl"),
            "profile": w.get("profile_url") or None,
        })
    board_df = pd.DataFrame(board_rows)
    st.dataframe(
        board_df, use_container_width=True, hide_index=True,
        column_config={
            "wallet": st.column_config.TextColumn("wallet", width="medium"),
            "win rate": st.column_config.NumberColumn("win rate", format="%.1f%%"),
            "P&L ($)": st.column_config.NumberColumn("P&L ($)", format="$%.2f"),
            "avg P&L (%)": st.column_config.NumberColumn("avg P&L (%)", format="%.1f%%"),
            "profile": st.column_config.LinkColumn("profile", display_text="View ↗"),
        },
    )

    st.markdown("**Live Picks from Winning Wallets**")
    st.caption(
        "Every open (unresolved) position these wallets currently hold, across all of them, "
        "sorted by conviction -- the size of their % P&L on that position -- so the top row "
        "is the single strongest signal on this page right now."
    )
    pick_rows = []
    for w in _winners:
        label = w.get("display_name") or w.get("name") or w.get("pseudonym") \
            or (w.get("address", "")[:10] + "…" if w.get("address") else "unknown")
        for m in w.get("active_markets", []):
            pick_rows.append({
                "wallet": label,
                "market": m.get("title") or "(untitled)",
                "side": (m.get("outcome") or "").upper(),
                "conviction (%)": m.get("pct_pnl"),
                "link": m.get("url") or None,
            })
    if not pick_rows:
        st.caption("No open positions recorded for these wallets right now.")
    else:
        picks_df = pd.DataFrame(pick_rows).sort_values(
            "conviction (%)", key=lambda s: s.abs(), ascending=False
        )
        st.dataframe(
            picks_df, use_container_width=True, hide_index=True,
            column_config={
                "market": st.column_config.TextColumn("market", width="large"),
                "conviction (%)": st.column_config.NumberColumn("conviction (%)", format="%.1f%%"),
                "link": st.column_config.LinkColumn("link", display_text="View ↗", width="small"),
            },
        )

st.divider()

# ── Panel 2: Wallet Discovery Funnel (supporting diagnostic for Panel 1) ──

st.subheader("Discovery Funnel — why the Winning Whales list looks the way it does")
hdr_col, btn_col = st.columns([5, 1])
hdr_col.caption(
    "Out of a FRESH live sample of recent Polymarket traders (independent of the cached list "
    "above), how many actually look like skilled, consistent winners rather than people who "
    "just got lucky once? Each bar is a test a wallet has to pass to stay in consideration."
)
if btn_col.button("Force refresh", help="Bypass the 5-min cache and re-run the live discovery diagnostic now."):
    st.cache_data.clear()


@st.cache_data(ttl=300)
def _cached_diagnose_discovery(config: dict) -> dict:
    accounts_cfg = dict(config.get("accounts", {}))
    accounts_cfg["discovery_sample_size"] = min(accounts_cfg.get("discovery_sample_size", 100), 100)
    return _accounts.diagnose_discovery({**config, "accounts": accounts_cfg})


try:
    with st.spinner("Running live discovery diagnostic against Polymarket (paced to stay under their rate limit -- can take up to a minute on first load; cached for 5 min after)..."):
        result = _cached_diagnose_discovery(_config)
except Exception as exc:
    st.error(f"Discovery diagnostic failed: {exc}")
    result = None

if result:
    st.caption(
        f"Sample requested: {result['n_trades_requested']}  |  "
        f"Sample fetched: {result['n_trades_fetched']}  |  "
        f"Winners found in this fresh sample: {result['n_winners']}"
    )
    with st.expander("Show the full funnel + gating-metric distributions", expanded=False):
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

# ── Panel 3: Whale Activity (Kalshi order-book size, no track record) ────

st.subheader("Whale Activity")
st.caption(
    "A 'whale' trade is an unusually large order on Kalshi's order book -- real, serious "
    "money on one side of a question. Kalshi's order book shows a trade's size and direction "
    "only, never who placed it, so this is NOT tied to any wallet or trader -- a 'streak' "
    "means one *market* has kept attracting the same big-money direction scan after scan, "
    "which is different from the wallets above having a resolved track record."
)

if not _streak_data:
    st.info("No whale streak data recorded yet.")
else:
    now = pd.Timestamp.now(tz="UTC")
    rows = []
    for ticker, v in _streak_data.items():
        last_updated = pd.to_datetime(v.get("last_updated"), utc=True, errors="coerce")
        hours_ago = (now - last_updated).total_seconds() / 3600 if pd.notna(last_updated) else None
        rows.append({
            "ticker": ticker, "direction": v.get("direction"),
            "streak": v.get("streak", 0), "last_updated": last_updated,
            "hours_ago": hours_ago,
        })
    all_streak_df = pd.DataFrame(rows)

    max_streak = int(all_streak_df["streak"].max()) if not all_streak_df.empty else 1
    f1, f2, f3, f4 = st.columns([2, 2, 1, 2])
    min_streak = f1.slider("Min streak length", 1, max(max_streak, 1), 1)
    directions = sorted(all_streak_df["direction"].dropna().unique())
    picked_directions = f2.multiselect("Direction", directions, default=directions)
    top_n = f3.number_input("Rows to show", min_value=5, max_value=100, value=20, step=5)
    sort_mode = f4.radio("Sort by", ["Most recent", "Longest streak"], horizontal=True,
                          help="'Most recent' answers 'what just happened' -- 'Longest streak' "
                               "answers 'what's been persistently true for a while.'")

    filtered_df = all_streak_df[
        (all_streak_df["streak"] >= min_streak)
        & (all_streak_df["direction"].isin(picked_directions))
    ].copy()

    sort_col = "last_updated" if sort_mode == "Most recent" else "streak"
    streak_df = filtered_df.sort_values(sort_col, ascending=False).head(int(top_n)).copy()

    if streak_df.empty:
        st.info("No whale activity matches the current filters.")
    else:
        chart_col, split_col = st.columns([3, 2])
        with chart_col:
            fig = px.bar(
                streak_df.sort_values("streak"), x="streak", y="ticker", orientation="h",
                color="direction", color_discrete_map={"YES": WIN_COLOR, "NO": LOSS_COLOR},
                labels={"streak": "current streak", "ticker": ""},
            )
            fig.update_layout(PLOTLY_TEMPLATE["layout"], height=max(220, 26 * len(streak_df)),
                               legend=dict(orientation="h", y=-0.08))
            fig.update_yaxes(showticklabels=False)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Ticker labels hidden on the chart -- see the table below for readable market names.")
        with split_col:
            dir_split = streak_df["direction"].value_counts()
            fig2 = px.pie(names=dir_split.index, values=dir_split.values, hole=0.5,
                           color=dir_split.index, color_discrete_map={"YES": WIN_COLOR, "NO": LOSS_COLOR})
            fig2.update_layout(PLOTLY_TEMPLATE["layout"], height=220,
                                legend=dict(orientation="h", y=-0.1),
                                title=dict(text="Direction split (this view)", font=dict(size=13)))
            st.plotly_chart(fig2, use_container_width=True)

        # Real, human-readable market name + a clickable Kalshi link replace
        # the raw ticker entirely -- "KXFDAAPPROVE-MDMA-27JAN01" told a
        # reader nothing; a title + a "View" link does.
        meta = _logger.get_market_meta_for_tickers(streak_df["ticker"].tolist())
        streak_df["market"] = streak_df["ticker"].map(
            lambda t: meta.get(t, {}).get("title") or t
        )
        streak_df["kalshi_link"] = streak_df["ticker"].map(
            lambda t: _kalshi_market_url(meta.get(t, {}).get("series_ticker"), meta.get(t, {}).get("event_ticker"))
        )

        display_df = streak_df[["market", "direction", "streak", "hours_ago", "kalshi_link"]].rename(
            columns={"hours_ago": "hours ago"}
        )
        st.dataframe(
            display_df, use_container_width=True, hide_index=True,
            column_config={
                "market": st.column_config.TextColumn("market", width="large"),
                "hours ago": st.column_config.NumberColumn("hours ago", format="%.1f"),
                "kalshi_link": st.column_config.LinkColumn("kalshi", display_text="View ↗", width="small"),
            },
        )
        missing_titles = int((streak_df["market"] == streak_df["ticker"]).sum())
        if missing_titles:
            st.caption(f"{missing_titles} of {len(streak_df)} shown have no title on file (ticker no longer in signals) -- market name falls back to the raw ticker for those.")
        stale = int((streak_df["hours_ago"].fillna(0) > 168).sum())
        if stale:
            st.caption(f"{stale} of the {len(streak_df)} shown haven't updated in over a week -- likely a closed or no-longer-scanned market, not an active streak.")

st.divider()

# ── Panel 4: Fill Tracking -- the new persisted per-fill ledger ──────────
#
# Distinct from Panel 1: read_cached_winners() reflects a broader,
# separately-discovered winner pool refreshed once/day from live
# discovery sampling. This panel is the watchlist-specific ledger --
# every qualifying fill core.logger.record_smart_money_fills() has
# actually observed for one of the 20 hand-picked config.json watchlist
# wallets, plus its resolution once backfill_smart_money_resolutions()
# confirms one. It starts empty and grows one scan at a time; there is
# no live-API fallback here the way Panels 1-3 have -- this section can
# ONLY show what has actually been persisted.

WALLET_TRACK_RECORD_GATE_N = 10  # mirrors backlog: per-wallet-track-record's own trigger

st.subheader("Fill Tracking")
st.caption(
    "A new, separate record from everything above: every individual fill "
    "core.logger has actually observed and persisted for a watchlist wallet, plus its "
    "outcome once the position resolves. This builds up scan by scan, starting from zero -- "
    "it is not a live snapshot recomputed on page load like Panels 1-3 above."
)

_watchlist = _config.get("accounts", {}).get("watchlist", [])
_fills = _logger.get_smart_money_fills()
_fills_df = pd.DataFrame(_fills)

_wallets_with_fills = int(_fills_df["wallet"].nunique()) if not _fills_df.empty else 0
_open_n = int((_fills_df["resolved"] == 0).sum()) if not _fills_df.empty else 0
_resolved_n = int((_fills_df["resolved"] == 1).sum()) if not _fills_df.empty else 0

try:
    _bl_backlog = _load_backlog(ROOT / "backlog" / "backlog.json")
    _bl_metrics = _bl_checker.compute_metrics()
    _bl_items = {i["id"]: i for i in _bl_backlog.get("items", [])}
    _gate_item = _bl_items.get("per-wallet-track-record")
    _wallet_max = _bl_metrics.get("resolved_count_per_wallet_max", 0)
    _gate_progress = _bl_checker.gate_progress_str(_gate_item, _bl_metrics) if _gate_item else None
except Exception:
    _wallet_max = _logger.get_resolved_count_per_wallet_max()  # direct fallback, same underlying query
    _gate_progress = None

fk1, fk2, fk3, fk4 = st.columns(4)
fk1.metric("Watchlist Size", len(_watchlist),
           help="Hand-picked candidate wallets in config.json. Being on this list does not mean "
                "fills are being recorded for it yet -- see 'Wallets With Recorded Fills'.")
fk2.metric("Wallets With Recorded Fills", _wallets_with_fills,
           help="Distinct watchlist wallets that have cleared sources.accounts._verify_watchlist_trader's "
                "gates at least once and had a real fill written to smart_money_fills. Can be, and "
                "currently is, far smaller than Watchlist Size.")
fk3.metric("Open / Resolved Fills", f"{_open_n} / {_resolved_n}",
           help="Open = currently-held position, outcome not yet known. Resolved = "
                "backfill_smart_money_resolutions() confirmed the market settled.")
fk4.metric("Best-Tracked Wallet", f"{_wallet_max}/{WALLET_TRACK_RECORD_GATE_N} resolved",
           help=f"The single wallet with the most resolved fills, out of the {WALLET_TRACK_RECORD_GATE_N} "
                "this project's own backlog gate (per-wallet-track-record) requires before any "
                "per-wallet win rate here is treated as more than a raw tally -- see the Backlog page.")

if _gate_progress:
    st.caption(f"Backlog gate `per-wallet-track-record`: {_gate_progress}")

if _fills_df.empty:
    st.info(
        f"0 resolved fills recorded yet, across {_wallets_with_fills} wallets that have ever had a fill "
        f"tracked (0 of the {len(_watchlist)} watchlist wallets currently pass the qualification gates "
        "that would let one be recorded -- see sources.accounts._verify_watchlist_trader). This is a "
        "real, honest zero, not a bug: the smart_money_fills table was only built 2026-09-07, and no "
        "watchlist wallet has cleared the qualification gates since. It will start filling in as the "
        "daily scan keeps running and watchlist wallets open new positions -- this section will "
        "populate on its own, nothing here needs to be manually seeded."
    )
else:
    st.markdown("**Per-Wallet Tally**")
    st.caption(
        "Win rate shown for transparency only, not as a trustworthy signal, until a wallet reaches "
        f"{WALLET_TRACK_RECORD_GATE_N} resolved fills (this project's own reliability floor -- see the "
        "Best-Tracked Wallet metric above and the Backlog page's per-wallet-track-record gate). hit = "
        "resolved_pct_pnl > 0, the same win/loss definition sources.accounts._score_wallet already uses."
    )
    _wallet_label = _fills_df["trader_name"].fillna(_fills_df["wallet"])
    _fills_df = _fills_df.assign(wallet_label=_wallet_label)
    _summary_rows = []
    for label, g in _fills_df.groupby("wallet_label"):
        open_n = int((g["resolved"] == 0).sum())
        resolved_n = int((g["resolved"] == 1).sum())
        wins_n = int((g["hit"] == 1).sum())
        _summary_rows.append({
            "wallet": label,
            "open fills": open_n,
            "resolved fills": resolved_n,
            "wins": wins_n,
            "win rate": (wins_n / resolved_n * 100) if resolved_n > 0 else None,
            "sample": (
                f"n={resolved_n} (below the n>={WALLET_TRACK_RECORD_GATE_N} reliability floor)"
                if resolved_n < WALLET_TRACK_RECORD_GATE_N
                else f"n={resolved_n} (meets the reliability floor)"
            ),
        })
    _summary = pd.DataFrame(_summary_rows).sort_values(
        ["resolved fills", "open fills"], ascending=False
    )
    st.dataframe(
        _summary[["wallet", "open fills", "resolved fills", "wins", "win rate", "sample"]],
        use_container_width=True, hide_index=True,
        column_config={
            "win rate": st.column_config.NumberColumn("win rate", format="%.1f%%"),
            "sample": st.column_config.TextColumn("sample", width="medium"),
        },
    )

    st.markdown("**Raw Fill History**")
    st.caption(
        "Every individual fill on record, newest-observed first. kalshi_ticker is a schema column that "
        "no writer populates yet (the raw position data doesn't carry a cross-referenced Kalshi ticker) "
        "-- omitted below rather than shown as a permanently-empty column."
    )
    hist_wallets = sorted(_wallet_label.unique())
    hist_col1, hist_col2 = st.columns([2, 2])
    picked_wallets = hist_col1.multiselect("Wallet", hist_wallets, default=hist_wallets)
    status_pick = hist_col2.radio("Status", ["All", "Open", "Resolved"], horizontal=True)

    hist_df = _fills_df[_fills_df["wallet_label"].isin(picked_wallets)]
    if status_pick == "Open":
        hist_df = hist_df[hist_df["resolved"] == 0]
    elif status_pick == "Resolved":
        hist_df = hist_df[hist_df["resolved"] == 1]

    if hist_df.empty:
        st.caption("No fills match the current filters.")
    else:
        hist_df = hist_df.copy()
        hist_df["market"] = hist_df["poly_title"].fillna(hist_df["poly_slug"])
        hist_df["status"] = hist_df["resolved"].map({0: "open", 1: "resolved"})
        hist_df["outcome (win/loss)"] = hist_df.apply(
            lambda r: ("win" if r["hit"] == 1 else "loss") if r["resolved"] == 1 else "--", axis=1
        )
        display_cols = [
            "wallet_label", "market", "outcome", "entry_price", "position_val", "status",
            "outcome (win/loss)", "resolved_pct_pnl", "first_seen_at", "last_seen_at",
        ]
        st.dataframe(
            hist_df[display_cols].rename(columns={"wallet_label": "wallet", "outcome": "side"}),
            use_container_width=True, hide_index=True,
            column_config={
                "market": st.column_config.TextColumn("market", width="large"),
                "entry_price": st.column_config.NumberColumn("entry price", format="%.3f"),
                "position_val": st.column_config.NumberColumn("position val ($)", format="$%.2f"),
                "resolved_pct_pnl": st.column_config.NumberColumn("resolved P&L (%)", format="%.1f%%"),
                "first_seen_at": st.column_config.TextColumn("first seen", width="medium"),
                "last_seen_at": st.column_config.TextColumn("last seen", width="medium"),
            },
        )
