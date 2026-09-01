"""
Persistent storage for Leviathan signals and run history.
Uses SQLite (leviathan.db) — no Excel locking, fast queries, no extra dependencies.
Auto-migrates calls.csv / runs.csv to the database on first import.
"""

import csv
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

_ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH   = os.path.join(_ROOT, "data", "leviathan.db")
CALLS_CSV = os.path.join(_ROOT, "calls.csv")
RUNS_CSV  = os.path.join(_ROOT, "runs.csv")


# ── DB connection ─────────────────────────────────────────────────────────────

@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema ────────────────────────────────────────────────────────────────────

def _add_col(conn, col_def: str, table: str = "signals") -> None:
    """Add a column to `table` if it doesn't already exist (idempotent)."""
    col_name = col_def.split()[0]
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col_name not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")


def _init_db() -> None:
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                call_id         TEXT PRIMARY KEY,
                timestamp       TEXT,
                ticker          TEXT,
                title           TEXT,
                market_price    REAL,
                our_estimate    REAL,
                edge            REAL,
                direction       TEXT,
                confidence      TEXT,
                whale_detected  INTEGER DEFAULT 0,
                whale_direction TEXT,
                outcome         TEXT,
                result          TEXT,
                pnl_if_traded   REAL,
                run_id          TEXT
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id             TEXT PRIMARY KEY,
                timestamp          TEXT,
                markets_scanned    INTEGER,
                signals_generated  INTEGER,
                whale_flags        INTEGER,
                model_used         TEXT,
                tokens_used        INTEGER,
                cost_usd           REAL,
                runtime_ms         INTEGER
            );
            CREATE TABLE IF NOT EXISTS blind_scores (
                call_id             TEXT PRIMARY KEY,
                timestamp           TEXT,
                run_id              TEXT,
                ticker              TEXT,
                title               TEXT,
                estimate            REAL,
                confidence          TEXT,
                reasoning           TEXT,
                sources_checked     TEXT,
                market_price_at_score REAL,
                cost_usd            REAL
            );
            CREATE INDEX IF NOT EXISTS idx_signals_ts     ON signals(timestamp);
            CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
            CREATE INDEX IF NOT EXISTS idx_blind_scores_ticker ON blind_scores(ticker);
        """)
        # Additive schema migration — non-destructive, safe to run repeatedly.
        for col in [
            "segment               TEXT",
            "entry_price           REAL",
            "source                TEXT    DEFAULT 'paper'",
            "from_signal           INTEGER DEFAULT 0",
            "signal_call_id        TEXT",
            "direction_aligned     INTEGER",
            "fill_count            INTEGER",
            "fill_fee              REAL",
            "market_price_at_probe REAL",
            "claude_estimate       REAL",
            "divergence            REAL",
            "predicted_direction   TEXT",
            "flag_path             TEXT",
            "watchlist_signal      INTEGER DEFAULT 0",
            "sig_edge              INTEGER DEFAULT 0",
            "sig_drift             INTEGER DEFAULT 0",
            "sig_br_none           INTEGER DEFAULT 0",
            "base_rate             REAL",
            "net_edge              REAL",
            "heuristic_direction   TEXT",
            "short_horizon         INTEGER DEFAULT 0",
            "time_horizon          TEXT",
            "close_time            TEXT",
            "leviathan_score       INTEGER",
            "heuristic_label       TEXT",
            "net_edge_after_fee    REAL",
            "ev_after_fee_per_contract REAL",
            "event_ticker          TEXT    DEFAULT ''",
            "series_ticker         TEXT    DEFAULT ''",
            "market_baseline_brier REAL",
            "whale_max_trade_size  REAL",
            "category              TEXT    DEFAULT ''",
            # Order-book/spread signal-time context (computed in main.py's
            # signal dict already, just never persisted before now).
            "ob_flag               INTEGER DEFAULT 0",
            "ob_imbalance          REAL",
            "ob_direction          TEXT",
            "spread_wide           INTEGER DEFAULT 0",
            "spread_pct            REAL",
            # Methodology flags -- both are set by main.py logic that changes
            # how a signal was selected/graded, and lumping them in with
            # ordinary signals without a marker risks confounding any later
            # calibration analysis.
            "confidence_downgraded INTEGER DEFAULT 0",
            "second_pass           INTEGER DEFAULT 0",
            # Extremizing transform output (Satopää et al. 2014) -- lets a
            # future analysis check whether the extremized estimate actually
            # performed better or worse than the raw our_estimate.
            "ext_estimate          REAL",
            "ext_edge              REAL",
            "ext_n_signals         INTEGER",
            "ext_alpha             REAL",
            # backlog: confluence-detection. The same agreeing-signal count
            # ext_n_signals carries, but recorded unconditionally on every
            # YES/NO signal -- ext_n_signals is only ever set when the
            # extremizing transform's own >=2 threshold fires (main.py),
            # so n=0 and n=1 are both NULL there and indistinguishable.
            # confluence_count is that same _count_agreeing_signals() value
            # with no threshold gate, letting get_stats_by_confluence()
            # compare 0 vs 1 vs 2+ agreeing sources against actual outcomes.
            "confluence_count      INTEGER",
            # Cross-market/smart-money evidence at signal time -- previously
            # computed fresh every run for the prompt/report and discarded;
            # without these, "did Polymarket/consensus/smart-money agreement
            # predict outcomes" can never be answered even retroactively.
            "poly_price            REAL",
            "poly_price_gap        REAL",
            "consensus_gap         REAL",
            "consensus_dir         TEXT",
            "smart_money_count     INTEGER DEFAULT 0",
            "smart_money_dir       TEXT",
            # Hypothetical stake under confidence-weighted sizing (core.
            # sizing.compute_stake_size) -- a SEPARATE column from the
            # existing flat-unit_size P&L path, never used in place of it.
            # Equals config.betting.unit_size (the existing flat figure)
            # until is_dynamic_sizing_eligible() clears its own gate.
            "stake_size_hypothetical REAL",
            # GOAL_subscriber_report.md Phase 3: Claude's own narrative
            # (already in the live in-memory signal dict since before this
            # migration existed) and the structured web-search sources
            # captured by core/llm.py's _extract_web_search_sources (Phase
            # 2), persisted so past picks/the track record/the digest can
            # show them, not just the run that produced them. sources is
            # stored as a JSON-encoded string (TEXT column) -- readers must
            # go through report._coerce_sources, never assume it's already
            # a list. Captured going forward only -- old rows read back NULL
            # and render the existing graceful fallback, never backfilled.
            "reasoning              TEXT",
            "sources                TEXT",
            # GOAL_subscriber_report.md Phase 4: CLV-style edge metric --
            # did the market drift toward our estimate before it resolved,
            # independent of whether the coin-flip outcome landed our way.
            # Computed in resolve_outcomes() from the same market object
            # already fetched there (last_price_dollars) -- zero extra API
            # calls. Signed toward the flagged direction: positive means
            # the market moved our way. See get_market_drift_stats().
            "market_drift_pp       REAL",
            # signal-time market microstructure context (already fetched
            # onto the market dict for filtering/scoring in main.py --
            # volume_fp/open_interest_fp -- but never copied onto the
            # logged signal, so "did edge cluster in illiquid markets"
            # could never be answered from historical data). Column named
            # without the _fp suffix since Power BI/CSV consumers read the
            # already-dollar/contract-scaled value, matching category/
            # whale_max_trade_size's naming convention above, not the raw
            # Kalshi field name.
            "volume                REAL",
            "open_interest         REAL",
            # When a signal actually resolved, distinct from `timestamp`
            # (signal creation) and `close_time` (the market's SCHEDULED
            # close, which settlement can lag) -- without this, time-to-
            # resolution could only ever be approximated, never measured.
            # Set once, in resolve_outcomes(), the same call that fills
            # outcome/result/pnl_if_traded. NULL for every row logged
            # before this existed and for anything still pending.
            "resolved_at           TEXT",
            # backlog: net-edge-fee-depth-model. ob_bid_depth/ob_ask_depth are
            # already computed by compute_orderbook_signal() but were never
            # persisted (only the derived ob_imbalance/ob_flag/ob_direction
            # were) -- without the raw depth, "did edge cluster in markets
            # too thin to fill unit_size" can't be answered after the fact.
            # liquidity_thin is the check itself: depth on the side this
            # signal's direction needs was below the configured unit_size at
            # scan time. Own column (not folded into confidence_downgraded)
            # for the same reason second_pass/confidence_downgraded are
            # separate -- one flag per distinct methodology reason, so later
            # calibration analysis can tell them apart.
            "ob_bid_depth          REAL",
            "ob_ask_depth          REAL",
            "liquidity_checked     INTEGER DEFAULT 0",
            "liquidity_thin        INTEGER DEFAULT 0",
            # backlog: citations-provenance-grounding. core.llm.ground_
            # citations_via_api's structured [{"url","title","cited_text",
            # "start_char_index","end_char_index"}, ...] output, JSON-
            # encoded like the existing `sources` column above -- readers
            # must go through the same json.loads-or-[] pattern, never
            # assume it's already a list. NULL for every row logged before
            # this existed, and for any row whose shortlist re-score wasn't
            # reached or found no citations (see _rescore_shortlist_for_
            # clean_sources's non-fatal try/except in main.py).
            "citations             TEXT",
        ]:
            _add_col(conn, col)
        # Tag all pre-existing rows (source IS NULL) as paper signals.
        conn.execute("UPDATE signals SET source='paper' WHERE source IS NULL")
        # backlog: brier-tracking. get_brier_score()/get_market_baseline_
        # brier_score() only ever computed a single current-snapshot
        # aggregate over all resolved signals to date -- nothing persisted
        # a point-in-time value, so there was no way to see whether
        # calibration is improving or degrading across runs. log_run()
        # now snapshots both onto each run row; brier_n is None (not 0)
        # for any run logged before this existed, or any run where no
        # signal had resolved yet -- distinguishing "not measured" from
        # "measured zero signals resolved."
        for col in ("brier_scorer REAL", "brier_market REAL", "brier_n INTEGER"):
            _add_col(conn, col, table="runs")
        # db-audit-2026-08: contract_type/resolution_date/logged_under were
        # never read or written by any code path (confirmed against every
        # INSERT INTO signals in this file) -- dead schema from an earlier
        # design, not columns any feature depends on. Dropped rather than
        # left blank forever; existence-checked like _add_col so this stays
        # idempotent/safe to run repeatedly -- SQLite's DROP COLUMN has no
        # IF EXISTS clause (unlike Postgres/MySQL), it errors on a column
        # that's already gone.
        _existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
        for _dead_col in ("contract_type", "resolution_date", "logged_under"):
            if _dead_col in _existing_cols:
                conn.execute(f"ALTER TABLE signals DROP COLUMN {_dead_col}")
    _migrate_csv()


def _to_float(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def _to_int(v):
    try:
        return int(v) if v not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def _migrate_csv() -> None:
    """One-time migration of calls.csv and runs.csv into SQLite."""
    if os.path.exists(CALLS_CSV):
        try:
            with open(CALLS_CSV, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            with _db() as conn:
                for row in rows:
                    conn.execute("""
                        INSERT OR IGNORE INTO signals
                        (call_id,timestamp,ticker,title,market_price,our_estimate,
                         edge,direction,confidence,whale_detected,whale_direction,
                         outcome,result,pnl_if_traded,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        row.get("call_id") or str(uuid.uuid4())[:8],
                        row.get("timestamp", ""),
                        row.get("ticker", ""),
                        row.get("title", ""),
                        _to_float(row.get("market_price")),
                        _to_float(row.get("our_estimate")),
                        _to_float(row.get("edge")),
                        row.get("direction", ""),
                        row.get("confidence", ""),
                        1 if str(row.get("whale_detected", "")).lower() in ("true", "1") else 0,
                        row.get("whale_direction", ""),
                        row.get("outcome", ""),
                        row.get("result", ""),
                        _to_float(row.get("pnl_if_traded")),
                        row.get("run_id", ""),
                    ))
            os.rename(CALLS_CSV, CALLS_CSV + ".migrated")
            print(f"  [logger] Migrated {len(rows)} rows from calls.csv to leviathan.db")
        except Exception as e:
            print(f"  [logger] CSV migration warning: {e}")

    if os.path.exists(RUNS_CSV):
        try:
            with open(RUNS_CSV, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            with _db() as conn:
                for row in rows:
                    conn.execute("""
                        INSERT OR IGNORE INTO runs
                        (run_id,timestamp,markets_scanned,signals_generated,
                         whale_flags,model_used,tokens_used,cost_usd,runtime_ms)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (
                        row.get("run_id") or str(uuid.uuid4())[:8],
                        row.get("timestamp", ""),
                        _to_int(row.get("markets_scanned")),
                        _to_int(row.get("signals_generated")),
                        _to_int(row.get("whale_flags")),
                        row.get("model_used", ""),
                        _to_int(row.get("tokens_used")),
                        _to_float(row.get("cost_usd")),
                        _to_int(row.get("runtime_ms")),
                    ))
            os.rename(RUNS_CSV, RUNS_CSV + ".migrated")
        except Exception as e:
            print(f"  [logger] Runs CSV migration warning: {e}")


_init_db()


# ── Write ─────────────────────────────────────────────────────────────────────

def log_signal(signal: dict) -> None:
    try:
        with _db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO signals
                (call_id,timestamp,ticker,title,market_price,our_estimate,
                 edge,direction,confidence,whale_detected,whale_direction,
                 outcome,result,pnl_if_traded,run_id,source,
                 flag_path,watchlist_signal,sig_edge,sig_drift,sig_br_none,
                 base_rate,net_edge,heuristic_direction,short_horizon,time_horizon,
                 close_time,leviathan_score,heuristic_label,
                 net_edge_after_fee,ev_after_fee_per_contract,event_ticker,series_ticker,
                 whale_max_trade_size,category,
                 ob_flag,ob_imbalance,ob_direction,spread_wide,spread_pct,
                 volume,open_interest,
                 confidence_downgraded,second_pass,
                 ext_estimate,ext_edge,ext_n_signals,ext_alpha,confluence_count,
                 poly_price,poly_price_gap,consensus_gap,consensus_dir,
                 smart_money_count,smart_money_dir,reasoning,sources,
                 ob_bid_depth,ob_ask_depth,liquidity_checked,liquidity_thin,citations)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(uuid.uuid4())[:8],
                datetime.now(timezone.utc).isoformat(),
                signal.get("ticker", ""),
                signal.get("title", ""),
                _to_float(signal.get("market_price")),
                _to_float(signal.get("our_estimate")),
                _to_float(signal.get("edge")),
                signal.get("direction", ""),
                signal.get("confidence", ""),
                1 if signal.get("whale_detected") else 0,
                signal.get("whale_direction", ""),
                "", "",  # outcome, result — filled by resolve_outcomes
                None,    # pnl_if_traded
                signal.get("run_id", ""),
                "paper",
                signal.get("flag_path"),
                1 if signal.get("watchlist_signal") else 0,
                1 if signal.get("sig_edge") else 0,
                1 if signal.get("sig_drift") else 0,
                1 if signal.get("sig_br_none") else 0,
                _to_float(signal.get("base_rate")),
                _to_float(signal.get("net_edge")),
                signal.get("heuristic_direction"),
                1 if signal.get("short_horizon") else 0,
                signal.get("time_horizon"),
                signal.get("close_time"),
                _to_int(signal.get("leviathan_score")),
                signal.get("heuristic_label"),
                _to_float(signal.get("net_edge_after_fee")),
                _to_float(signal.get("ev_after_fee_per_contract")),
                signal.get("event_ticker", ""),
                signal.get("series_ticker", ""),
                _to_float(signal.get("whale_max_trade_size")),
                signal.get("category", ""),
                1 if signal.get("ob_flag") else 0,
                _to_float(signal.get("ob_imbalance")),
                signal.get("ob_direction"),
                1 if signal.get("spread_wide") else 0,
                _to_float(signal.get("spread_pct")),
                _to_float(signal.get("volume")),
                _to_float(signal.get("open_interest")),
                1 if signal.get("confidence_downgraded") else 0,
                1 if signal.get("second_pass") else 0,
                _to_float(signal.get("ext_estimate")),
                _to_float(signal.get("ext_edge")),
                _to_int(signal.get("ext_n_signals")),
                _to_float(signal.get("ext_alpha")),
                _to_int(signal.get("confluence_count")),
                _to_float(signal.get("poly_price")),
                _to_float(signal.get("poly_price_gap")),
                _to_float(signal.get("consensus_gap")),
                signal.get("consensus_dir"),
                _to_int(signal.get("smart_money_count")) or 0,
                signal.get("smart_money_dir"),
                signal.get("reasoning") or "",
                json.dumps(signal.get("sources") or []),
                _to_float(signal.get("ob_bid_depth")),
                _to_float(signal.get("ob_ask_depth")),
                1 if signal.get("liquidity_checked") else 0,
                1 if signal.get("liquidity_thin") else 0,
                json.dumps(signal.get("citations") or []),
            ))
    except Exception as e:
        print(f"  [logger] Failed to log signal: {e}")


def log_pass(signal: dict) -> None:
    """
    Log a PASS decision (Claude found no actionable edge) for scanner calibration.
    Stored as source='paper', direction='PASS' — never enters outcome/result pipeline.
    Used by get_pass_tickers() to identify systematic scanner false-positives.
    """
    try:
        with _db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO signals
                (call_id,timestamp,ticker,title,market_price,our_estimate,
                 edge,direction,confidence,whale_detected,whale_direction,
                 outcome,result,pnl_if_traded,run_id,source,
                 flag_path,watchlist_signal,sig_edge,sig_drift,sig_br_none,
                 base_rate,net_edge,heuristic_direction,
                 short_horizon,time_horizon,close_time,leviathan_score,heuristic_label,
                 net_edge_after_fee,ev_after_fee_per_contract,event_ticker,series_ticker,
                 whale_max_trade_size,category,
                 ob_flag,ob_imbalance,ob_direction,spread_wide,spread_pct,
                 volume,open_interest,
                 confidence_downgraded,second_pass,
                 ext_estimate,ext_edge,ext_n_signals,ext_alpha,confluence_count,
                 poly_price,poly_price_gap,consensus_gap,consensus_dir,
                 smart_money_count,smart_money_dir,reasoning,sources,
                 ob_bid_depth,ob_ask_depth,liquidity_checked,liquidity_thin,citations)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(uuid.uuid4())[:8],
                datetime.now(timezone.utc).isoformat(),
                signal.get("ticker", ""),
                signal.get("title", ""),
                _to_float(signal.get("market_price")),
                _to_float(signal.get("our_estimate")),
                _to_float(signal.get("edge")),
                "PASS",
                signal.get("confidence", ""),
                # Previously hardcoded to 0/None regardless of the real
                # value, even though the caller (main.py) already has
                # whale_detected/whale_direction on `signal` at this point
                # -- every whale-flagged market that resulted in a PASS
                # (the majority of them, in practice) silently lost its
                # whale flag the moment it hit the DB.
                1 if signal.get("whale_detected") else 0,
                signal.get("whale_direction"),
                "", "",  # outcome, result — never filled for PASSes
                None,
                signal.get("run_id", ""),
                "paper",
                signal.get("flag_path"),
                1 if signal.get("watchlist_signal") else 0,
                1 if signal.get("sig_edge") else 0,
                1 if signal.get("sig_drift") else 0,
                1 if signal.get("sig_br_none") else 0,
                _to_float(signal.get("base_rate")),
                _to_float(signal.get("net_edge")),
                signal.get("heuristic_direction"),
                1 if signal.get("short_horizon") else 0,
                signal.get("time_horizon"),
                signal.get("close_time"),
                _to_int(signal.get("leviathan_score")),
                signal.get("heuristic_label"),
                _to_float(signal.get("net_edge_after_fee")),
                _to_float(signal.get("ev_after_fee_per_contract")),
                signal.get("event_ticker", ""),
                signal.get("series_ticker", ""),
                _to_float(signal.get("whale_max_trade_size")),
                signal.get("category", ""),
                1 if signal.get("ob_flag") else 0,
                _to_float(signal.get("ob_imbalance")),
                signal.get("ob_direction"),
                1 if signal.get("spread_wide") else 0,
                _to_float(signal.get("spread_pct")),
                _to_float(signal.get("volume")),
                _to_float(signal.get("open_interest")),
                1 if signal.get("confidence_downgraded") else 0,
                1 if signal.get("second_pass") else 0,
                _to_float(signal.get("ext_estimate")),
                _to_float(signal.get("ext_edge")),
                _to_int(signal.get("ext_n_signals")),
                _to_float(signal.get("ext_alpha")),
                _to_int(signal.get("confluence_count")),
                _to_float(signal.get("poly_price")),
                _to_float(signal.get("poly_price_gap")),
                _to_float(signal.get("consensus_gap")),
                signal.get("consensus_dir"),
                _to_int(signal.get("smart_money_count")) or 0,
                signal.get("smart_money_dir"),
                signal.get("reasoning") or "",
                json.dumps(signal.get("sources") or []),
                _to_float(signal.get("ob_bid_depth")),
                _to_float(signal.get("ob_ask_depth")),
                1 if signal.get("liquidity_checked") else 0,
                1 if signal.get("liquidity_thin") else 0,
                json.dumps(signal.get("citations") or []),
            ))
    except Exception as e:
        print(f"  [logger] Failed to log pass: {e}")


def log_blind_score(row: dict) -> None:
    """
    Persist one price-blind shadow score (backlog: price-blind-arm) to its
    own table, never `signals` -- this keeps the blind arm structurally
    unable to feed signal selection, not just unused by convention.

    row keys: run_id, ticker, title, estimate, confidence, reasoning,
    sources_checked (list), market_price_at_score (the anchored scorer's
    market_price for this ticker/run, captured for later comparison only --
    never shown to the blind prompt itself), cost_usd.
    """
    import json as _json
    try:
        with _db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO blind_scores
                (call_id,timestamp,run_id,ticker,title,estimate,confidence,
                 reasoning,sources_checked,market_price_at_score,cost_usd)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                str(uuid.uuid4())[:8],
                datetime.now(timezone.utc).isoformat(),
                row.get("run_id", ""),
                row.get("ticker", ""),
                row.get("title", ""),
                _to_float(row.get("estimate")),
                row.get("confidence", ""),
                row.get("reasoning", ""),
                _json.dumps(row.get("sources_checked") or []),
                _to_float(row.get("market_price_at_score")),
                _to_float(row.get("cost_usd")),
            ))
    except Exception as e:
        print(f"  [logger] Failed to log blind score: {e}")


def get_pass_tickers(days: int = 14) -> dict:
    """
    Return tickers that consistently got PASS in the last N days.
    Returns {ticker: pass_count} for all paper PASS rows in the window.
    Used to deprioritize repeat false-positives in the scoring queue.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT ticker, COUNT(*) as cnt FROM signals "
                "WHERE direction='PASS' AND (source='paper' OR source IS NULL) "
                "AND timestamp >= ? AND ticker != '' "
                "GROUP BY ticker ORDER BY cnt DESC",
                (cutoff,),
            ).fetchall()
        return {r["ticker"]: r["cnt"] for r in rows}
    except Exception:
        return {}


def log_run(run_data: dict) -> None:
    try:
        with _db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO runs
                (run_id,timestamp,markets_scanned,signals_generated,
                 whale_flags,model_used,tokens_used,cost_usd,runtime_ms,
                 brier_scorer,brier_market,brier_n)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                run_data.get("run_id", ""),
                run_data.get("timestamp", ""),
                _to_int(run_data.get("markets_scanned")),
                _to_int(run_data.get("signals_generated")),
                _to_int(run_data.get("whale_flags")),
                run_data.get("model_used", ""),
                _to_int(run_data.get("tokens_used")),
                _to_float(run_data.get("cost_usd")),
                _to_int(run_data.get("runtime_ms")),
                _to_float(run_data.get("brier_scorer")),
                _to_float(run_data.get("brier_market")),
                _to_int(run_data.get("brier_n")),
            ))
    except Exception as e:
        print(f"  [logger] Failed to log run: {e}")


