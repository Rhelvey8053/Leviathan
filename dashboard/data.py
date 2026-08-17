"""
Shared data loading for the Leviathan Streamlit dashboard.

Reads the pipeline's existing PowerBI CSV export (core/export_to_csv.py) --
never the SQLite DB directly, and never main.py. Read-only, additive module.

--- DATA CONTRACT (verified against a real export, 2026-08-16) --------------
signals.csv -- 317 rows x 67 cols at time of writing. Columns actually used
by this dashboard, with real dtype and how populated they are:

  call_id            str   always present, unique per signal
  run_id             str   present on most rows (a handful of legacy rows lack it)
  date               str   "YYYY-MM-DD", always present
  timestamp          str   ISO8601 UTC, always present
  ticker             str   always present
  title              str   present on nearly all rows
  source             str   "paper" | "real_fill" | "research_probe"
                            (signal population type -- NOT a detection
                            platform. There is no Kalshi-vs-Polymarket
                            column; see flag_path below.)
  direction          str   "YES" | "NO" | "PASS" (PASS = scanned, no signal)
  confidence         str   "HIGH" | "MED" | "LOW", ~98% populated
  flag_path          str   "DRIFT" | "HEURISTIC" | "RESOLVE_FIRST" in the
                            current export; "CROSS_MARKET" exists in the
                            scoring code (core/scorer.py) for signals
                            corroborated against Polymarket, just has 0 rows
                            in this snapshot. Nearest real proxy for
                            "detection path" -- used instead of a
                            Kalshi-vs-Polymarket split, which doesn't exist.
  category           str   ~29% populated, otherwise blank ("Uncategorized")
  market_price       float ~99.7% populated
  edge               float raw edge, ~93% populated
  net_edge           float spread-adjusted edge -- does NOT model fees
                            (core/scanner.py: net_edge_after_fee is the
                            fee-adjusted figure, separately, only ~12% populated)
  is_resolved        int   0/1, always present -- used for "active" KPI
  is_win / result     -    only populated once a signal resolves (~15%)
  leviathan_score    float composite score, ~90% populated
  lv_band            str   "A"-"D" / "Unscored", always present
  whale_detected     int   0/1, always present
  market_drift_pp    float CLV-drift, the credibility metric -- only ~4%
                            populated right now (13/317). Treated as a
                            small-n metric; the dashboard always shows n
                            alongside it and states plainly when there
                            isn't enough data yet.
  volume, open_interest, resolved_at, days_to_resolution
                            real columns (added 2026-08-16), currently 0/317
                            populated -- too new to have any data yet.
                            # TODO: wire these in once resolved signals with
                            volume/open_interest exist; omitted from charts
                            for now rather than shipping permanently-empty ones.

runs.csv -- one row per pipeline run. Columns used:
  run_id, timestamp, markets_scanned, signals_generated, whale_flags, cost_usd
  markets_scanned is a single combined count (Kalshi markets scanned this
  run) -- there is no separate Polymarket-scanned count anywhere in the
  pipeline's output, so the KPI below is labeled "Markets Scanned (Kalshi)".
  # TODO: add a Polymarket-scanned counter upstream if that split matters later.
------------------------------------------------------------------------------
"""

import os
from pathlib import Path

import pandas as pd
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "powerbi_export"
DATA_DIR = Path(os.environ.get("LEVIATHAN_DASHBOARD_DATA_DIR", str(DEFAULT_DATA_DIR)))

SIGNALS_CSV = DATA_DIR / "signals.csv"
RUNS_CSV = DATA_DIR / "runs.csv"


class DataLoadError(Exception):
    pass


@st.cache_data(ttl=60)
def load_signals() -> pd.DataFrame:
    if not SIGNALS_CSV.exists():
        raise DataLoadError(f"signals.csv not found at {SIGNALS_CSV}")
    df = pd.read_csv(SIGNALS_CSV)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    return df


@st.cache_data(ttl=60)
def load_runs() -> pd.DataFrame:
    if not RUNS_CSV.exists():
        raise DataLoadError(f"runs.csv not found at {RUNS_CSV}")
    df = pd.read_csv(RUNS_CSV)
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    return df


def data_freshness():
    """Returns the mtime of signals.csv, or None if it doesn't exist."""
    if not SIGNALS_CSV.exists():
        return None
    import datetime
    return datetime.datetime.fromtimestamp(SIGNALS_CSV.stat().st_mtime, tz=datetime.timezone.utc)


CONFIDENCE_ORDER = ["LOW", "MED", "HIGH"]
