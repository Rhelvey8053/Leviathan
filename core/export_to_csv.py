"""
export_to_csv.py — Export leviathan.db tables to CSV for Power BI.

Writes data/powerbi_export/signals.csv, data/powerbi_export/scan_log.csv,
and data/powerbi_export/runs.csv. stdlib only (csv + sqlite3 + datetime)
plus one intra-project reuse: core.kalshi.kalshi_market_url() for the
computed kalshi_url column, so the URL-building pattern has exactly one
implementation (already relied on by core/report.py for the email's
clickable links) rather than a second copy here that could drift from it.
kalshi-sdk is already a hard requirement of this whole project via
core/kalshi.py, so this adds no new dependency.
Importable without side effects; runnable standalone via __main__.

signals.csv vs scan_log.csv (2026-08-16 cleanup): the signals table logs
every scan decision, including PASS (scanner looked, found no actionable
edge) — historically that meant ~85% of exported rows were never real
bets, which buried the ~15% that were in a wall of PASS-shaped blanks.
signals.csv now holds only real bets (direction YES/NO); scan_log.csv
holds every row, PASS included, for anyone who wants full scan history.
Both share the same WHITELIST/column shape — scan_log.csv is the
superset, signals.csv the actionable subset.
"""

import csv
import os
import sqlite3
from datetime import datetime

from core.kalshi import kalshi_market_url
from core.logger import brier_component

_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH    = os.path.join(_ROOT, "data", "leviathan.db")
EXPORT_DIR = os.path.join(_ROOT, "data", "powerbi_export")

# String columns where NULL should become "" so Power BI DAX comparisons
# (= "" and = "LOSS") work correctly. Numeric columns are left as-is.
_STRING_COLS = frozenset({
    "result", "outcome", "direction", "confidence", "flag_path", "source",
    "time_horizon", "heuristic_direction", "heuristic_label",
    "whale_direction", "ticker", "title", "run_id", "call_id",
    "close_time", "lv_band", "date", "timestamp", "resolved_at",
    "category", "ob_direction", "consensus_dir", "smart_money_dir",
    "event_ticker", "series_ticker", "kalshi_url",
})

# Sentinel strings that SQLite/Python can produce for missing data.
_NULL_STRINGS = frozenset({"None", "nan", "NaT"})

# Computed columns that are derived at export time, not stored in the DB.
_COMPUTED_COLS = frozenset({
    "is_resolved", "is_win", "confidence_rank", "horizon_rank",
    "date", "pnl_scaled", "lv_band", "brier_scorer", "brier_market",
    "kalshi_url", "days_to_resolution", "pre_scoring_era",
})