# ── Real fills ───────────────────────────────────────────────────────────────

def pull_real_fills(config: dict) -> dict:
    """
    Fetch all real Kalshi fills and insert them as source='real_fill' rows.
    Matches each fill against the most recent PRIOR paper signal by ticker
    (never a paper signal logged after the fill -- see db-audit-2026-08 note
    below); sets from_signal, signal_call_id, and direction_aligned
    accordingly. Returns a summary dict: pulled, matched, aligned, contradictory.

    call_id uses Kalshi's own fill_id/trade_id (stable across re-runs) rather
    than a random uuid (db-audit-2026-08: the prior random call_id meant
    INSERT OR IGNORE could never actually dedupe a re-run -- every call would
    insert a fresh duplicate row for the same real fills, silently double-
    counting real PnL).
    """
    from core import kalshi as _kalshi

    fills = _kalshi.fetch_fills(config)
    if not fills:
        return {"pulled": 0, "matched": 0, "aligned": 0, "contradictory": 0}

    try:
        with _db() as conn:
            sig_rows = conn.execute(
                f"SELECT call_id, ticker, direction, timestamp FROM signals "
                f"WHERE {_NO_PASS} ORDER BY timestamp ASC"
            ).fetchall()
    except Exception:
        sig_rows = []

    # All actionable (YES/NO) paper signals per ticker, chronological (ASC,
    # matching sig_rows' own order). A later PASS decision on the same
    # ticker must never displace a real match -- PASS rows are excluded
    # above by the direction filter, since a fill can only ever be matched
    # against the actual YES/NO call it was meant to confirm, not whatever
    # Claude said most recently regardless of direction (a prior version
    # included PASS rows here, so a real fill correctly matching an earlier
    # YES/NO call could be marked "contradictory" simply because Claude
    # passed on the same ticker on a later, unrelated scan).
    ticker_signals: dict[str, list[dict]] = {}
    for row in sig_rows:
        if row["ticker"]:
            ticker_signals.setdefault(row["ticker"], []).append(dict(row))

    pulled = len(fills)
    matched = aligned = contradictory = 0

    for fill in fills:
        ticker      = fill.get("ticker", "")
        side        = (fill.get("side") or "").upper()   # "YES" / "NO"
        action      = (fill.get("action") or "").upper() # "BUY" / "SELL"
        fill_price  = _to_float(fill.get("yes_price_dollars") or fill.get("no_price_dollars"))
        fee         = _to_float(fill.get("fee_cost", 0)) or 0.0
        count       = _to_int(fill.get("count")) or 1
        created     = fill.get("created_time", "")
        fill_id     = fill.get("fill_id") or fill.get("trade_id") or str(uuid.uuid4())

        # db-audit-2026-08: must be the most recent signal that PRECEDES
        # this fill, not just "most recent overall" -- a paper signal
        # logged after a real trade already happened can't be what the
        # trade was acting on. sig_rows/ticker_signals are chronological
        # ASC, so the first candidate found after this fill's timestamp
        # means every later one is too late as well.
        sig = None
        if created:
            for candidate in ticker_signals.get(ticker, []):
                if candidate["timestamp"] and candidate["timestamp"] <= created:
                    sig = candidate
                else:
                    break

        from_sig     = 0
        sig_call_id  = None
        dir_aligned  = None

        if sig:
            from_sig    = 1
            sig_call_id = sig["call_id"]
            sig_dir     = (sig["direction"] or "").upper()
            if sig_dir and side:
                dir_aligned = 1 if sig_dir == side else 0
            matched += 1
            if dir_aligned == 1:
                aligned += 1
            elif dir_aligned == 0:
                contradictory += 1

        try:
            with _db() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO signals
                    (call_id, timestamp, ticker, title, market_price, our_estimate,
                     edge, direction, confidence, whale_detected, whale_direction,
                     outcome, result, pnl_if_traded, run_id,
                     source, from_signal, signal_call_id, direction_aligned,
                     entry_price, fill_count, fill_fee)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    fill_id,
                    created, ticker, ticker,
                    fill_price, None, None,
                    side, action,
                    0, None,
                    "", "", None, "",
                    "real_fill", from_sig, sig_call_id, dir_aligned,
                    fill_price, count, fee,
                ))
        except Exception as e:
            print(f"  [logger] pull_real_fills: failed on {ticker}: {e}")

    return {
        "pulled":        pulled,
        "matched":       matched,
        "aligned":       aligned,
        "contradictory": contradictory,
    }


