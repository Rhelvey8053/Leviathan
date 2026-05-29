import csv
import os
import uuid
from datetime import datetime, timezone, timedelta


CALLS_CSV = os.path.join(os.path.dirname(__file__), "calls.csv")
RUNS_CSV = os.path.join(os.path.dirname(__file__), "runs.csv")

CALLS_FIELDS = [
    "call_id",
    "timestamp",
    "ticker",
    "title",
    "market_price",
    "our_estimate",
    "edge",
    "direction",
    "confidence",
    "whale_detected",
    "whale_direction",
    "outcome",
    "result",
    "pnl_if_traded",
    "run_id",
]

RUNS_FIELDS = [
    "run_id",
    "timestamp",
    "markets_scanned",
    "signals_generated",
    "whale_flags",
    "model_used",
    "tokens_used",
    "cost_usd",
    "runtime_ms",
]


def _ensure_csv(path: str, fields: list[str]) -> None:
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()


def log_signal(signal: dict) -> None:
    """Appends a signal row to calls.csv."""
    _ensure_csv(CALLS_CSV, CALLS_FIELDS)
    row = {
        "call_id": str(uuid.uuid4())[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": signal.get("ticker", ""),
        "title": signal.get("title", ""),
        "market_price": signal.get("market_price", ""),
        "our_estimate": signal.get("our_estimate", ""),
        "edge": signal.get("edge", ""),
        "direction": signal.get("direction", ""),
        "confidence": signal.get("confidence", ""),
        "whale_detected": signal.get("whale_detected", False),
        "whale_direction": signal.get("whale_direction", ""),
        "outcome": "",
        "result": "",
        "pnl_if_traded": "",
        "run_id": signal.get("run_id", ""),
    }
    try:
        with open(CALLS_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CALLS_FIELDS)
            writer.writerow(row)
    except PermissionError:
        print(f"  [logger] calls.csv is locked (close it in Excel) — signal not logged")


def log_run(run_data: dict) -> None:
    """Appends a run summary row to runs.csv."""
    _ensure_csv(RUNS_CSV, RUNS_FIELDS)
    row = {f: run_data.get(f, "") for f in RUNS_FIELDS}
    try:
        with open(RUNS_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=RUNS_FIELDS)
            writer.writerow(row)
    except PermissionError:
        print(f"  [logger] runs.csv is locked (close it in Excel) — run not logged")


def get_recent_tickers(days: int = 7) -> set[str]:
    """
    Returns the set of tickers that have already been signalled in the last N days.
    Used to detect duplicate signals across runs.
    """
    if not os.path.exists(CALLS_CSV):
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    tickers = set()
    try:
        with open(CALLS_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    ts = datetime.fromisoformat(row.get("timestamp", "").replace("Z", "+00:00"))
                    if ts >= cutoff:
                        tickers.add(row.get("ticker", ""))
                except Exception:
                    pass
    except Exception:
        pass
    return tickers


def get_week_signals(days: int = 7) -> list[dict]:
    """Returns all signal rows from the past N days, newest first."""
    if not os.path.exists(CALLS_CSV):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    try:
        with open(CALLS_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    ts = datetime.fromisoformat(row.get("timestamp", "").replace("Z", "+00:00"))
                    if ts >= cutoff:
                        rows.append({**row, "_ts": ts})
                except Exception:
                    pass
    except Exception:
        pass
    return sorted(rows, key=lambda r: r["_ts"], reverse=True)


def resolve_outcomes(config: dict) -> int:
    """
    Checks all unresolved calls (empty outcome) against the Kalshi API.
    Fills outcome (YES/NO), result (WIN/LOSS), and pnl_if_traded for settled markets.
    Returns count of newly resolved calls.
    """
    import kalshi as _kalshi

    if not os.path.exists(CALLS_CSV):
        return 0

    try:
        with open(CALLS_CSV, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return 0

    unresolved = [r for r in rows if not r.get("outcome")]
    if not unresolved:
        return 0

    resolved_count = 0
    for row in unresolved:
        ticker = row.get("ticker", "")
        if not ticker:
            continue
        try:
            market   = _kalshi.fetch_market(config, ticker)
            result   = (market.get("result") or "").lower()
            if result not in ("yes", "no"):
                continue  # still open

            outcome   = result.upper()
            direction = (row.get("direction") or "").upper()
            win       = (direction == outcome)

            try:
                pnl = round(float(row.get("edge") or 0) * (1 if win else -1), 4)
            except (ValueError, TypeError):
                pnl = ""

            row["outcome"]       = outcome
            row["result"]        = "WIN" if win else "LOSS"
            row["pnl_if_traded"] = pnl
            resolved_count += 1
        except Exception:
            pass

    if resolved_count > 0:
        try:
            with open(CALLS_CSV, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CALLS_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
        except PermissionError:
            print(f"  [logger] calls.csv is locked — could not save {resolved_count} resolved outcome(s)")
            return 0

    return resolved_count


def get_stats() -> dict:
    """
    Returns aggregate stats from calls.csv.
    win_rate only calculated on resolved markets (outcome != null).
    """
    if not os.path.exists(CALLS_CSV):
        return {
            "total_calls": 0,
            "resolved": 0,
            "win_rate": None,
            "avg_edge_captured": None,
            "total_hypothetical_pnl": None,
            "best_call": None,
            "worst_call": None,
        }

    rows = []
    with open(CALLS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total = len(rows)
    resolved = [r for r in rows if r.get("outcome") not in ("", None)]
    wins = [r for r in resolved if r.get("result") == "WIN"]

    win_rate = (len(wins) / len(resolved) * 100) if resolved else None

    edges = []
    for r in rows:
        try:
            edges.append(float(r["edge"]))
        except (ValueError, KeyError):
            pass
    avg_edge = (sum(edges) / len(edges)) if edges else None

    pnls = []
    for r in rows:
        try:
            pnls.append(float(r["pnl_if_traded"]))
        except (ValueError, KeyError):
            pass
    total_pnl = sum(pnls) if pnls else None

    best = max(rows, key=lambda r: float(r.get("edge") or 0), default=None)
    worst = min(rows, key=lambda r: float(r.get("edge") or 0), default=None)

    return {
        "total_calls": total,
        "resolved": len(resolved),
        "win_rate": win_rate,
        "avg_edge_captured": avg_edge,
        "total_hypothetical_pnl": total_pnl,
        "best_call": best,
        "worst_call": worst,
    }