# Analysis-relevant columns written to signals.csv / scan_log.csv, grouped
# by what they describe rather than by when they were added (2026-08-16
# reorg -- the old flat append-only order made a 68-column row unreadable
# in a spreadsheet). Pipeline plumbing (from_signal, fill_count, fill_fee,
# outcome, direction_aligned, entry_price, signal_call_id, logged_under,
# resolution_date) is still excluded. our_estimate is kept because Brier is
# (outcome - probability)^2 and without it a dashboard would have to derive
# probability as market_price + edge, which breaks wherever edge is blank.
# brier_scorer/brier_market are computed here from our_estimate/market_price
# via the same core.logger.brier_component() analysis/calibration.py's
# aggregates call, so the export and the calibration script can never
# report different numbers for the same row. run_id is kept as an explicit
# foreign key into runs.csv (powerbi-schema-hardening) -- blank only for
# rows that never originated from a scan run (real_fill/research_probe),
# never coerced or guessed via nearest-timestamp matching.
WHITELIST = [
    # Identity
    "call_id", "run_id", "date", "timestamp", "ticker", "title",

    # Classification
    "source", "direction", "confidence", "confidence_rank",
    "flag_path", "time_horizon", "horizon_rank", "category",
    "pre_scoring_era",

    # Pricing / edge
    "market_price", "our_estimate", "edge", "net_edge",
    "net_edge_after_fee", "ev_after_fee_per_contract", "base_rate",

    # Scoring / decision
    "leviathan_score", "lv_band", "sig_edge", "sig_drift", "sig_br_none",
    "watchlist_signal", "short_horizon", "confidence_downgraded", "second_pass",

    # Sub-signals: whale_direction/whale_max_trade_size, net_edge_after_fee/
    # ev_after_fee_per_contract, heuristic_direction, and category were
    # previously captured in the DB but silently dropped at export -- added
    # rather than left invisible to the dashboard. ob_flag/ob_imbalance/
    # ob_direction, spread_wide/spread_pct, ext_estimate/ext_edge/
    # ext_n_signals/ext_alpha, and poly_price/poly_price_gap/consensus_gap/
    # consensus_dir/smart_money_count/smart_money_dir are new columns
    # (2026-07-27) -- previously computed fresh every run for the
    # prompt/report and discarded, never persisted at all.
    # poly_net_price_gap (2026-09-04, backlog: cross-venue-expansion) is
    # poly_price_gap after modeling both venues' own taker fees -- purely
    # auxiliary/informational, same as poly_price_gap itself.
    "whale_detected", "whale_direction", "whale_max_trade_size",
    "heuristic_label", "heuristic_direction",
    "ob_flag", "ob_imbalance", "ob_direction", "spread_wide", "spread_pct",
    "ext_estimate", "ext_edge", "ext_n_signals", "ext_alpha", "confluence_count",
    "poly_price", "poly_price_gap", "poly_net_price_gap", "consensus_gap", "consensus_dir",
    "smart_money_count", "smart_money_dir",

    # Outcome / resolution. market_drift_pp is the GOAL_subscriber_report.md
    # Phase 4 CLV-style drift metric -- reasoning/sources (Phase 3) are
    # deliberately NOT whitelisted here, since free-text narrative and a
    # JSON blob don't fit a numeric analytics row the way every other
    # whitelisted column does; they're read directly from the DB by
    # core/report.py's subscriber renderer instead. resolved_at (set once
    # in resolve_outcomes() alongside outcome/result) and the
    # days_to_resolution computed from it answer "how long did this
    # actually take," which close_time (the market's SCHEDULED close, not
    # its actual settlement time) can only approximate.
    "result", "is_resolved", "is_win", "pnl_if_traded", "pnl_scaled",
    "resolved_at", "days_to_resolution", "brier_scorer", "brier_market",
    "market_drift_pp",

    # Market metadata / provenance. volume/open_interest (2026-08-16
    # strategy review) were already fetched onto the market dict for
    # filtering/scoring in main.py but never persisted -- without them,
    # "did edge cluster in illiquid markets" could never be answered from
    # historical data. event_ticker/series_ticker were already columns in
    # the DB (added for kalshi-event-ticker-capture) but excluded here, so
    # a row had no way to link back to the real market the way the email
    # report already can -- kalshi_url (computed below, via the exact same
    # core.kalshi.kalshi_market_url() the email uses) closes that gap
    # directly rather than making every consumer reconstruct the URL
    # itself from the two raw ticker fields.
    "close_time", "volume", "open_interest",
    "event_ticker", "series_ticker", "kalshi_url",
]

_CONF_RANK    = {"HIGH": 0, "MED": 1, "LOW": 2}
_HORIZON_RANK = {"INTRADAY": 0, "WEEKLY": 1, "MONTHLY": 2, "QUARTERLY": 3, "LONG": 4}

# Per-column notes shown in blank-rate warnings.
_COL_NOTES = {
    "leviathan_score": "lv_band will show Unscored",
    "time_horizon":    "horizon breakdown unavailable",
}