# ── Read ──────────────────────────────────────────────────────────────────────

def get_recent_tickers(days: int = 7) -> set[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT ticker FROM signals WHERE timestamp >= ?", (cutoff,)
            ).fetchall()
        return {r["ticker"] for r in rows if r["ticker"]}
    except Exception:
        return set()


def get_ticker_day_count(ticker: str, days: int = 14) -> int:
    """Return how many distinct calendar days this ticker was flagged in the last N days."""
    if not ticker:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT date(timestamp)) as cnt FROM signals "
                "WHERE ticker = ? AND timestamp >= ?",
                (ticker, cutoff),
            ).fetchone()
        return row["cnt"] if row else 0
    except Exception:
        return 0


def get_signal_history_batch(tickers: list, days: int = 14) -> dict:
    """
    Fetch paper signal history for multiple tickers in a single DB query.
    Returns {ticker: [row_dict, ...]} sorted newest-first within each ticker.
    Useful for computing persistence and direction consistency before scoring.
    """
    tickers = [t for t in tickers if t]
    if not tickers:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    placeholders = ",".join("?" * len(tickers))
    try:
        with _db() as conn:
            rows = conn.execute(
                f"SELECT ticker, timestamp, direction, market_price, our_estimate, edge "
                f"FROM signals WHERE ticker IN ({placeholders}) AND timestamp >= ? "
                f"AND ({_PAPER}) AND direction IN ('YES','NO') "
                f"ORDER BY timestamp DESC",
                (*tickers, cutoff),
            ).fetchall()
    except Exception:
        return {}
    result: dict = {}
    for r in rows:
        t = r["ticker"]
        if t:
            result.setdefault(t, []).append(dict(r))
    return result


def get_week_signals(days: int = 7) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE timestamp >= ? ORDER BY timestamp DESC",
                (cutoff,)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Outcome resolution ────────────────────────────────────────────────────────

def _market_baseline_brier(market_price, direction: str, outcome: str) -> float | None:
    """
    Brier score of the market price at scan time against the resolved
    outcome: (market_price - outcome_binary)^2, where outcome_binary is 1 if
    the market resolved YES, 0 if NO. Uses the same YES/NO-relative-to-
    direction derivation as get_brier_score() so the two scores are directly
    comparable apples-to-apples over the identical row population.

    Returns None (never 0.5) when market_price is missing — a market with no
    logged price at scan time has no baseline to compare against, and
    silently guessing 0.5 would understate the scorer's real edge over it.
    """
    if market_price is None:
        return None
    win = direction == outcome
    if direction == "YES":
        outcome_binary = 1.0 if win else 0.0
    elif direction == "NO":
        outcome_binary = 0.0 if win else 1.0
    else:
        return None
    return round((float(market_price) - outcome_binary) ** 2, 4)


def _market_drift_pp(late_price, market_price_at_flag, direction: str) -> float | None:
    """
    CLV-style edge metric (GOAL_subscriber_report.md Phase 4): did the market
    drift toward our flagged direction before it resolved, independent of
    whether the coin-flip outcome landed our way? This is the fix for the
    known problem that outcome/Brier scoring alone is unreliable on a small,
    price-anchored sample -- a call can be "right" on a 55/45 coin flip that
    tells you little, but a market that moved 20 points toward your number
    before resolving is a real, price-anchored signal.

    Raw (late_price - market_price_at_flag) is signed for a YES call already
    (price rising toward 1 = toward YES); a NO call needs the sign flipped
    (price FALLING toward 0 is what moves toward NO), so "positive" always
    means "the market moved our way" regardless of which side we called --
    the same per-direction branching resolve_outcomes()'s pnl calc already
    uses, just for drift instead of payoff.

    Returns None (never 0) when either price is missing or direction isn't
    YES/NO -- same "never fabricate a number with no real basis" discipline
    as _market_baseline_brier.
    """
    if late_price is None or market_price_at_flag is None:
        return None
    raw_diff = float(late_price) - float(market_price_at_flag)
    if direction == "YES":
        return round(raw_diff * 100, 2)
    elif direction == "NO":
        return round(-raw_diff * 100, 2)
    return None


def backfill_market_baseline_brier() -> int:
    """
    One-off backfill for resolved rows written before market_baseline_brier
    existed. Idempotent — only touches rows where the column is still NULL,
    safe to run repeatedly (e.g. from a migration script). Rows missing
    market_price at scan time are left NULL, never coerced to 0.5.
    Returns the number of rows updated.
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT call_id, market_price, direction, outcome FROM signals "
            "WHERE result IN ('WIN','LOSS') AND market_baseline_brier IS NULL"
        ).fetchall()

        updated = 0
        for r in rows:
            brier = _market_baseline_brier(r["market_price"], r["direction"] or "", r["outcome"] or "")
            if brier is None:
                continue
            conn.execute(
                "UPDATE signals SET market_baseline_brier=? WHERE call_id=?",
                (brier, r["call_id"]),
            )
            updated += 1
    return updated


def backfill_run_id() -> dict:
    """
    One-off backfill for rows with a blank run_id (powerbi-schema-hardening).
    Idempotent — only touches rows where run_id is still NULL/''.

    The ONLY genuine recovery path: a row with signal_call_id populated
    (pull_real_fills sets this when a real_fill is matched to a prior paper
    signal) whose referenced paper row has a real run_id — that's a true
    foreign-key traversal, not a guess. Rows with no signal_call_id (every
    research_probe row, and any unmatched real_fill) have no recorded link
    to a scan run at all: log_probe() never sets run_id, and
    pull_real_fills() hardcodes run_id='' for every real_fill row regardless
    of match status. These are NEVER backfilled via nearest-timestamp
    matching or any other inference — that would fabricate a relationship
    that doesn't exist in the data, the same discipline applied to
    market_baseline_brier's missing-price rows.

    Returns {"backfilled": n, "unrecoverable": n} — unrecoverable is exact,
    not an estimate, so it can be reported rather than silently absorbed.
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT call_id, signal_call_id FROM signals "
            "WHERE (run_id IS NULL OR run_id = '')"
        ).fetchall()

        backfilled = 0
        unrecoverable = 0
        for r in rows:
            sig_call_id = r["signal_call_id"]
            recovered_run_id = None
            if sig_call_id:
                ref = conn.execute(
                    "SELECT run_id FROM signals WHERE call_id=?", (sig_call_id,)
                ).fetchone()
                if ref and ref["run_id"]:
                    recovered_run_id = ref["run_id"]

            if recovered_run_id:
                conn.execute(
                    "UPDATE signals SET run_id=? WHERE call_id=?",
                    (recovered_run_id, r["call_id"]),
                )
                backfilled += 1
            else:
                unrecoverable += 1

    return {"backfilled": backfilled, "unrecoverable": unrecoverable}


