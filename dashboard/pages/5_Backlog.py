"""
Leviathan dashboard -- Backlog page.

Replaces the monday.com board (trial expired 2026-08-30) as the internal
place to browse the backlog -- reads backlog/backlog.json directly, live,
every page load. No sync step, no external service: this file already was
this project's real source of truth all along (monday.com was always a
secondary, human-facing mirror of it -- see scripts/monday_sync.py,
now retired).

Live gate metrics come from backlog.checker.compute_metrics() (reads
data/leviathan.db read-only) -- the same function every gate-unlock
decision in this project already runs through, so a locked/blocked item's
progress shown here is never a stale, separately-computed number.
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from backlog.engine import load_backlog
from backlog import checker as _checker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from theme import inject_css, page_header

st.set_page_config(page_title="Leviathan -- Backlog", layout="wide")
inject_css()
page_header("Backlog", "what's ready, what's gated, and why")
st.caption(
    "This project's real backlog lives in backlog/backlog.json, not any external tool -- "
    "this page reads it directly, live, so it's never out of sync with what a fix actually "
    "changed. 'Ready' means actionable now. 'Locked'/'Blocked' items are gated on a live "
    "metric or a dependency, shown below with real-time progress toward it."
)

BACKLOG_PATH = ROOT / "backlog" / "backlog.json"


@st.cache_data(ttl=60)
def _load():
    backlog = load_backlog(BACKLOG_PATH)
    metrics = _checker.compute_metrics()
    return backlog, metrics


try:
    backlog, metrics = _load()
except Exception as exc:
    st.error(f"Failed to load backlog: {exc}")
    st.stop()

items = backlog.get("items", [])
if not items:
    st.warning("backlog.json has no items.")
    st.stop()

df = pd.DataFrame(items)
df["priority"] = df["priority"].fillna(9).astype(int)

# ── KPI row ──────────────────────────────────────────────────────────────

counts = df["status"].value_counts()
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total", len(df))
k2.metric("Ready", int(counts.get("ready", 0)), help="Actionable now -- no gate, no unresolved dependency.")
k3.metric("Locked", int(counts.get("locked", 0)), help="Gated on a live metric clearing a threshold (e.g. resolved_count >= 30).")
k4.metric("Blocked", int(counts.get("blocked", 0)), help="Waiting on another backlog item to finish first, or a manual/policy decision.")
k5.metric("Done", int(counts.get("done", 0)))

data_gaps = metrics.get("_data_gaps", [])
gap_note = lambda k: " *(not tracked yet)*" if k in data_gaps else ""
st.caption(
    f"Live gate metrics -- resolved_count: **{metrics.get('resolved_count', 0)}**  |  "
    f"resolved_count_per_category_max: **{metrics.get('resolved_count_per_category_max', 0)}**  |  "
    f"resolved_count_per_wallet_max: **{metrics.get('resolved_count_per_wallet_max', 0)}**{gap_note('resolved_count_per_wallet_max')}  |  "
    f"fills_count: **{metrics.get('fills_count', 0)}**"
)

st.divider()

# ── Ready items -- what's actionable now ────────────────────────────────

st.subheader("Ready now")
ready_df = df[df["status"] == "ready"].sort_values(["priority", "id"]).copy()
if ready_df.empty:
    st.info("Nothing is currently ready -- every open item is either locked, blocked, or done.")
else:
    ready_df["summary"] = ready_df["action"].apply(lambda a: _checker._summarize_action(a or "", max_len=160))
    st.dataframe(
        ready_df[["priority", "id", "area", "summary"]],
        use_container_width=True, hide_index=True,
        column_config={
            "priority": st.column_config.NumberColumn("P", width="small"),
            "id": st.column_config.TextColumn("id", width="medium"),
            "area": st.column_config.TextColumn("area", width="small"),
            "summary": st.column_config.TextColumn("what needs doing", width="large"),
        },
    )

st.divider()

# ── Full backlog -- filterable ───────────────────────────────────────────

st.subheader("Full backlog")
f1, f2, f3 = st.columns([2, 2, 3])
status_opts = sorted(df["status"].unique())
picked_status = f1.multiselect("Status", status_opts, default=status_opts)
area_opts = sorted(df["area"].dropna().unique())
picked_area = f2.multiselect("Area", area_opts, default=area_opts)
search = f3.text_input("Search (id, title, action text)", "")

filtered = df[df["status"].isin(picked_status) & df["area"].isin(picked_area)].copy()
if search:
    needle = search.lower()
    mask = (
        filtered["id"].str.lower().str.contains(needle)
        | filtered["title"].fillna("").str.lower().str.contains(needle)
        | filtered["action"].fillna("").str.lower().str.contains(needle)
    )
    filtered = filtered[mask]


def _gate_or_deps(row) -> str:
    if row["status"] == "blocked" and row.get("depends_on"):
        deps = ", ".join(row["depends_on"])
        return f"waiting on: {deps}"
    if row["status"] == "locked":
        return _checker.gate_progress_str(row.to_dict(), metrics)
    if row["status"] == "blocked":
        return "manual/policy hold -- see notes"
    return ""


filtered = filtered.sort_values(["priority", "id"])
if filtered.empty:
    st.info("No items match the current filters.")
else:
    filtered["gate / dependency"] = filtered.apply(_gate_or_deps, axis=1)
    st.dataframe(
        filtered[["priority", "id", "area", "status", "gate / dependency"]],
        use_container_width=True, hide_index=True,
        column_config={
            "priority": st.column_config.NumberColumn("P", width="small"),
            "id": st.column_config.TextColumn("id", width="medium"),
            "area": st.column_config.TextColumn("area", width="small"),
            "status": st.column_config.TextColumn("status", width="small"),
            "gate / dependency": st.column_config.TextColumn("gate / dependency", width="large"),
        },
    )
    st.caption(f"{len(filtered)} of {len(df)} items shown.")

st.divider()

# ── Item detail ──────────────────────────────────────────────────────────

st.subheader("Item detail")
detail_ids = filtered["id"].tolist() if not filtered.empty else df["id"].tolist()
picked_id = st.selectbox("Pick an item to see its full action/notes text", detail_ids)
if picked_id:
    item = df[df["id"] == picked_id].iloc[0].to_dict()
    st.markdown(f"#### `{item['id']}`  —  {item.get('title', '')}")
    badge_col1, badge_col2, badge_col3 = st.columns(3)
    badge_col1.caption(f"**Status:** {item['status']}")
    badge_col2.caption(f"**Priority:** {item['priority']}")
    badge_col3.caption(f"**Area:** {item.get('area', '')}")

    if item.get("depends_on"):
        st.caption(f"**Depends on:** {', '.join(item['depends_on'])}")
    trigger_conds = (item.get("trigger") or {}).get("all", [])
    if trigger_conds:
        st.caption(f"**Gate:** {_checker._gate_str(item, metrics) if item['status'] != 'locked' else _checker.gate_progress_str(item, metrics)}")

    st.markdown("**Action**")
    st.write(item.get("action", "") or "_(none)_")
    if item.get("notes"):
        st.markdown("**Notes**")
        st.write(item["notes"])