def _clean_str(val) -> str:
    """Convert None or null-sentinel strings to '' for string columns."""
    if val is None:
        return ""
    s = str(val)
    return "" if s in _NULL_STRINGS else s


def _null_to_empty(headers: list, rows: list) -> list:
    """Replace None with '' in string columns; leave all other values untouched."""
    str_idx = {i for i, h in enumerate(headers) if h in _STRING_COLS}
    if not str_idx:
        return rows
    out = []
    for row in rows:
        row = list(row)
        for i in str_idx:
            if row[i] is None or (isinstance(row[i], str) and row[i] in _NULL_STRINGS):
                row[i] = ""
        out.append(tuple(row))
    return out


def _add_computed_cols(row: dict) -> dict:
    """Return a copy of row with analysis-ready computed columns added."""
    r = dict(row)

    result           = _clean_str(r.get("result"))
    direction        = _clean_str(r.get("direction"))
    r["is_resolved"] = 1 if result in ("WIN", "LOSS") else 0
    # FIX 2: blank for unresolved so Power BI excludes them from SUM()
    if result == "WIN":
        r["is_win"] = 1
    elif result == "LOSS":
        r["is_win"] = 0
    else:
        r["is_win"] = None

    conf = _clean_str(r.get("confidence")).upper()
    if conf not in _CONF_RANK:
        r["confidence"] = ""
        conf = ""
    r["confidence_rank"] = _CONF_RANK.get(conf, 0)

    horizon = _clean_str(r.get("time_horizon")).upper()
    # FIX 4: default 0 so Power BI sorts blanks consistently
    r["horizon_rank"] = _HORIZON_RANK.get(horizon, 0)

    ts         = _clean_str(r.get("timestamp"))
    r["date"]  = ts[:10] if ts else ""

    pnl = r.get("pnl_if_traded")
    try:
        r["pnl_scaled"] = round(float(pnl) * 10, 4)
    except (TypeError, ValueError):
        r["pnl_scaled"] = ""

    lv = r.get("leviathan_score")
    has_score = True
    try:
        lv_int = int(lv)
        if lv_int >= 70:    r["lv_band"] = "A"
        elif lv_int >= 55:  r["lv_band"] = "B"
        elif lv_int >= 40:  r["lv_band"] = "C"
        else:               r["lv_band"] = "D"
    except (TypeError, ValueError):
        # FIX 3: readable label so Power BI shows a category rather than blank
        r["lv_band"] = "Unscored"
        has_score = False

    # pre_scoring_era: real bets (YES/NO) logged before leviathan_score
    # existed as a tracked field, with no way to retroactively compute it
    # (the market snapshot from that moment is gone). Deliberately scoped
    # to real bets only -- a handful of PASS rows are also missing a score
    # for unrelated reasons (an in-progress scoring run, a skipped step),
    # and lumping those in here would misrepresent a live gap as old data.
    r["pre_scoring_era"] = 1 if (direction in ("YES", "NO") and not has_score) else 0

    # brier_scorer / brier_market per row, computed via the exact same
    # core.logger.brier_component() analysis/calibration.py's aggregates
    # call — same source columns (our_estimate/market_price, direction,
    # result) — so the export and the calibration script can never disagree
    # on a row's number. Blank (not 0.5) when unresolved or the relevant
    # source value is missing.
    component_scorer = brier_component(r.get("our_estimate"), direction, result)
    r["brier_scorer"] = round(component_scorer, 4) if component_scorer is not None else ""

    component_market = brier_component(r.get("market_price"), direction, result)
    r["brier_market"] = round(component_market, 4) if component_market is not None else ""

    # kalshi_url: same core.kalshi.kalshi_market_url() the email report
    # uses -- None (missing series_ticker or event_ticker; both blank on
    # rows logged before kalshi-event-ticker-capture) becomes "", never a
    # guessed/partial URL.
    url = kalshi_market_url(r.get("series_ticker"), r.get("event_ticker"))
    r["kalshi_url"] = url or ""

    # days_to_resolution: resolved_at (when resolve_outcomes() actually
    # recorded the outcome) minus timestamp (when the signal was created),
    # in days -- blank until both ends exist, i.e. blank for every
    # unresolved row and every row logged before resolved_at existed.
    r["days_to_resolution"] = ""
    ts_raw  = r.get("timestamp")
    res_raw = r.get("resolved_at")
    if ts_raw and res_raw:
        try:
            ts  = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            res = datetime.fromisoformat(str(res_raw).replace("Z", "+00:00"))
            r["days_to_resolution"] = round((res - ts).total_seconds() / 86400, 2)
        except (TypeError, ValueError):
            pass

    return r