def audit_run_id_coverage() -> dict:
    """
    Read-only audit (powerbi-schema-hardening): how many signals rows have a
    populated run_id, broken down by source, so a blank run_id can be told
    apart from a bug. A blank run_id is structurally expected for
    research_probe (log_probe never sets it) and real_fill
    (pull_real_fills hardcodes '') — it is NOT expected for source='paper',
    where main.py always attaches the current run_id.
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT source, run_id FROM signals"
        ).fetchall()

    total = len(rows)
    blank = sum(1 for r in rows if not r["run_id"])
    by_source: dict = {}
    for r in rows:
        src = r["source"] or "(none)"
        bucket = by_source.setdefault(src, {"total": 0, "blank": 0})
        bucket["total"] += 1
        if not r["run_id"]:
            bucket["blank"] += 1

    return {"total": total, "blank": blank, "by_source": by_source}


def audit_source_discriminator() -> dict:
    """
    Read-only audit (powerbi-schema-hardening): confirms the source column
    is populated on every row and lists every distinct value actually
    present, so "no value other than paper appears" can be verified rather
    than assumed. As of 2026-07-24 this is FALSE on the real DB — paper,
    real_fill, and research_probe all currently appear — but the paper-only
    filter (source='paper' OR source IS NULL, used throughout core/logger.py)
    already correctly excludes the other two, so the discriminator is still
    reliable; the notes assumption about *how many* values exist was wrong,
    not the mechanism itself.
    """
    with _db() as conn:
        rows = conn.execute("SELECT source FROM signals").fetchall()

    total = len(rows)
    blank = sum(1 for r in rows if not r["source"])
    counts: dict = {}
    for r in rows:
        val = r["source"] or "(blank)"
        counts[val] = counts.get(val, 0) + 1

    return {"total": total, "blank": blank, "by_value": counts}


def resolve_outcomes(config: dict) -> int:
    """
    Checks all unresolved calls against the Kalshi API and fills in outcomes.
    Returns count of newly resolved calls.
    """
    from core import kalshi as _kalshi
    from core.sizing import compute_stake_size
    import time as _time

    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT call_id, ticker, direction, market_price, confidence, "
                "entry_price, fill_count, fill_fee, source "
                "FROM signals WHERE outcome IS NULL OR outcome = ''"
            ).fetchall()
    except Exception:
        return 0

    if not rows:
        return 0

    resolved_count = 0
    for i, row in enumerate(rows):
        if i > 0:
            _time.sleep(0.3)  # ~3 req/s — stay well under Kalshi rate limits
        ticker = row["ticker"]
        if not ticker:
            continue
        market = None
        for attempt in range(3):
            try:
                market = _kalshi.fetch_market(config, ticker)
                break
            except Exception as e:
                if attempt < 2:
                    _time.sleep(1.5 * (2 ** attempt))  # 1.5s, 3.0s backoff
                else:
                    print(f"  [logger] resolve_outcomes: failed on {ticker} after 3 attempts: {e}")
        if market is None:
            continue
        try:
            result = (market.get("result") or "").lower()
            if result not in ("yes", "no"):
                continue

            outcome   = result.upper()
            direction = (row["direction"] or "").upper()
            win       = direction == outcome

            source = row["source"] or "paper"
            if source == "real_fill":
                # Use actual fill price; subtract fee per contract.
                price       = float(row["entry_price"] or row["market_price"] or 0)
                fill_count  = float(row["fill_count"] or 1)
                fee_per_unit = float(row["fill_fee"] or 0) / fill_count
            else:
                price        = float(row["market_price"] or 0)
                fee_per_unit = 0.0

            # Binary contract payoff per $1 notional net of fees:
            # YES bought at p: win → +(1-p) - fee, lose → -p - fee
            # NO  bought at p: win → +p     - fee, lose → -(1-p) - fee
            if direction == "YES":
                pnl = round(((1.0 - price) if win else -price) - fee_per_unit, 4)
            elif direction == "NO":
                pnl = round((price if win else -(1.0 - price)) - fee_per_unit, 4)
            else:
                pnl = 0.0

            baseline_brier = _market_baseline_brier(row["market_price"], direction, outcome)
            stake_size = compute_stake_size({"confidence": row["confidence"]}, config)
            # last_price_dollars is the same field settled_fetcher.py persists
            # as settled_markets.last_price -- the market's final traded price
            # before settlement. Read here from the same fetch_market() call
            # already made above, zero extra API cost.
            late_price = _to_float(market.get("last_price_dollars"))
            drift_pp   = _market_drift_pp(late_price, row["market_price"], direction)

            with _db() as conn:
                conn.execute(
                    "UPDATE signals SET outcome=?, result=?, pnl_if_traded=?, "
                    "market_baseline_brier=?, stake_size_hypothetical=?, market_drift_pp=?, "
                    "resolved_at=? "
                    "WHERE call_id=?",
                    (outcome, "WIN" if win else "LOSS", pnl, baseline_brier, stake_size,
                     drift_pp, datetime.now(timezone.utc).isoformat(), row["call_id"])
                )
            resolved_count += 1
        except Exception as e:
            print(f"  [logger] resolve_outcomes: failed on {ticker}: {e}")

    return resolved_count


# ── Stats ─────────────────────────────────────────────────────────────────────

# Paper signals are source='paper' or NULL (pre-migration rows).
_PAPER   = "source = 'paper' OR source IS NULL"
_NO_PASS = f"({_PAPER}) AND direction != 'PASS'"


# ── Query helpers (MCP server / conversational lookup) ───────────────────────
# These read the same DB_PATH the pipeline writes — no copies, no snapshots.

def get_signal_log(limit: int = 50, resolved_only: bool = False,
                    ticker: str | None = None) -> list[dict]:
    """
    Most recent paper signals (non-PASS), newest first.

    resolved_only restricts to rows with a settled outcome; ticker filters
    to an exact ticker match. Used by the MCP signal-log query tool.
    """
    where = [_NO_PASS]
    params: list = []
    if resolved_only:
        where.append("outcome != '' AND outcome IS NOT NULL")
    if ticker:
        where.append("ticker = ?")
        params.append(ticker)
    params.append(limit)
    try:
        with _db() as conn:
            rows = conn.execute(
                f"SELECT * FROM signals WHERE {' AND '.join(where)} "
                f"ORDER BY timestamp DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_resolved_track_record(days: int | None = None) -> list[dict]:
    """
    Every resolved paper signal (non-PASS) with its score and actual outcome.

    Uses the identical filter as get_stats()/get_brier_score() so the count
    always matches the headline "n resolved" figure reported elsewhere.

    days (GOAL_subscriber_report.md Phase 5): when given, restricts to
    signals logged in the last N days -- e.g. days=7 for the subscriber
    digest's "how last week's calls landed" recap. None (default) preserves
    the original all-time behavior for existing callers.
    """
    where  = [_NO_PASS, "outcome != ''", "outcome IS NOT NULL"]
    params: list = []
    if days is not None:
        where.append("timestamp >= ?")
        params.append((datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    try:
        with _db() as conn:
            rows = conn.execute(
                f"SELECT * FROM signals WHERE {' AND '.join(where)} "
                f"ORDER BY timestamp DESC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


_TICKER_DATE_SUFFIX_RE = re.compile(r"-\d{2}[A-Z]{3}\d{0,2}$")


def _ticker_stem(ticker: str) -> str:
    """
    Strips exactly one trailing rolling-window expiry token (e.g. -26AUG,
    -27JAN01) from a ticker, leaving the part that identifies the
    underlying real-world question. Two tickers sharing a stem are the
    same story re-flagged under a new expiry window, not independent
    markets -- see backlog: rolled-market-repeat-detection (the
    KXCABLEAVE-26MAY22-{26JUN,26JUL,26AUG,26SEP} finding, later confirmed
    to recur across at least 4 distinct stories, 2026-08-24).

    Strips ONE token, not "+" (one-or-more) -- a ticker like
    KXCABLEAVE-26MAY22-26JUN has two date-shaped segments (a creation
    date and an expiry date; "26MAY22" itself matches the same
    \\d{2}[A-Z]{3}\\d{0,2} shape as the expiry token it's paired with).
    Stripping both would collapse it to bare "KXCABLEAVE" and wrongly
    merge it with any other, unrelated KXCABLEAVE-prefixed story that
    happens to exist. Confirmed against the real ticker format: for this
    project's actual KX<TOPIC>-<created>-<expiry> shape, the creation-date
    segment is part of what identifies a distinct real-world question,
    only the expiry segment rolls.
    """
    return _TICKER_DATE_SUFFIX_RE.sub("", ticker)


def get_repeat_family(ticker: str) -> list[dict]:
    """
    Every OTHER ticker sharing `ticker`'s stem (see _ticker_stem) -- the
    same real-world question, previously flagged under a different expiry
    window. One row per sibling ticker: its most recent signal (timestamp,
    direction, our_estimate, market_price, outcome). Empty list if `ticker`
    has no stem-mates, including when the stem is the whole ticker (no
    trailing date suffix matched) -- a bare, unrolled ticker never has
    siblings by definition.

    Deliberately no automatic confidence penalty derived from this --
    the 2026-08-24 investigation found the pattern's actual effect on
    calibration was mixed (badly overconfident in one family, fine to
    good, including two wins, in three others). This is visibility for
    the scorer prompt to weigh, not a rule.
    """
    stem = _ticker_stem(ticker)
    if stem == ticker:
        return []
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT s.* FROM signals s
                INNER JOIN (
                    SELECT ticker, MAX(timestamp) AS max_ts
                    FROM signals
                    WHERE ({_PAPER}) AND ticker != ? AND ticker LIKE ?
                    GROUP BY ticker
                ) latest ON s.ticker = latest.ticker AND s.timestamp = latest.max_ts
                WHERE ({_PAPER})
                ORDER BY s.timestamp ASC
                """,
                (ticker, f"{stem}%"),
            ).fetchall()
        # LIKE '<stem>%' over-matches any ticker sharing the stem as a
        # prefix, not just ones matching the full date-suffix pattern
        # (e.g. a genuinely different ticker that happens to start with
        # the same characters) -- filter to true stem equality before
        # returning, not just prefix match.
        return [dict(r) for r in rows if _ticker_stem(r["ticker"]) == stem]
    except Exception:
        return []


def get_titles_for_tickers(tickers: list[str]) -> dict[str, str]:
    """
    Latest known title per ticker, for surfaces that only have the raw
    ticker (e.g. data/whale_history/streak.json, which predates title
    ever being persisted alongside it) and need something a human can
    actually read. One batched exact-match query, not N calls to
    get_market_data()'s LIKE-based lookup -- correct (no substring
    over-matching between tickers that share a prefix) and cheap even
    for a large ticker list. Tickers with no signals row at all are
    simply absent from the returned dict, not mapped to '' or None --
    callers should fall back to showing the raw ticker themselves.
    """
    if not tickers:
        return {}
    try:
        with _db() as conn:
            placeholders = ",".join("?" for _ in tickers)
            rows = conn.execute(
                f"""
                SELECT ticker, title FROM signals s
                WHERE ticker IN ({placeholders}) AND title != '' AND title IS NOT NULL
                  AND timestamp = (
                      SELECT MAX(timestamp) FROM signals
                      WHERE ticker = s.ticker AND title != '' AND title IS NOT NULL
                  )
                """,
                tickers,
            ).fetchall()
        return {r["ticker"]: r["title"] for r in rows}
    except Exception:
        return {}


def get_market_meta_for_tickers(tickers: list[str]) -> dict[str, dict]:
    """
    Like get_titles_for_tickers(), but also returns series_ticker/
    event_ticker so a caller can build a real clickable Kalshi link
    (core.kalshi.kalshi_market_url) instead of showing a bare, often
    unreadable ticker string (e.g. "KXFDAAPPROVE-MDMA-27JAN01"). Added for
    the Smart Money dashboard's whale-activity table -- a separate
    function rather than changing get_titles_for_tickers()'s existing
    dict[str, str] contract, which other/future callers may still want.
    Tickers with no signals row at all are simply absent from the
    returned dict; callers should fall back to the raw ticker themselves.
    """
    if not tickers:
        return {}
    try:
        with _db() as conn:
            placeholders = ",".join("?" for _ in tickers)
            rows = conn.execute(
                f"""
                SELECT ticker, title, series_ticker, event_ticker FROM signals s
                WHERE ticker IN ({placeholders}) AND title != '' AND title IS NOT NULL
                  AND timestamp = (
                      SELECT MAX(timestamp) FROM signals
                      WHERE ticker = s.ticker AND title != '' AND title IS NOT NULL
                  )
                """,
                tickers,
            ).fetchall()
        return {r["ticker"]: {"title": r["title"], "series_ticker": r["series_ticker"],
                               "event_ticker": r["event_ticker"]} for r in rows}
    except Exception:
        return {}


