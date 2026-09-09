"""
Leviathan dashboard -- Trader Profile page.

backlog: (added 2026-09-08, directly following the win/loss measurement fix
in sources.accounts._score_wallet). Answers, for ONE wallet at a time, in
plain language: why is (or isn't) this trader being tracked, what are they
betting on right now, and what does their actual bet-by-bet history look
like -- not just an aggregate win-rate number.

Reached two ways:
  1. Click "Open Profile ->" next to a wallet on the Smart Money page (either
     the Winning Whales leaderboard or the watchlist picker) -- that page sets
     st.session_state["profile_wallet_address"/"profile_wallet_label"] and
     calls st.switch_page() here.
  2. Directly from the sidebar -- in that case no wallet is pre-selected, so
     this page offers its own picker (same two pools) plus a free-text
     address field, so it still works as a standalone destination.

All data comes from sources.accounts.get_wallet_profile(), a single live
fetch_user_positions() call plus permanently-cached true-resolution lookups
(sources.accounts.fetch_resolutions_for_positions) -- the same ground-truth
win/loss logic _score_wallet uses. Cached 5 min per wallet (st.cache_data)
so clicking between panels on this page doesn't re-fetch every rerun.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from sources import accounts as _accounts

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from theme import LOSS_COLOR, WIN_COLOR, inject_css, page_header

st.set_page_config(page_title="Leviathan -- Trader Profile", layout="wide")
inject_css()
page_header("Trader Profile", "one wallet, in plain language: why it's tracked, its current bets, and its full history")


def _load_config() -> dict:
    path = ROOT / "config.json"
    if not path.exists():
        path = ROOT / "config.example.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_config = _load_config()

# ── Wallet picker ────────────────────────────────────────────────────────

_winners, _ = _accounts.read_cached_winners()
_watchlist = _config.get("accounts", {}).get("watchlist", [])

_options: dict[str, str] = {}
for w in _winners:
    label = w.get("display_name") or w.get("name") or w.get("pseudonym") \
        or (w.get("address", "")[:10] + "…")
    if w.get("address"):
        _options[f"{label} (Winning Whales)"] = w["address"]
for w in _watchlist:
    label = w.get("name") or w["address"][:10] + "…"
    if w.get("address"):
        _options[f"{label} (Watchlist)"] = w["address"]

pre_address = st.session_state.get("profile_wallet_address")
pre_label   = st.session_state.get("profile_wallet_label")

pick_col, addr_col = st.columns([2, 2])
with pick_col:
    default_key = next((k for k, v in _options.items() if v == pre_address), None)
    dropdown_choice = st.selectbox(
        "Pick a tracked wallet",
        ["(none)"] + list(_options.keys()),
        index=(list(_options.keys()).index(default_key) + 1) if default_key else 0,
    )
with addr_col:
    manual_address = st.text_input(
        "...or paste any Polymarket wallet address",
        value="" if default_key else (pre_address or ""),
        placeholder="0x...",
    )

if manual_address.strip():
    address = manual_address.strip()
    display_label = address[:10] + "…"
elif dropdown_choice != "(none)":
    address = _options[dropdown_choice]
    display_label = dropdown_choice.rsplit(" (", 1)[0]
elif pre_address:
    address = pre_address
    display_label = pre_label or (pre_address[:10] + "…")
else:
    address = None
    display_label = None

if not address:
    st.info("Pick a wallet above (or come here by clicking \"Open Profile →\" on the Smart Money page) to see its full profile.")
    st.stop()

st.session_state["profile_wallet_address"] = address
st.session_state["profile_wallet_label"] = display_label


@st.cache_data(ttl=300)
def _cached_profile(addr: str, config: dict) -> dict | None:
    return _accounts.get_wallet_profile(addr, config)


with st.spinner(f"Loading {display_label}'s full position history from Polymarket..."):
    try:
        profile = _cached_profile(address, _config)
    except Exception as exc:
        st.error(f"Couldn't load this wallet's data: {exc}")
        st.stop()

if not profile:
    st.warning(f"Polymarket has no position data for this wallet ({address}). Double-check the address.")
    st.stop()

stats      = profile["stats"] or {}
checklist  = profile["checklist"]
passed_all = profile["classification"] == "PASS"

st.markdown(f"### {display_label}")
st.caption(f"`{address}`  ·  [View on Polymarket ↗](https://polymarket.com/profile/{address})")

st.divider()

# ── Why is this trader tracked? ──────────────────────────────────────────

st.subheader("Why is this trader tracked?" if passed_all else "Why isn't this trader tracked yet?")
st.caption(
    "Every one of these has to be true before Leviathan treats a wallet as a verified "
    "winner worth watching. Win/loss here is checked against Polymarket's own official "
    "market-resolution data (fixed 2026-09-08) -- not the raw P&L fields Polymarket's "
    "position API shows, which read as a near-total loss for every resolved bet "
    "regardless of whether it actually won."
)

if passed_all:
    st.success("✅ Passes every check below — this is a verified, tracked winner.")
else:
    st.warning("This wallet does not currently pass every check — shown for transparency, not as a recommendation.")

for item in checklist:
    icon = "✅" if item["passed"] else "❌"
    st.markdown(f"{icon} **{item['label']}** — {item['detail']}")

st.divider()

# ── Key measurements ─────────────────────────────────────────────────────

st.subheader("Key measurements")
m1, m2, m3, m4 = st.columns(4)
wr = stats.get("win_rate")
m1.metric("Win Rate", f"{wr:.1f}%" if wr is not None else "no data",
          help="Share of resolved bets that actually settled in this wallet's favor.")
m2.metric("Resolved Bets", stats.get("resolved_count", 0),
          help="How many of this wallet's bets we can verify the true outcome of.")
cash = stats.get("resolved_cash_pnl")
m3.metric("Total P&L ($)", f"${cash:,.2f}" if cash is not None else "no data",
          help="Real dollars: cash already banked from selling, plus the true payout on "
               "anything held to resolution, minus what was paid — added up across every "
               "resolved bet.")
pct = stats.get("resolved_avg_pct_pnl")
m4.metric("Avg Return / Bet", f"{pct:+.1f}%" if pct is not None else "no data",
          help="Average return per resolved bet, as a percentage of what was staked.")

st.divider()

# ── Current bets ──────────────────────────────────────────────────────────

st.subheader("Current Bets")
st.caption("What this wallet is holding right now — outcome not yet known. Sorted by conviction (the size of their live % P&L on the position).")

current_bets = profile["current_bets"]
if not current_bets:
    st.info("No open positions recorded for this wallet right now.")
else:
    bets_df = pd.DataFrame(current_bets)
    st.dataframe(
        bets_df, use_container_width=True, hide_index=True,
        column_config={
            "title":          st.column_config.TextColumn("market", width="large"),
            "side":           st.column_config.TextColumn("side", width="small"),
            "value":          st.column_config.NumberColumn("position value ($)", format="$%.2f"),
            "conviction_pct": st.column_config.NumberColumn("conviction (%)", format="%.1f%%"),
            "url":            st.column_config.LinkColumn("link", display_text="View ↗", width="small"),
        },
    )

st.divider()

# ── Bet history ────────────────────────────────────────────────────────────

st.subheader("Resolved Bet History")
st.caption("Every bet with a known outcome — the receipts behind the win rate above. Sorted by the size of the real dollar result.")

history = profile["bet_history"]
if not history:
    st.info("No resolved bets with a verified outcome for this wallet yet.")
else:
    hist_df = pd.DataFrame(history)
    hist_df["result"] = hist_df["won"].map({True: "WON", False: "LOST"})
    wins_n = int(hist_df["won"].sum())
    st.caption(f"{wins_n} won, {len(hist_df) - wins_n} lost — {wins_n / len(hist_df) * 100:.1f}% win rate on {len(hist_df)} verified bets.")
    st.dataframe(
        hist_df[["title", "side", "result", "pnl", "pct_pnl", "url"]], use_container_width=True, hide_index=True,
        column_config={
            "title":   st.column_config.TextColumn("market", width="large"),
            "side":    st.column_config.TextColumn("side", width="small"),
            "result":  st.column_config.TextColumn("result", width="small"),
            "pnl":     st.column_config.NumberColumn("P&L ($)", format="$%.2f"),
            "pct_pnl": st.column_config.NumberColumn("P&L (%)", format="%.1f%%"),
            "url":     st.column_config.LinkColumn("link", display_text="View ↗", width="small"),
        },
    )