def _is_blank(val) -> bool:
    return val is None or val == "" or (isinstance(val, str) and val in _NULL_STRINGS)


def _print_validation(rows: list, final_cols: list, label: str = "signals.csv") -> None:
    """Print a post-export summary so data gaps are immediately visible."""
    n      = len(rows)
    col_idx = {c: i for i, c in enumerate(final_cols)}

    def get(row, col):
        idx = col_idx.get(col)
        return row[idx] if idx is not None else None

    resolved   = sum(1 for r in rows if get(r, "result") in ("WIN", "LOSS"))
    pending    = n - resolved
    wins       = sum(1 for r in rows if get(r, "result") == "WIN")
    losses     = sum(1 for r in rows if get(r, "result") == "LOSS")
    win_rate   = (wins / resolved * 100) if resolved else 0.0

    total_pnl = 0.0
    for r in rows:
        try:
            total_pnl += float(get(r, "pnl_if_traded"))
        except (TypeError, ValueError):
            pass

    sign = "-" if total_pnl < 0 else ""
    print(f"[export] {label} — {n} rows, {len(final_cols)} columns")
    print(f"[export] Resolved: {resolved} | Pending: {pending} | Wins: {wins} | Losses: {losses}")
    print(f"[export] Win Rate: {win_rate:.1f}%")
    print(f"[export] Net PnL: {sign}${abs(total_pnl):.2f}")

    if n > 0:
        warnings = []
        for col in final_cols:
            idx        = col_idx[col]
            blank_cnt  = sum(1 for r in rows if _is_blank(r[idx]))
            if blank_cnt / n > 0.5:
                pct  = int(blank_cnt / n * 100)
                note = _COL_NOTES.get(col, "")
                warnings.append((col, pct, note))
        if warnings:
            print(f"[export] BLANK RATE WARNING ({label}, >50% blank):")
            for col, pct, note in warnings:
                suffix = f" — {note}" if note else ""
                print(f"         {col}: {pct}% blank{suffix}")