def get_market_data(ticker: str | None = None, date: str | None = None) -> list[dict]:
    """
    Scored market data for a ticker (partial match) or a signal date
    (YYYY-MM-DD, matched against the timestamp prefix). At least one of
    ticker/date must be given; passing neither returns no rows.
    """
    if not ticker and not date:
        return []
    where = [_NO_PASS]
    params: list = []
    if ticker:
        where.append("ticker LIKE ?")
        params.append(f"%{ticker}%")
    if date:
        where.append("timestamp LIKE ?")
        params.append(f"{date}%")
    try:
        with _db() as conn:
            rows = conn.execute(
                f"SELECT * FROM signals WHERE {' AND '.join(where)} "
                f"ORDER BY timestamp DESC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_stats() -> dict:
    """Stats for paper (simulated) signals only — never blends with real fills."""
    _NO_PASS = f"({_PAPER}) AND direction != 'PASS'"
    try:
        with _db() as conn:
            total     = conn.execute(f"SELECT COUNT(*) FROM signals WHERE {_NO_PASS}").fetchone()[0]
            resolved  = conn.execute(
                f"SELECT COUNT(*) FROM signals WHERE {_NO_PASS} AND outcome != '' AND outcome IS NOT NULL"
            ).fetchone()[0]
            wins      = conn.execute(
                f"SELECT COUNT(*) FROM signals WHERE {_NO_PASS} AND result = 'WIN'"
            ).fetchone()[0]
            avg_edge  = conn.execute(
                f"SELECT AVG(edge) FROM signals WHERE {_NO_PASS} AND edge IS NOT NULL"
            ).fetchone()[0]
            total_pnl = conn.execute(
                f"SELECT SUM(pnl_if_traded) FROM signals WHERE {_NO_PASS} AND pnl_if_traded IS NOT NULL"
            ).fetchone()[0]
            best  = conn.execute(
                f"SELECT * FROM signals WHERE {_NO_PASS} AND edge IS NOT NULL ORDER BY edge DESC LIMIT 1"
            ).fetchone()
            worst = conn.execute(
                f"SELECT * FROM signals WHERE {_NO_PASS} AND edge IS NOT NULL ORDER BY edge ASC LIMIT 1"
            ).fetchone()
    except Exception:
        return {"total_calls": 0, "resolved": 0, "win_rate": None,
                "avg_edge_captured": None, "total_hypothetical_pnl": None,
                "best_call": None, "worst_call": None}

    return {
        "total_calls":            total,
        "resolved":               resolved,
        "win_rate":               (wins / resolved * 100) if resolved else None,
        "avg_edge_captured":      avg_edge,
        "total_hypothetical_pnl": total_pnl,
        "best_call":              dict(best)  if best  else None,
        "worst_call":             dict(worst) if worst else None,
    }


def get_stats_by_sig() -> dict:
    """
    Win rate broken down by which mode-independent signal fired on each paper signal.
    Returns a dict keyed by signal type: sig_edge / sig_drift / sig_br_none.
    Only includes resolved paper signals.
    """
    result = {}
    for sig_col in ("sig_edge", "sig_drift", "sig_br_none"):
        try:
            with _db() as conn:
                row = conn.execute(
                    f"""
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
                        AVG(edge) AS avg_edge,
                        SUM(pnl_if_traded) AS total_pnl
                    FROM signals
                    WHERE ({_PAPER})
                      AND outcome != '' AND outcome IS NOT NULL
                      AND {sig_col} = 1
                    """
                ).fetchone()
            total = row["total"] or 0
            wins  = row["wins"] or 0
            result[sig_col] = {
                "total":     total,
                "wins":      wins,
                "win_rate":  round(wins / total * 100, 1) if total else None,
                "avg_edge":  row["avg_edge"],
                "total_pnl": row["total_pnl"],
            }
        except Exception:
            result[sig_col] = {"total": 0, "wins": 0, "win_rate": None,
                               "avg_edge": None, "total_pnl": None}
    return result


def get_stats_by_flag_path() -> list[dict]:
    """
    Win rate and P&L broken down by flag_path (EDGE / BR_NONE / DRIFT / HEURISTIC / WATCHLIST).
    Only includes paper signals with a resolved outcome.
    Returns a list of dicts sorted by win_rate descending.
    """
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    COALESCE(flag_path, 'UNKNOWN') AS path,
                    COUNT(*) AS total,
                    SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
                    AVG(edge) AS avg_edge,
                    SUM(pnl_if_traded) AS total_pnl
                FROM signals
                WHERE ({_PAPER})
                  AND outcome != '' AND outcome IS NOT NULL
                GROUP BY flag_path
                ORDER BY wins * 1.0 / COUNT(*) DESC
                """
            ).fetchall()
    except Exception:
        return []

    result = []
    for r in rows:
        total = r["total"]
        wins  = r["wins"] or 0
        result.append({
            "flag_path":  r["path"],
            "total":      total,
            "wins":       wins,
            "win_rate":   round(wins / total * 100, 1) if total else None,
            "avg_edge":   r["avg_edge"],
            "total_pnl":  r["total_pnl"],
        })
    return result


def log_probe(probe: dict) -> str:
    """
    Insert a research probe result as source='research_probe', segment='research_probe'.
    Probe rows are unresolved at log time — resolve_outcomes settles them later.
    Returns the new call_id.
    """
    call_id = str(uuid.uuid4())[:8]
    try:
        with _db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO signals
                (call_id, timestamp, ticker, title, market_price, direction,
                 confidence, outcome, result, source, segment,
                 market_price_at_probe, claude_estimate, divergence, predicted_direction)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                call_id,
                datetime.now(timezone.utc).isoformat(),
                probe.get("ticker", ""),
                probe.get("title", ""),
                probe.get("market_price_at_probe"),
                probe.get("predicted_direction", ""),
                probe.get("confidence", ""),
                "", "",
                "research_probe",
                "research_probe",
                probe.get("market_price_at_probe"),
                probe.get("claude_estimate"),
                probe.get("divergence"),
                probe.get("predicted_direction", ""),
            ))
    except Exception as e:
        print(f"  [logger] log_probe: failed for {probe.get('ticker')}: {e}")
    return call_id


def get_stats_real() -> dict:
    """Stats for real Kalshi fills — separate from paper signals."""
    try:
        with _db() as conn:
            total    = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source='real_fill'"
            ).fetchone()[0]
            resolved = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source='real_fill' "
                "AND outcome != '' AND outcome IS NOT NULL"
            ).fetchone()[0]
            wins     = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source='real_fill' AND result='WIN'"
            ).fetchone()[0]
            matched  = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source='real_fill' AND from_signal=1"
            ).fetchone()[0]
            aligned  = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source='real_fill' AND direction_aligned=1"
            ).fetchone()[0]
            total_pnl = conn.execute(
                "SELECT SUM(pnl_if_traded) FROM signals WHERE source='real_fill' "
                "AND pnl_if_traded IS NOT NULL"
            ).fetchone()[0]
    except Exception:
        return {"total_fills": 0, "resolved": 0, "win_rate": None,
                "matched_signals": 0, "aligned": 0, "total_net_pnl": None}

    return {
        "total_fills":     total,
        "resolved":        resolved,
        "win_rate":        (wins / resolved * 100) if resolved else None,
        "matched_signals": matched,
        "aligned":         aligned,
        "total_net_pnl":   total_pnl,
    }


def get_stats_probe(high_divergence_threshold: float = 0.10) -> dict:
    """
    Stats for research_probe rows.
    Once probe rows resolve, reports hit rate and high-divergence hit rate.
    At run time, all rows are unresolved — call again after settlement.

    NOTE: run-one divergences are hypotheses only. Edge verdict requires
    resolved outcomes and cannot be determined until markets settle.
    """
    try:
        with _db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source='research_probe'"
            ).fetchone()[0]
            resolved = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source='research_probe' "
                "AND outcome != '' AND outcome IS NOT NULL"
            ).fetchone()[0]
            correct = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source='research_probe' AND result='WIN'"
            ).fetchone()[0]
            # High-divergence subset: |divergence| >= threshold
            hi_total = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source='research_probe' "
                "AND ABS(divergence) >= ?", (high_divergence_threshold,)
            ).fetchone()[0]
            hi_resolved = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source='research_probe' "
                "AND ABS(divergence) >= ? AND outcome != '' AND outcome IS NOT NULL",
                (high_divergence_threshold,)
            ).fetchone()[0]
            hi_correct = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source='research_probe' "
                "AND ABS(divergence) >= ? AND result='WIN'",
                (high_divergence_threshold,)
            ).fetchone()[0]
            avg_div = conn.execute(
                "SELECT AVG(ABS(divergence)) FROM signals WHERE source='research_probe' "
                "AND divergence IS NOT NULL"
            ).fetchone()[0]
    except Exception:
        return {"total_probes": 0, "resolved": 0, "hit_rate": None,
                "hi_div_total": 0, "hi_div_resolved": 0, "hi_div_hit_rate": None,
                "avg_abs_divergence": None, "verdict": "PENDING — no resolved probes yet"}

    hit_rate    = (correct / resolved * 100)    if resolved    else None
    hi_hit_rate = (hi_correct / hi_resolved * 100) if hi_resolved else None

    if resolved == 0:
        verdict = "PENDING — no resolved probes yet. Divergences logged, awaiting settlement."
    elif resolved < total:
        verdict = f"PARTIAL — {resolved}/{total} probes resolved. Full verdict pending."
    else:
        verdict = (
            f"COMPLETE — {hit_rate:.0f}% overall hit rate, "
            f"{hi_hit_rate:.0f}% on high-divergence (>={high_divergence_threshold*100:.0f}%) calls."
            if hit_rate is not None else "COMPLETE — insufficient data."
        )

    return {
        "total_probes":      total,
        "resolved":          resolved,
        "hit_rate":          hit_rate,
        "hi_div_total":      hi_total,
        "hi_div_resolved":   hi_resolved,
        "hi_div_hit_rate":   hi_hit_rate,
        "avg_abs_divergence": avg_div,
        "verdict":           verdict,
    }


def brier_component(value, direction: str, result: str) -> float | None:
    """
    Raw (unrounded) (value - outcome_binary)^2 — the Brier building block
    shared by the scorer Brier (value=our_estimate) and the market-baseline
    Brier (value=market_price). get_brier_score(), get_market_baseline_brier_score(),
    and core/export_to_csv.py's per-row brier_scorer/brier_market export
    columns all call this one function on the same raw source columns
    (our_estimate/market_price, direction, result), so analysis/calibration.py
    and the Power BI export can never disagree on an individual row.

    outcome_binary = 1 if the trade was a WIN (direction resolved correctly),
    0 if LOSS — same derivation get_brier_score() has always used:
      - YES trades: outcome_binary = 1 if WIN, 0 if LOSS
      - NO trades:  outcome_binary = 0 if WIN (YES didn't happen), 1 if LOSS

    Returns None — never 0.5 — when value is missing or direction/result
    don't resolve to a clean WIN/LOSS outcome. Callers should sum raw values
    and round only the final mean for an aggregate; round per-row for display.
    """
    if value is None or direction not in ("YES", "NO") or result not in ("WIN", "LOSS"):
        return None
    win = result == "WIN"
    outcome_binary = (1.0 if win else 0.0) if direction == "YES" else (0.0 if win else 1.0)
    return (float(value) - outcome_binary) ** 2


def get_brier_score() -> dict:
    """
    Compute Brier score for resolved paper signals.

    Brier score = mean((estimate - outcome_binary)^2), via brier_component().
      - estimate: our_estimate (Claude's probability for YES)

    Lower is better. Perfect calibration = 0. Random 50/50 = 0.25.
    Returns None if no resolved signals exist.
    """
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT our_estimate, direction, result
                FROM signals
                WHERE ({_PAPER})
                  AND result IS NOT NULL AND result != ''
                  AND our_estimate IS NOT NULL
                  AND direction IN ('YES','NO')
                """
            ).fetchall()
    except Exception:
        return {"brier_score": None, "n": 0, "label": "PENDING"}

    if not rows:
        return {"brier_score": None, "n": 0, "label": "PENDING — no resolved signals"}

    total_sq = 0.0
    for r in rows:
        total_sq += brier_component(r["our_estimate"], r["direction"], r["result"])

    brier = total_sq / len(rows)
    if brier <= 0.10:
        label = "EXCELLENT"
    elif brier <= 0.20:
        label = "GOOD"
    elif brier <= 0.25:
        label = "FAIR (near random)"
    else:
        label = "POOR"

    return {"brier_score": round(brier, 4), "n": len(rows), "label": label}


def get_market_baseline_brier_score() -> dict:
    """
    Baseline Brier score: same population, same outcome definition, and same
    formula as get_brier_score(), but scores the market price at scan time
    instead of our_estimate. Compares against get_brier_score() to answer
    whether the scorer adds real edge over the market price, since the
    scorer prompt injects the current market price (core/scorer.py:649) and
    explicitly instructs staying near it (core/scorer.py:245-253) — scorer
    Brier alone can't distinguish genuine edge from echoing the price back.

    Rows with no market_price at scan time are excluded, never coerced to
    0.5 — a missing price has no baseline to compare against.
    Returns None if no resolved signal has a usable market_price.
    """
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT market_price, direction, result
                FROM signals
                WHERE ({_PAPER})
                  AND result IS NOT NULL AND result != ''
                  AND market_price IS NOT NULL
                  AND direction IN ('YES','NO')
                """
            ).fetchall()
    except Exception:
        return {"brier_score": None, "n": 0, "label": "PENDING"}

    if not rows:
        return {"brier_score": None, "n": 0, "label": "PENDING — no resolved signals with a market price"}

    total_sq = 0.0
    for r in rows:
        total_sq += brier_component(r["market_price"], r["direction"], r["result"])

    brier = total_sq / len(rows)
    if brier <= 0.10:
        label = "EXCELLENT"
    elif brier <= 0.20:
        label = "GOOD"
    elif brier <= 0.25:
        label = "FAIR (near random)"
    else:
        label = "POOR"

    return {"brier_score": round(brier, 4), "n": len(rows), "label": label}


def get_brier_history() -> list[dict]:
    """
    backlog: brier-tracking. Returns the per-run cumulative Brier snapshots
    log_run() writes onto the runs table, oldest first -- the actual
    "over time" view get_brier_score()/get_market_baseline_brier_score()
    can't provide on their own, since those two only ever compute the
    CURRENT aggregate at call time.

    Each entry: {run_id, timestamp, brier_scorer, brier_market, n}. Runs
    logged before this existed (or where brier_n was never recorded because
    log_run() failed, or 0 signals had resolved yet) have brier_n IS NULL
    and are excluded -- a missing snapshot is not the same as a genuine
    "0 signals resolved at this point" data point, which br_n=0 would be.
    Consecutive runs frequently share the same values (the underlying
    resolved-signal population barely changes run to run) -- that's a real
    feature of a slow-resolving market, not a computation bug.
    """
    try:
        with _db() as conn:
            rows = conn.execute(
                """
                SELECT run_id, timestamp, brier_scorer, brier_market, brier_n
                FROM runs
                WHERE brier_n IS NOT NULL
                ORDER BY timestamp ASC
                """
            ).fetchall()
    except Exception:
        return []

    return [
        {
            "run_id":       r["run_id"],
            "timestamp":    r["timestamp"],
            "brier_scorer": r["brier_scorer"],
            "brier_market": r["brier_market"],
            "n":            r["brier_n"],
        }
        for r in rows
    ]


def get_market_drift_stats() -> dict:
    """
    Aggregate CLV-style drift stats (GOAL_subscriber_report.md Phase 4) over
    every resolved paper signal that has a market_drift_pp value -- always
    paired with its sample size `n`, per the doc's explicit guardrail: never
    print an accuracy/win-rate/drift number without its N beside it. Callers
    (the digest, the Track Record page) must render both together, never
    avg_drift_pp alone.

    n is deliberately independent of get_stats()'s `resolved` count: not
    every resolved row has a drift value yet (existing rows before Phase 4
    landed are NULL until backfill_market_drift() runs; PASS/undirected rows
    never get one). Returns n=0/None fields, never a fabricated 0.0, when no
    row has a drift value yet.
    """
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT market_drift_pp FROM signals
                WHERE ({_PAPER}) AND market_drift_pp IS NOT NULL
                """
            ).fetchall()
    except Exception:
        return {"avg_drift_pp": None, "pct_positive_drift": None, "n": 0}

    if not rows:
        return {"avg_drift_pp": None, "pct_positive_drift": None, "n": 0}

    drifts = [r["market_drift_pp"] for r in rows]
    n = len(drifts)
    positive = sum(1 for d in drifts if d > 0)

    return {
        "avg_drift_pp":        round(sum(drifts) / n, 2),
        "pct_positive_drift":  round(positive / n * 100, 1),
        "n":                   n,
    }


def backfill_market_drift(config: dict, limit: int | None = None, dry_run: bool = False) -> dict:
    """
    One-off backfill (GOAL_subscriber_report.md Phase 4) for resolved rows
    written before market_drift_pp existed. Idempotent -- only touches rows
    where the column is still NULL, safe to run repeatedly.

    Unlike backfill_market_baseline_brier (pure arithmetic over columns
    already on the row), this needs a genuinely new number -- the market's
    late/settlement price -- that was never captured for these older rows.
    Checked in order, cheapest first:
      1. settled_markets.last_price (already fetched by settled_fetcher.py,
         zero API cost) via a ticker join.
      2. A live Kalshi fetch_market() call, same retry/backoff precedent as
         resolve_outcomes(), for rows settled_markets doesn't have yet.
    Rows where neither source has a usable price are left NULL, never
    coerced to a guess -- same "don't fabricate a relationship that doesn't
    exist in the data" discipline as every other backfill in this module.

    GOAL_phase2-6_decisions.md Decision 2 preflight support:
      limit: process at most this many eligible rows this call -- lets a
        caller dry-run (or live-run) a small batch (e.g. 5) before
        committing to the full remaining population.
      dry_run: compute everything (including the live Kalshi fetch) but
        never write to the DB. Every candidate row's computed values are
        returned in "rows" -- {ticker, market_price_at_flag, late_price,
        market_drift_pp, direction} -- so a human can eyeball the sign
        convention before any write happens. "backfilled" stays 0 in this
        mode; a row that WOULD have been written is instead counted in
        "would_backfill".

    Every ticker actually fetched (live or from settled_markets) is
    printed as it's processed, so a partial run's progress is visible --
    idempotency (the NULL-only WHERE clause) already makes re-running safe,
    this is purely for operator visibility into a long-running batch.

    Returns {"backfilled": n, "would_backfill": n, "from_settled_markets": n,
    "from_live_fetch": n, "unrecoverable": n, "rows": [...] (dry_run only)}.
    """
    from core import kalshi as _kalshi
    import time as _time

    with _db() as conn:
        query = (
            "SELECT call_id, ticker, direction, market_price FROM signals "
            f"WHERE ({_PAPER}) AND result IN ('WIN','LOSS') "
            "AND market_drift_pp IS NULL AND ticker != '' "
            "ORDER BY timestamp ASC"
        )
        if limit is not None:
            query += " LIMIT ?"
            rows = conn.execute(query, (limit,)).fetchall()
        else:
            rows = conn.execute(query).fetchall()

    empty_result = {
        "backfilled": 0, "would_backfill": 0, "from_settled_markets": 0,
        "from_live_fetch": 0, "unrecoverable": 0, "rows": [],
    }
    if not rows:
        return empty_result

    try:
        with _db() as conn:
            settled = {
                r["ticker"]: r["last_price"]
                for r in conn.execute(
                    "SELECT ticker, last_price FROM settled_markets"
                ).fetchall()
            }
    except sqlite3.OperationalError:
        # settled_markets is created by backtesting/settled_fetcher.py, not
        # core/logger.py's own _init_db() -- may not exist in every DB.
        settled = {}

    backfilled = would_backfill = from_settled = from_live = unrecoverable = 0
    dry_run_rows: list = []
    for i, row in enumerate(rows):
        ticker = row["ticker"]
        late_price = settled.get(ticker)
        source = "settled_markets" if late_price is not None else None

        if late_price is None:
            if i > 0:
                _time.sleep(0.3)  # ~3 req/s, matches resolve_outcomes()'s rate-limit courtesy
            market = None
            for attempt in range(3):
                try:
                    market = _kalshi.fetch_market(config, ticker)
                    break
                except Exception as e:
                    is_rate_limited = "429" in str(e)
                    if attempt < 2:
                        # Longer, more deliberate backoff specifically for a
                        # rate-limit response -- don't hammer Kalshi with the
                        # same short retry cadence as a generic transient error.
                        backoff = (5.0 * (attempt + 1)) if is_rate_limited else (1.5 * (2 ** attempt))
                        _time.sleep(backoff)
                    else:
                        print(f"  [logger] backfill_market_drift: failed on {ticker} after 3 attempts: {e}")
            if market is not None:
                late_price = _to_float(market.get("last_price_dollars"))
                if late_price is not None:
                    source = "live_fetch"

        print(f"  [logger] backfill_market_drift: {ticker} -- "
              f"source={source or 'unavailable'}, late_price={late_price}")

        if late_price is None:
            unrecoverable += 1
            continue

        drift_pp = _market_drift_pp(late_price, row["market_price"], row["direction"] or "")
        if drift_pp is None:
            unrecoverable += 1
            continue

        if dry_run:
            would_backfill += 1
            dry_run_rows.append({
                "ticker":               ticker,
                "market_price_at_flag": row["market_price"],
                "late_price":           late_price,
                "market_drift_pp":      drift_pp,
                "direction":            row["direction"],
            })
            continue

        with _db() as conn:
            conn.execute(
                "UPDATE signals SET market_drift_pp=? WHERE call_id=?",
                (drift_pp, row["call_id"]),
            )
        backfilled += 1
        if source == "settled_markets":
            from_settled += 1
        else:
            from_live += 1

    return {
        "backfilled":           backfilled,
        "would_backfill":       would_backfill,
        "from_settled_markets": from_settled,
        "from_live_fetch":      from_live,
        "unrecoverable":        unrecoverable,
        "rows":                 dry_run_rows,
    }


def get_equity_curve_data() -> dict:
    """
    Cumulative equity curve (GOAL_subscriber_report.md Phase 6): "from real
    $1 Kalshi bets + logged signals" -- a real fill's actual realized
    pnl_if_traded takes priority over the paper/hypothetical $1 figure for
    the same signal wherever one exists (matched via signal_call_id), since
    that's the truer number; the hypothetical paper figure fills in every
    signal that was never actually traded for real money. One point per
    resolved paper signal (non-PASS), in chronological order.

    If a signal has more than one matched real fill, the most recently
    resolved one wins -- a real, if imperfect, choice among genuine data,
    never a fabricated blend.

    A curve that silently mixes real and hypothetical dollars is the exact
    "asterisk" move this project is trying to beat competitors on
    (GOAL_phase2-6_decisions.md Choice B) -- so every point is also tagged
    "real" or "paper" in `is_real`, and the two populations are counted
    separately, never just folded into one undifferentiated N.

    Returns {"points": [cumulative pnl, ...], "is_real": [bool, ...] (same
    length/order as points), "n": len(points), "real_n": count of real-fill
    points, "paper_n": count of paper points, "final": last cumulative value
    or None}.
    """
    try:
        with _db() as conn:
            paper_rows = conn.execute(
                f"SELECT call_id, timestamp, pnl_if_traded FROM signals "
                f"WHERE ({_NO_PASS}) AND result IN ('WIN','LOSS') "
                f"AND pnl_if_traded IS NOT NULL"
            ).fetchall()
            real_rows = conn.execute(
                "SELECT signal_call_id, pnl_if_traded, timestamp FROM signals "
                "WHERE source='real_fill' AND signal_call_id IS NOT NULL "
                "AND result IN ('WIN','LOSS') AND pnl_if_traded IS NOT NULL "
                "ORDER BY timestamp ASC"
            ).fetchall()
    except Exception:
        return {"points": [], "is_real": [], "n": 0, "real_n": 0, "paper_n": 0, "final": None}

    # ORDER BY timestamp ASC + dict-comprehension last-write-wins is what
    # actually makes "the most recently resolved fill wins" (this function's
    # own docstring) true when one signal has more than one matched real
    # fill -- a plain unordered query left this to whatever order SQLite
    # happened to return rows in, which isn't guaranteed to be chronological.
    real_pnl_by_call = {r["signal_call_id"]: r["pnl_if_traded"] for r in real_rows}

    ordered = sorted(paper_rows, key=lambda r: r["timestamp"] or "")
    running = 0.0
    points: list = []
    is_real: list = []
    real_n = paper_n = 0
    for r in ordered:
        is_real_point = r["call_id"] in real_pnl_by_call
        pnl = real_pnl_by_call.get(r["call_id"], r["pnl_if_traded"])
        running += float(pnl)
        points.append(round(running, 4))
        is_real.append(is_real_point)
        if is_real_point:
            real_n += 1
        else:
            paper_n += 1

    return {
        "points":  points,
        "is_real": is_real,
        "n":       len(points),
        "real_n":  real_n,
        "paper_n": paper_n,
        "final":   points[-1] if points else None,
    }


def get_stats_by_time_horizon() -> dict:
    """
    Win rate and P&L grouped by time_horizon bucket for resolved paper signals.

    Returns a dict keyed by bucket name (INTRADAY/WEEKLY/MONTHLY/QUARTERLY/LONG/None),
    each with: total, wins, losses, win_rate, total_pnl, avg_edge.
    """
    BUCKETS = ("INTRADAY", "WEEKLY", "MONTHLY", "QUARTERLY", "LONG")
    result = {b: {"total": 0, "wins": 0, "losses": 0, "win_rate": None,
                  "total_pnl": None, "avg_edge": None}
              for b in BUCKETS + ("OTHER",)}
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT time_horizon, result, pnl_if_traded, edge
                FROM signals
                WHERE ({_PAPER})
                  AND result IS NOT NULL AND result != ''
                """
            ).fetchall()
    except Exception:
        return result

    pnl_sum  = {k: 0.0 for k in result}
    edge_sum = {k: 0.0 for k in result}
    edge_n   = {k: 0   for k in result}

    for r in rows:
        th = r["time_horizon"] or "OTHER"
        if th not in result:
            th = "OTHER"
        result[th]["total"] += 1
        if r["result"] == "WIN":
            result[th]["wins"] += 1
        elif r["result"] == "LOSS":
            result[th]["losses"] += 1
        if r["pnl_if_traded"] is not None:
            pnl_sum[th] += float(r["pnl_if_traded"])
        if r["edge"] is not None:
            edge_sum[th] += float(r["edge"])
            edge_n[th]   += 1

    for th, d in result.items():
        n = d["total"]
        d["win_rate"] = d["wins"] / n * 100 if n else None
        d["total_pnl"] = pnl_sum[th] if n else None
        d["avg_edge"]  = edge_sum[th] / edge_n[th] if edge_n[th] else None

    return result


def get_stats_by_heuristic_alignment() -> dict:
    """
    Compare win rate for paper signals where Claude agreed vs overrode the heuristic.

    'aligned'  — heuristic_direction == direction (Claude and heuristic agree)
    'override' — heuristic_direction set, not NEUTRAL, and != direction (CLAUDE OVERRIDE fired)
    'no_heuristic' — heuristic_direction is NULL or NEUTRAL (no comparison possible)

    Only includes paper signals with a resolved outcome.
    Returns a dict with those three keys; each value has:
      total, wins, losses, win_rate (float|None), total_pnl (float|None), avg_edge (float|None)
    """
    result = {grp: {"total": 0, "wins": 0, "losses": 0,
                    "win_rate": None, "total_pnl": None, "avg_edge": None}
              for grp in ("aligned", "override", "no_heuristic")}
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT direction, heuristic_direction, result, pnl_if_traded, edge
                FROM signals
                WHERE ({_PAPER})
                  AND result IS NOT NULL AND result != ''
                  AND direction IN ('YES','NO')
                """
            ).fetchall()
    except Exception:
        return result

    totals  = {k: 0   for k in result}
    wins_d  = {k: 0   for k in result}
    losses_d= {k: 0   for k in result}
    pnl_sum = {k: 0.0 for k in result}
    edge_sum= {k: 0.0 for k in result}
    edge_n  = {k: 0   for k in result}

    for r in rows:
        direction = (r["direction"] or "").upper()
        hd        = (r["heuristic_direction"] or "").upper()

        if not hd or hd == "NEUTRAL":
            grp = "no_heuristic"
        elif hd == direction:
            grp = "aligned"
        else:
            grp = "override"

        totals[grp]   += 1
        if r["result"] == "WIN":
            wins_d[grp]  += 1
        elif r["result"] == "LOSS":
            losses_d[grp] += 1
        if r["pnl_if_traded"] is not None:
            pnl_sum[grp] += float(r["pnl_if_traded"])
        if r["edge"] is not None:
            edge_sum[grp] += float(r["edge"])
            edge_n[grp]   += 1

    for grp in result:
        n = totals[grp]
        result[grp]["total"]    = n
        result[grp]["wins"]     = wins_d[grp]
        result[grp]["losses"]   = losses_d[grp]
        result[grp]["win_rate"] = wins_d[grp] / n * 100 if n else None
        result[grp]["total_pnl"]= pnl_sum[grp] if n else None
        result[grp]["avg_edge"] = edge_sum[grp] / edge_n[grp] if edge_n[grp] else None

    return result


