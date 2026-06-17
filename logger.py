"""
Persistent storage for Leviathan signals and run history.
Uses SQLite (leviathan.db) — no Excel locking, fast queries, no extra dependencies.
Auto-migrates calls.csv / runs.csv to the database on first import.
"""

import csv
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

DB_PATH   = os.path.join(os.path.dirname(__file__), "leviathan.db")
CALLS_CSV = os.path.join(os.path.dirname(__file__), "calls.csv")
RUNS_CSV  = os.path.join(os.path.dirname(__file__), "runs.csv")


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

def _add_col(conn, col_def: str) -> None:
    """Add a column to signals if it doesn't already exist (idempotent)."""
    col_name = col_def.split()[0]
    existing = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    if col_name not in existing:
        conn.execute(f"ALTER TABLE signals ADD COLUMN {col_def}")


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
            CREATE INDEX IF NOT EXISTS idx_signals_ts     ON signals(timestamp);
            CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
        """)
        # Additive schema migration — non-destructive, safe to run repeatedly.
        for col in [
            "contract_type         TEXT",
            "segment               TEXT",
            "entry_price           REAL",
            "resolution_date       TEXT",
            "logged_under          TEXT",
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
        ]:
            _add_col(conn, col)
        # Tag all pre-existing rows (source IS NULL) as paper signals.
        conn.execute("UPDATE signals SET source='paper' WHERE source IS NULL")
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
                 flag_path,watchlist_signal,sig_edge,sig_drift,sig_br_none)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            ))
    except Exception as e:
        print(f"  [logger] Failed to log signal: {e}")


def log_run(run_data: dict) -> None:
    try:
        with _db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO runs
                (run_id,timestamp,markets_scanned,signals_generated,
                 whale_flags,model_used,tokens_used,cost_usd,runtime_ms)
                VALUES (?,?,?,?,?,?,?,?,?)
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
            ))
    except Exception as e:
        print(f"  [logger] Failed to log run: {e}")


# ── Real fills ───────────────────────────────────────────────────────────────

def pull_real_fills(config: dict) -> dict:
    """
    Fetch all real Kalshi fills and insert them as source='real_fill' rows.
    Matches each fill against prior paper signals by ticker; sets from_signal,
    signal_call_id, and direction_aligned accordingly.
    Returns a summary dict: pulled, matched, aligned, contradictory.
    """
    import kalshi as _kalshi

    fills = _kalshi.fetch_fills(config)
    if not fills:
        return {"pulled": 0, "matched": 0, "aligned": 0, "contradictory": 0}

    try:
        with _db() as conn:
            sig_rows = conn.execute(
                "SELECT call_id, ticker, direction FROM signals "
                "WHERE source='paper' OR source IS NULL "
                "ORDER BY timestamp ASC"
            ).fetchall()
    except Exception:
        sig_rows = []

    # Most-recent paper signal per ticker (last in ASC order wins)
    ticker_signals: dict = {}
    for row in sig_rows:
        if row["ticker"]:
            ticker_signals[row["ticker"]] = dict(row)

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

        sig = ticker_signals.get(ticker)
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
                    str(uuid.uuid4())[:8],
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

def resolve_outcomes(config: dict) -> int:
    """
    Checks all unresolved calls against the Kalshi API and fills in outcomes.
    Returns count of newly resolved calls.
    """
    import kalshi as _kalshi
    import time as _time

    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT call_id, ticker, direction, market_price, "
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

            with _db() as conn:
                conn.execute(
                    "UPDATE signals SET outcome=?, result=?, pnl_if_traded=? WHERE call_id=?",
                    (outcome, "WIN" if win else "LOSS", pnl, row["call_id"])
                )
            resolved_count += 1
        except Exception as e:
            print(f"  [logger] resolve_outcomes: failed on {ticker}: {e}")

    return resolved_count


# ── Stats ─────────────────────────────────────────────────────────────────────

# Paper signals are source='paper' or NULL (pre-migration rows).
_PAPER = "source = 'paper' OR source IS NULL"


def get_stats() -> dict:
    """Stats for paper (simulated) signals only — never blends with real fills."""
    try:
        with _db() as conn:
            total     = conn.execute(f"SELECT COUNT(*) FROM signals WHERE {_PAPER}").fetchone()[0]
            resolved  = conn.execute(
                f"SELECT COUNT(*) FROM signals WHERE ({_PAPER}) AND outcome != '' AND outcome IS NOT NULL"
            ).fetchone()[0]
            wins      = conn.execute(
                f"SELECT COUNT(*) FROM signals WHERE ({_PAPER}) AND result = 'WIN'"
            ).fetchone()[0]
            avg_edge  = conn.execute(
                f"SELECT AVG(edge) FROM signals WHERE ({_PAPER}) AND edge IS NOT NULL"
            ).fetchone()[0]
            total_pnl = conn.execute(
                f"SELECT SUM(pnl_if_traded) FROM signals WHERE ({_PAPER}) AND pnl_if_traded IS NOT NULL"
            ).fetchone()[0]
            best  = conn.execute(
                f"SELECT * FROM signals WHERE ({_PAPER}) AND edge IS NOT NULL ORDER BY edge DESC LIMIT 1"
            ).fetchone()
            worst = conn.execute(
                f"SELECT * FROM signals WHERE ({_PAPER}) AND edge IS NOT NULL ORDER BY edge ASC LIMIT 1"
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