def _write_csv(dest: str, final_cols: list, rows: list) -> None:
    with open(dest, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(final_cols)
        writer.writerows(rows)


def _signals_to_csv(conn: sqlite3.Connection, signals_dest: str, scan_log_dest: str) -> dict:
    """
    Write the signals table to two CSVs with computed columns added and
    whitelist applied: signals_dest gets only real bets (direction YES/NO),
    scan_log_dest gets every row including PASS (scanner looked, no signal).
    Pipeline plumbing columns are excluded from both; only WHITELIST columns
    are written. Returns {"signals": n, "scan_log": n}.
    """
    cur        = conn.execute("SELECT * FROM signals")
    db_headers = [d[0] for d in cur.description]
    db_rows    = cur.fetchall()

    # realfill-dedup guard
    _src_idx = db_headers.index("source") if "source" in db_headers else -1
    _tkr_idx = db_headers.index("ticker") if "ticker" in db_headers else -1
    _res_idx = db_headers.index("result") if "result" in db_headers else -1
    if _src_idx >= 0 and _tkr_idx >= 0 and _res_idx >= 0:
        _rf_seen: dict[str, int] = {}
        for _raw in db_rows:
            if str(_raw[_src_idx] or "") == "real_fill" and str(_raw[_res_idx] or "") == "":
                _t = str(_raw[_tkr_idx] or "")
                _rf_seen[_t] = _rf_seen.get(_t, 0) + 1
        for _t, _cnt in _rf_seen.items():
            if _cnt > 1:
                print(f"WARNING: duplicate real_fill detected for {_t}")

    # Final column set: WHITELIST ∩ (DB columns ∪ computed columns), in WHITELIST order.
    available  = set(db_headers) | _COMPUTED_COLS
    final_cols = [c for c in WHITELIST if c in available]
    dir_idx    = final_cols.index("direction") if "direction" in final_cols else -1

    all_rows  = []
    bet_rows  = []
    for raw in db_rows:
        row_dict = _add_computed_cols(dict(zip(db_headers, raw)))
        out_row  = []
        for col in final_cols:
            val = row_dict.get(col)
            # FIX 1: harden all string columns against None and null-sentinel strings
            if col in _STRING_COLS:
                val = _clean_str(val)
            out_row.append(val)
        all_rows.append(out_row)
        # PASS rows (Claude found no actionable edge) never entered the
        # outcome/result pipeline as a real bet -- excluded from signals.csv
        # so the actionable file isn't 85% scan noise. Still present in
        # scan_log.csv for anyone who wants full scan history.
        if dir_idx < 0 or out_row[dir_idx] != "PASS":
            bet_rows.append(out_row)

    _print_validation(bet_rows, final_cols, label="signals.csv")
    _print_validation(all_rows, final_cols, label="scan_log.csv")

    _write_csv(signals_dest, final_cols, bet_rows)
    _write_csv(scan_log_dest, final_cols, all_rows)
    return {"signals": len(bet_rows), "scan_log": len(all_rows)}


def _table_to_csv(conn: sqlite3.Connection, table: str, dest: str) -> int:
    """Write one table to a CSV file. Returns row count (excluding header)."""
    cur     = conn.execute(f"SELECT * FROM {table}")
    headers = [d[0] for d in cur.description]
    rows    = _null_to_empty(headers, cur.fetchall())
    with open(dest, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    return len(rows)


def export_csvs(db_path: str = DB_PATH, export_dir: str = EXPORT_DIR) -> dict:
    """
    Read signals and runs from leviathan.db and write CSVs to export_dir.
    Returns {"signals": row_count, "scan_log": row_count, "runs": row_count}.
    signals.csv holds only real bets (direction YES/NO); scan_log.csv holds
    every scan decision including PASS. Prints a warning and returns zeros
    if the DB is missing or unreadable.
    """
    if not os.path.exists(db_path):
        print(f"[export] WARNING: DB not found at {db_path} — skipping export")
        return {"signals": 0, "scan_log": 0, "runs": 0}

    os.makedirs(export_dir, exist_ok=True)

    counts = {"signals": 0, "scan_log": 0, "runs": 0}
    try:
        conn = sqlite3.connect(db_path)
        try:
            signal_counts = _signals_to_csv(
                conn,
                os.path.join(export_dir, "signals.csv"),
                os.path.join(export_dir, "scan_log.csv"),
            )
            counts["signals"]  = signal_counts["signals"]
            counts["scan_log"] = signal_counts["scan_log"]
            counts["runs"] = _table_to_csv(
                conn, "runs", os.path.join(export_dir, "runs.csv")
            )
        finally:
            conn.close()
    except Exception as e:
        print(f"[export] WARNING: export failed — {e}")

    return counts


if __name__ == "__main__":
    result = export_csvs()
    print(f"[export] signals.csv:  {result['signals']} rows")
    print(f"[export] scan_log.csv: {result['scan_log']} rows")
    print(f"[export] runs.csv:     {result['runs']} rows")
    print(f"[export] Written to:  {EXPORT_DIR}")