def get_stats_by_confidence() -> dict:
    """
    Win rate and P&L grouped by confidence level for resolved paper signals.

    Returns a dict keyed by "HIGH" / "MED" / "LOW", each with:
      total, wins, losses, win_rate (float|None), total_pnl (float|None)
    """
    result = {lvl: {"total": 0, "wins": 0, "losses": 0, "win_rate": None, "total_pnl": None}
              for lvl in ("HIGH", "MED", "LOW")}
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT confidence, result, pnl_if_traded
                FROM signals
                WHERE ({_PAPER})
                  AND result IS NOT NULL AND result != ''
                  AND confidence IN ('HIGH','MED','LOW')
                """
            ).fetchall()
    except Exception:
        return result

    for r in rows:
        lvl = r["confidence"]
        if lvl not in result:
            continue
        result[lvl]["total"] += 1
        if r["result"] == "WIN":
            result[lvl]["wins"] += 1
        elif r["result"] == "LOSS":
            result[lvl]["losses"] += 1
        if r["pnl_if_traded"] is not None:
            prev = result[lvl]["total_pnl"] or 0.0
            result[lvl]["total_pnl"] = prev + float(r["pnl_if_traded"])

    for lvl, d in result.items():
        if d["total"] > 0:
            d["win_rate"] = d["wins"] / d["total"] * 100

    return result


def get_stats_by_net_edge() -> dict:
    """
    Win rate and P&L grouped by net_edge (realizable edge after spread).

    Buckets:
      spread_dominant  — net_edge <= 0 (spread consumes all theoretical edge)
      thin             — 0 < net_edge <= 0.05 (tradeable but thin)
      good             — 0.05 < net_edge <= 0.10 (solid tradeable edge)
      strong           — net_edge > 0.10 (strong realizable edge)
      no_data          — net_edge IS NULL (spread not available)

    Only includes paper signals with a resolved outcome.
    Returns a dict keyed by those bucket names; each value has:
      total, wins, losses, win_rate (float|None), total_pnl (float|None), avg_edge (float|None)
    """
    buckets = ("spread_dominant", "thin", "good", "strong", "no_data")
    result  = {b: {"total": 0, "wins": 0, "losses": 0,
                   "win_rate": None, "total_pnl": None, "avg_edge": None}
               for b in buckets}
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT net_edge, result, pnl_if_traded, edge
                FROM signals
                WHERE ({_PAPER})
                  AND result IS NOT NULL AND result != ''
                  AND direction IN ('YES','NO')
                """
            ).fetchall()
    except Exception:
        return result

    pnl_sum  = {b: 0.0 for b in buckets}
    edge_sum = {b: 0.0 for b in buckets}
    edge_n   = {b: 0   for b in buckets}

    for r in rows:
        ne = r["net_edge"]
        if ne is None:
            b = "no_data"
        elif ne <= 0:
            b = "spread_dominant"
        elif ne <= 0.05:
            b = "thin"
        elif ne <= 0.10:
            b = "good"
        else:
            b = "strong"

        result[b]["total"] += 1
        if r["result"] == "WIN":
            result[b]["wins"] += 1
        elif r["result"] == "LOSS":
            result[b]["losses"] += 1
        if r["pnl_if_traded"] is not None:
            pnl_sum[b] += float(r["pnl_if_traded"])
        if r["edge"] is not None:
            edge_sum[b] += float(r["edge"])
            edge_n[b]   += 1

    for b in buckets:
        n = result[b]["total"]
        if n:
            result[b]["win_rate"]  = result[b]["wins"] / n * 100
            result[b]["total_pnl"] = pnl_sum[b]
            result[b]["avg_edge"]  = edge_sum[b] / edge_n[b] if edge_n[b] else None

    return result


