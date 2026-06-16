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
                 outcome,result,pnl_if_traded,run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                "SELECT call_id, ticker, direction, market_price FROM signals "
                "WHERE outcome IS NULL OR outcome = ''"
            ).fetchall()
    except Exception:
        return 0

    if not rows:
        return 0

    resolved_count = 0
    for i, row in enumerate(rows):
        if i > 0:
            _time.sleep(0.25)  # 4 req/s — stay well under Kalshi rate limits
        ticker = row["ticker"]
        if not ticker:
            continue
        try:
            market = _kalshi.fetch_market(config, ticker)
            result = (market.get("result") or "").lower()
            if result not in ("yes", "no"):
                continue

            outcome      = result.upper()
            direction    = (row["direction"] or "").upper()
            win          = direction == outcome
            market_price = float(row["market_price"] or 0)

            # Correct binary contract payoff per $1 notional:
            # YES bought at p: win → +(1-p), lose → -p
            # NO  bought at (1-p): win → +p,   lose → -(1-p)
            if direction == "YES":
                pnl = round((1.0 - market_price) if win else -market_price, 4)
            elif direction == "NO":
                pnl = round(market_price if win else -(1.0 - market_price), 4)
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

def get_stats() -> dict:
    try:
        with _db() as conn:
            total     = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            resolved  = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE outcome != '' AND outcome IS NOT NULL"
            ).fetchone()[0]
            wins      = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE result = 'WIN'"
            ).fetchone()[0]
            avg_edge  = conn.execute(
                "SELECT AVG(edge) FROM signals WHERE edge IS NOT NULL"
            ).fetchone()[0]
            total_pnl = conn.execute(
                "SELECT SUM(pnl_if_traded) FROM signals WHERE pnl_if_traded IS NOT NULL"
            ).fetchone()[0]
            best  = conn.execute(
                "SELECT * FROM signals WHERE edge IS NOT NULL ORDER BY edge DESC LIMIT 1"
            ).fetchone()
            worst = conn.execute(
                "SELECT * FROM signals WHERE edge IS NOT NULL ORDER BY edge ASC LIMIT 1"
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