def get_stats_by_close_horizon() -> dict:
    """
    Win rate grouped by actual days-to-close at the time the signal was logged.

    Computes (close_time - timestamp) in days for each resolved paper signal.
    Buckets:
      urgent   — closes within 1 day of signal
      short    — 1-7 days
      medium   — 7-30 days
      long     — 30+ days
      no_close — close_time not recorded

    Returns dict keyed by bucket name; each value: total, wins, win_rate, total_pnl, avg_edge.
    """
    BUCKETS = ("urgent", "short", "medium", "long", "no_close")
    result = {b: {"total": 0, "wins": 0, "win_rate": None,
                  "total_pnl": None, "avg_edge": None}
              for b in BUCKETS}
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT timestamp, close_time, result, pnl_if_traded, edge
                FROM signals
                WHERE ({_PAPER})
                  AND result IS NOT NULL AND result != ''
                """
            ).fetchall()
    except Exception:
        return result

    pnl_sum  = {b: 0.0 for b in BUCKETS}
    edge_sum = {b: 0.0 for b in BUCKETS}
    edge_n   = {b: 0   for b in BUCKETS}

    for r in rows:
        ts  = r["timestamp"]
        ct  = r["close_time"]
        if not ts or not ct:
            b = "no_close"
        else:
            try:
                from datetime import datetime as _dt, timezone as _tz
                t_sig  = _dt.fromisoformat(ts.replace("Z", "+00:00"))
                t_cls  = _dt.fromisoformat(ct.replace("Z", "+00:00"))
                days   = (t_cls - t_sig).total_seconds() / 86400
                if days < 1:
                    b = "urgent"
                elif days < 7:
                    b = "short"
                elif days < 30:
                    b = "medium"
                else:
                    b = "long"
            except Exception:
                b = "no_close"

        result[b]["total"] += 1
        if r["result"] == "WIN":
            result[b]["wins"] += 1
        if r["pnl_if_traded"] is not None:
            pnl_sum[b] += float(r["pnl_if_traded"])
        if r["edge"] is not None:
            edge_sum[b] += float(r["edge"])
            edge_n[b]   += 1

    for b in BUCKETS:
        n = result[b]["total"]
        if n:
            result[b]["win_rate"]  = result[b]["wins"] / n * 100
            result[b]["total_pnl"] = pnl_sum[b]
            result[b]["avg_edge"]  = edge_sum[b] / edge_n[b] if edge_n[b] else None

    return result


def get_stats_by_whale() -> dict:
    """
    Win rate for paper signals where whale activity was detected vs not.

    Returns dict with keys 'whale' and 'no_whale'; each has:
      total, wins, win_rate, total_pnl, avg_edge
    Only includes resolved paper signals with direction YES or NO.
    """
    result = {k: {"total": 0, "wins": 0, "win_rate": None,
                  "total_pnl": None, "avg_edge": None}
              for k in ("whale", "no_whale")}
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT whale_detected, result, pnl_if_traded, edge
                FROM signals
                WHERE ({_NO_PASS})
                  AND result IS NOT NULL AND result != ''
                  AND direction IN ('YES','NO')
                """
            ).fetchall()
    except Exception:
        return result

    pnl_sum  = {k: 0.0 for k in result}
    edge_sum = {k: 0.0 for k in result}
    edge_n   = {k: 0   for k in result}

    for r in rows:
        k = "whale" if r["whale_detected"] else "no_whale"
        result[k]["total"] += 1
        if r["result"] == "WIN":
            result[k]["wins"] += 1
        if r["pnl_if_traded"] is not None:
            pnl_sum[k] += float(r["pnl_if_traded"])
        if r["edge"] is not None:
            edge_sum[k] += float(r["edge"])
            edge_n[k]   += 1

    for k in result:
        n = result[k]["total"]
        if n:
            result[k]["win_rate"]  = result[k]["wins"] / n * 100
            result[k]["total_pnl"] = pnl_sum[k]
            result[k]["avg_edge"]  = edge_sum[k] / edge_n[k] if edge_n[k] else None

    return result


def get_stats_by_watchlist() -> dict:
    """
    Win rate for paper signals with smart money (watchlist) alignment vs without.

    Returns dict with keys 'watchlist' and 'no_watchlist'; each has:
      total, wins, win_rate, total_pnl, avg_edge
    Only includes resolved paper signals with direction YES or NO.
    """
    result = {k: {"total": 0, "wins": 0, "win_rate": None,
                  "total_pnl": None, "avg_edge": None}
              for k in ("watchlist", "no_watchlist")}
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT watchlist_signal, result, pnl_if_traded, edge
                FROM signals
                WHERE ({_NO_PASS})
                  AND result IS NOT NULL AND result != ''
                  AND direction IN ('YES','NO')
                """
            ).fetchall()
    except Exception:
        return result

    pnl_sum  = {k: 0.0 for k in result}
    edge_sum = {k: 0.0 for k in result}
    edge_n   = {k: 0   for k in result}

    for r in rows:
        k = "watchlist" if r["watchlist_signal"] else "no_watchlist"
        result[k]["total"] += 1
        if r["result"] == "WIN":
            result[k]["wins"] += 1
        if r["pnl_if_traded"] is not None:
            pnl_sum[k] += float(r["pnl_if_traded"])
        if r["edge"] is not None:
            edge_sum[k] += float(r["edge"])
            edge_n[k]   += 1

    for k in result:
        n = result[k]["total"]
        if n:
            result[k]["win_rate"]  = result[k]["wins"] / n * 100
            result[k]["total_pnl"] = pnl_sum[k]
            result[k]["avg_edge"]  = edge_sum[k] / edge_n[k] if edge_n[k] else None

    return result


def get_stats_by_confluence() -> dict:
    """
    backlog: confluence-detection. Win rate/Brier for paper signals grouped
    by confluence_count (how many independent sources -- Polymarket,
    consensus cross-market signal, smart-money watchlist -- agreed with
    Claude's own direction at scan time; see main.py's
    _count_agreeing_signals()).

    Buckets: "0" (no corroboration), "1" (one agreeing source), "2+" (the
    same >=2 threshold main.py's extremizing transform itself uses to
    decide the estimate is worth adjusting). Rows with confluence_count
    NULL (logged before this field existed) are excluded, not coerced to 0
    -- unlike ext_n_signals, confluence_count is recorded unconditionally
    going forward, so NULL genuinely means "not measured", not "zero".

    Returns dict with keys '0', '1', '2+'; each has:
      total, wins, win_rate, total_pnl, avg_edge, brier
    Only includes resolved paper signals with direction YES or NO.
    """
    result = {k: {"total": 0, "wins": 0, "win_rate": None,
                  "total_pnl": None, "avg_edge": None, "brier": None}
              for k in ("0", "1", "2+")}
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT confluence_count, our_estimate, direction, result,
                       pnl_if_traded, edge
                FROM signals
                WHERE ({_NO_PASS})
                  AND result IS NOT NULL AND result != ''
                  AND direction IN ('YES','NO')
                  AND confluence_count IS NOT NULL
                """
            ).fetchall()
    except Exception:
        return result

    pnl_sum   = {k: 0.0 for k in result}
    edge_sum  = {k: 0.0 for k in result}
    edge_n    = {k: 0   for k in result}
    brier_sum = {k: 0.0 for k in result}
    brier_n   = {k: 0   for k in result}

    for r in rows:
        n = r["confluence_count"]
        k = "0" if n == 0 else ("1" if n == 1 else "2+")
        result[k]["total"] += 1
        if r["result"] == "WIN":
            result[k]["wins"] += 1
        if r["pnl_if_traded"] is not None:
            pnl_sum[k] += float(r["pnl_if_traded"])
        if r["edge"] is not None:
            edge_sum[k] += float(r["edge"])
            edge_n[k]   += 1
        bc = brier_component(r["our_estimate"], r["direction"], r["result"])
        if bc is not None:
            brier_sum[k] += bc
            brier_n[k]   += 1

    for k in result:
        n = result[k]["total"]
        if n:
            result[k]["win_rate"]  = result[k]["wins"] / n * 100
            result[k]["total_pnl"] = pnl_sum[k]
            result[k]["avg_edge"]  = edge_sum[k] / edge_n[k] if edge_n[k] else None
            result[k]["brier"]     = brier_sum[k] / brier_n[k] if brier_n[k] else None

    return result


def get_pass_rate_by_flag_path() -> list[dict]:
    """
    For each flag_path bucket, shows the total number of signals and what fraction
    Claude PASSed vs acted on (YES/NO). Identifies which scanner categories generate
    the most false positives (high PASS rate).

    Includes all paper signals regardless of resolution status.
    Returns list of dicts sorted by pass_rate descending:
      flag_path, total, passed, acted, pass_rate (0-100), act_rate (0-100)
    """
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    COALESCE(flag_path, 'UNKNOWN') AS path,
                    COUNT(*) AS total,
                    SUM(CASE WHEN direction = 'PASS' THEN 1 ELSE 0 END) AS passed
                FROM signals
                WHERE ({_PAPER})
                  AND direction IS NOT NULL AND direction != ''
                GROUP BY flag_path
                ORDER BY (SUM(CASE WHEN direction = 'PASS' THEN 1 ELSE 0 END) * 1.0 / COUNT(*)) DESC
                """
            ).fetchall()
    except Exception:
        return []

    result = []
    for r in rows:
        total  = r["total"] or 0
        passed = r["passed"] or 0
        acted  = total - passed
        result.append({
            "flag_path": r["path"],
            "total":     total,
            "passed":    passed,
            "acted":     acted,
            "pass_rate": round(passed / total * 100, 1) if total else None,
            "act_rate":  round(acted / total * 100, 1)  if total else None,
        })
    return result


def get_stats_by_leviathan_score() -> dict:
    """
    Win rate grouped by stored Leviathan Score band (A/B/C/D).

    Bands:
      A — score >= 70
      B — score 55-69
      C — score 40-54
      D — score  < 40
      unscored — leviathan_score IS NULL (logged before this feature)

    Returns dict keyed by band; each value: total, wins, win_rate, total_pnl, avg_edge.
    """
    BANDS = ("A", "B", "C", "D", "unscored")
    result = {b: {"total": 0, "wins": 0, "win_rate": None,
                  "total_pnl": None, "avg_edge": None}
              for b in BANDS}
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT leviathan_score, result, pnl_if_traded, edge
                FROM signals
                WHERE ({_NO_PASS})
                  AND result IS NOT NULL AND result != ''
                """
            ).fetchall()
    except Exception:
        return result

    pnl_sum  = {b: 0.0 for b in BANDS}
    edge_sum = {b: 0.0 for b in BANDS}
    edge_n   = {b: 0   for b in BANDS}

    for r in rows:
        sc = r["leviathan_score"]
        if sc is None:
            b = "unscored"
        elif sc >= 70:
            b = "A"
        elif sc >= 55:
            b = "B"
        elif sc >= 40:
            b = "C"
        else:
            b = "D"

        result[b]["total"] += 1
        if r["result"] == "WIN":
            result[b]["wins"] += 1
        if r["pnl_if_traded"] is not None:
            pnl_sum[b] += float(r["pnl_if_traded"])
        if r["edge"] is not None:
            edge_sum[b] += float(r["edge"])
            edge_n[b]   += 1

    for b in BANDS:
        n = result[b]["total"]
        if n:
            result[b]["win_rate"]  = result[b]["wins"] / n * 100
            result[b]["total_pnl"] = pnl_sum[b]
            result[b]["avg_edge"]  = edge_sum[b] / edge_n[b] if edge_n[b] else None

    return result


def get_next_resolution_date() -> str | None:
    """Return the earliest close_time (as YYYY-MM-DD) among unresolved paper signals, or None."""
    try:
        with _db() as conn:
            row = conn.execute(
                f"""
                SELECT MIN(close_time) AS earliest
                FROM signals
                WHERE ({_PAPER})
                  AND (result IS NULL OR result = '')
                  AND close_time IS NOT NULL AND close_time != ''
                  AND direction IN ('YES', 'NO')
                """
            ).fetchone()
        val = row["earliest"] if row else None
        return val[:10] if val else None
    except Exception:
        return None


def get_upcoming_resolutions(days: int = 14) -> list[dict]:
    """
    Return unresolved paper signals closing within the next N days.
    direction must be YES or NO. Ordered by close_time ASC.
    """
    now    = datetime.now(timezone.utc)
    cutoff = (now + timedelta(days=days)).isoformat()
    now_s  = now.isoformat()
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT ticker, title, direction, confidence, market_price, close_time
                FROM signals
                WHERE ({_PAPER})
                  AND (result IS NULL OR result = '')
                  AND close_time IS NOT NULL AND close_time != ''
                  AND close_time >= ?
                  AND close_time <= ?
                  AND direction IN ('YES', 'NO')
                ORDER BY close_time ASC
                """,
                (now_s, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_stats_by_heuristic_label() -> list[dict]:
    """
    Win rate and P&L grouped by heuristic_label for resolved paper signals.

    Answers: "which heuristic categories have the best calibration and win rate?"
    Only includes paper signals that have a resolved outcome and a non-NULL heuristic_label.
    Returns list of dicts sorted by win_rate descending:
      heuristic_label, total, wins, losses, win_rate (float|None),
      total_pnl (float|None), avg_edge (float|None)
    """
    try:
        with _db() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    heuristic_label,
                    COUNT(*) AS total,
                    SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) AS losses,
                    AVG(edge) AS avg_edge,
                    SUM(pnl_if_traded) AS total_pnl
                FROM signals
                WHERE ({_PAPER})
                  AND outcome != '' AND outcome IS NOT NULL
                  AND heuristic_label IS NOT NULL
                  AND direction IN ('YES','NO')
                GROUP BY heuristic_label
                ORDER BY (SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) * 1.0 / COUNT(*)) DESC
                """
            ).fetchall()
    except Exception:
        return []

    result = []
    for r in rows:
        total = r["total"] or 0
        wins  = r["wins"]  or 0
        result.append({
            "heuristic_label": r["heuristic_label"],
            "total":           total,
            "wins":            wins,
            "losses":          r["losses"] or 0,
            "win_rate":        round(wins / total * 100, 1) if total else None,
            "avg_edge":        r["avg_edge"],
            "total_pnl":       r["total_pnl"],
        })
    return result
