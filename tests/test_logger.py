"""
Offline tests for logger.py.

All tests use a throwaway SQLite file (never leviathan.db).
Network and subprocess boundaries are mocked — no Kalshi calls, no email.
"""

import uuid
import pytest
from unittest.mock import patch, call
from datetime import datetime, timezone

import logger


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=False)
def tmp_db(tmp_path, monkeypatch):
    """Fresh throwaway DB for each test — never touches leviathan.db."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(logger, "DB_PATH", db_file)
    logger._init_db()
    return db_file


def _insert(call_id, ticker, direction, market_price,
            outcome="", result="", pnl=None, edge=0.10):
    """Insert a signal row directly into whatever DB logger.DB_PATH points at."""
    with logger._db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO signals
            (call_id, timestamp, ticker, title, market_price, our_estimate,
             edge, direction, confidence, whale_detected, whale_direction,
             outcome, result, pnl_if_traded, run_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id,
            datetime.now(timezone.utc).isoformat(),
            ticker, "Test Market",
            market_price, 0.40,
            edge,
            direction, "MED", 0, "",
            outcome, result, pnl,
            "run-test",
        ))


# ─── resolve_outcomes: payoff math ────────────────────────────────────────────

PAYOFF_CASES = [
    # direction  market_price  api_result  expected_pnl  description
    ("YES",      0.30,         "yes",      +0.70,  "YES at 0.30 resolves YES → +0.70"),
    ("YES",      0.30,         "no",       -0.30,  "YES at 0.30 resolves NO  → -0.30"),
    ("NO",       0.30,         "no",       +0.30,  "NO  at 0.30 resolves NO  → +0.30"),
    ("NO",       0.30,         "yes",      -0.70,  "NO  at 0.30 resolves YES → -0.70"),
    ("",         0.30,         "yes",       0.00,  "blank direction           → 0.00"),
]

@pytest.mark.parametrize("direction,mp,api_result,expected,desc", PAYOFF_CASES)
def test_payoff_math(tmp_db, direction, mp, api_result, expected, desc):
    cid = str(uuid.uuid4())[:8]
    _insert(cid, "TICKER", direction, mp)

    with patch("kalshi.fetch_market", return_value={"result": api_result}):
        logger.resolve_outcomes({})

    with logger._db() as conn:
        row = conn.execute(
            "SELECT pnl_if_traded FROM signals WHERE call_id=?", (cid,)
        ).fetchone()

    assert row is not None, "Row should still exist after resolution"
    assert round(row["pnl_if_traded"], 4) == round(expected, 4), desc


def test_resolve_reads_market_price_not_edge(tmp_db):
    """
    Payoff must use market_price, not edge.
    YES at market_price=0.30, edge=0.25 resolving YES → +0.70, NOT +0.75.
    """
    cid = str(uuid.uuid4())[:8]
    _insert(cid, "TICKER", "YES", 0.30, edge=0.25)

    with patch("kalshi.fetch_market", return_value={"result": "yes"}):
        logger.resolve_outcomes({})

    with logger._db() as conn:
        row = conn.execute(
            "SELECT pnl_if_traded FROM signals WHERE call_id=?", (cid,)
        ).fetchone()

    # Correct:  +(1 - 0.30) = +0.70
    # Wrong if edge used: +(1 - 0.25) = +0.75 or +0.25
    assert round(row["pnl_if_traded"], 4) == 0.70


# ─── resolve_outcomes: only touches unresolved rows ───────────────────────────

def test_resolve_skips_already_resolved(tmp_db):
    """Rows with a non-empty outcome must not be touched or re-fetched."""
    already = str(uuid.uuid4())[:8]
    pending = str(uuid.uuid4())[:8]

    _insert(already, "DONE",    "YES", 0.40, outcome="YES", result="WIN",  pnl=0.60)
    _insert(pending, "PENDING", "YES", 0.40)

    fetch_calls = []
    def fake_fetch(config, ticker):
        fetch_calls.append(ticker)
        return {"result": "no"}

    with patch("kalshi.fetch_market", side_effect=fake_fetch):
        logger.resolve_outcomes({})

    # Only the pending row should trigger an API call
    assert fetch_calls == ["PENDING"]

    # Already-resolved row must be unchanged
    with logger._db() as conn:
        row = conn.execute(
            "SELECT outcome, result, pnl_if_traded FROM signals WHERE call_id=?",
            (already,)
        ).fetchone()

    assert row["outcome"] == "YES"
    assert row["result"]  == "WIN"
    assert round(row["pnl_if_traded"], 4) == 0.60


def test_resolve_leaves_unresolved_when_market_not_settled(tmp_db):
    """If Kalshi returns no result yet, row stays unresolved."""
    cid = str(uuid.uuid4())[:8]
    _insert(cid, "OPEN", "YES", 0.40)

    # Market not yet settled — result field absent / empty
    with patch("kalshi.fetch_market", return_value={"result": ""}):
        count = logger.resolve_outcomes({})

    assert count == 0

    with logger._db() as conn:
        row = conn.execute(
            "SELECT outcome FROM signals WHERE call_id=?", (cid,)
        ).fetchone()
    assert row["outcome"] == ""


# ─── get_stats ────────────────────────────────────────────────────────────────

def test_get_stats_empty_db(tmp_db):
    stats = logger.get_stats()
    assert stats["total_calls"] == 0
    assert stats["resolved"]    == 0
    assert stats["win_rate"]    is None


def test_get_stats_zero_resolved_win_rate_is_null(tmp_db):
    """Unresolved signals must not produce a win_rate."""
    _insert(str(uuid.uuid4())[:8], "T1", "YES", 0.40)
    _insert(str(uuid.uuid4())[:8], "T2", "NO",  0.60)

    stats = logger.get_stats()
    assert stats["total_calls"] == 2
    assert stats["resolved"]    == 0
    assert stats["win_rate"]    is None


def test_get_stats_win_rate_computed_correctly(tmp_db):
    """2 wins, 1 loss → win_rate = 66.7%."""
    for i in range(2):
        _insert(str(uuid.uuid4())[:8], f"WIN-{i}", "YES", 0.30,
                outcome="YES", result="WIN", pnl=0.70)
    _insert(str(uuid.uuid4())[:8], "LOSS-1", "YES", 0.60,
            outcome="NO", result="LOSS", pnl=-0.60)

    stats = logger.get_stats()
    assert stats["total_calls"] == 3
    assert stats["resolved"]    == 3
    assert round(stats["win_rate"], 1) == round(2 / 3 * 100, 1)


def test_log_signal_writes_blank_outcome(tmp_db):
    """log_signal must store blank outcome/result so rows count as unresolved."""
    logger.log_signal({
        "ticker": "TEST", "title": "T", "market_price": 0.40,
        "our_estimate": 0.55, "edge": 0.15, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "run_id": "r1",
    })

    stats = logger.get_stats()
    assert stats["total_calls"] == 1
    assert stats["resolved"]    == 0   # blank outcome → not counted as resolved


def test_get_stats_resolved_counts_match_log_signal_blanks(tmp_db):
    """
    Regression: get_stats.resolved and resolve_outcomes query must agree on
    what 'unresolved' means — both treat outcome='' as unresolved.
    """
    # log_signal writes outcome=""
    logger.log_signal({
        "ticker": "X", "title": "T", "market_price": 0.30,
        "our_estimate": 0.50, "edge": 0.20, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "run_id": "r",
    })

    # Confirm get_stats sees 0 resolved
    before = logger.get_stats()
    assert before["resolved"] == 0

    # resolve_outcomes should find the row (outcome='')
    with patch("kalshi.fetch_market", return_value={"result": "yes"}):
        resolved_count = logger.resolve_outcomes({})

    assert resolved_count == 1

    # Now get_stats should see 1 resolved
    after = logger.get_stats()
    assert after["resolved"] == 1


# ─── Regression: per-$1 payoff convention ─────────────────────────────────────

@pytest.mark.parametrize("p", [0.10, 0.20, 0.30, 0.50, 0.70, 0.80, 0.90])
def test_payoff_convention_yes_win(p):
    """YES contract bought at p wins (1-p) per $1 notional when it resolves YES."""
    win    = True
    actual = round((1.0 - p) if win else -p, 4)
    assert actual == round(1.0 - p, 4), (
        f"YES win at {p}: expected {1-p:.4f}, got {actual:.4f}. "
        "Changing this breaks binary contract accounting."
    )


@pytest.mark.parametrize("p", [0.10, 0.20, 0.30, 0.50, 0.70, 0.80, 0.90])
def test_payoff_convention_yes_loss(p):
    """YES contract bought at p loses p per $1 notional when it resolves NO."""
    win    = False
    actual = round((1.0 - p) if win else -p, 4)
    assert actual == round(-p, 4), (
        f"YES loss at {p}: expected {-p:.4f}, got {actual:.4f}."
    )


@pytest.mark.parametrize("p", [0.10, 0.20, 0.30, 0.50, 0.70, 0.80, 0.90])
def test_payoff_convention_no_win(p):
    """
    NO contract on a market priced at p (YES) wins p per $1 notional
    when the market resolves NO. Stored market_price is always the YES price.
    """
    win    = True
    actual = round(p if win else -(1.0 - p), 4)
    assert actual == round(p, 4), (
        f"NO win at YES-price {p}: expected {p:.4f}, got {actual:.4f}."
    )


@pytest.mark.parametrize("p", [0.10, 0.20, 0.30, 0.50, 0.70, 0.80, 0.90])
def test_payoff_convention_no_loss(p):
    """NO contract loses (1-p) per $1 notional when market resolves YES."""
    win    = False
    actual = round(p if win else -(1.0 - p), 4)
    assert actual == round(-(1.0 - p), 4), (
        f"NO loss at YES-price {p}: expected {-(1-p):.4f}, got {actual:.4f}."
    )


# ─── Real fills: schema migration ─────────────────────────────────────────────

def test_schema_new_columns_present(tmp_db):
    """After _init_db the signals table must have all new columns."""
    with logger._db() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    for col in ("source", "from_signal", "signal_call_id", "direction_aligned",
                "entry_price", "fill_count", "fill_fee",
                "contract_type", "segment", "resolution_date", "logged_under",
                "flag_path", "watchlist_signal"):
        assert col in cols, f"Missing column: {col}"


def test_schema_migration_idempotent(tmp_db):
    """Calling _init_db twice must not error or duplicate columns."""
    logger._init_db()
    with logger._db() as conn:
        col_names = [row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()]
    assert col_names.count("source") == 1


def test_schema_migration_non_destructive(tmp_db):
    """Existing rows survive migration and get source='paper'."""
    _insert("pre-exist", "TKR", "YES", 0.30)
    logger._init_db()   # re-run migration
    with logger._db() as conn:
        row = conn.execute(
            "SELECT source FROM signals WHERE call_id='pre-exist'"
        ).fetchone()
    assert row is not None
    assert row["source"] == "paper"


def test_existing_signals_tagged_paper(tmp_db):
    """Rows inserted before source column existed must be tagged source='paper' after migration."""
    # Write a row bypassing the source column (simulates pre-migration data)
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals (call_id, timestamp, ticker, direction, market_price)
            VALUES ('legacy-1', '2026-01-01T00:00:00Z', 'OLD', 'YES', 0.40)
        """)
    # Re-run migration — should set source='paper' for the NULL row
    logger._init_db()
    with logger._db() as conn:
        row = conn.execute("SELECT source FROM signals WHERE call_id='legacy-1'").fetchone()
    assert row["source"] == "paper"


# ─── Real fills: pull_real_fills ──────────────────────────────────────────────

def _mock_fill(ticker, side="YES", price=0.30, fee=0.01, count=5, action="BUY"):
    return {
        "ticker":            ticker,
        "side":              side.lower(),
        "action":            action.lower(),
        "yes_price_dollars": str(price) if side.upper() == "YES" else None,
        "no_price_dollars":  str(price) if side.upper() == "NO"  else None,
        "fee_cost":          str(fee),
        "count":             count,
        "created_time":      "2026-06-15T10:00:00Z",
    }


def test_fill_matching_signal_ticker_sets_from_signal(tmp_db):
    """Fill on a ticker that has a prior paper signal → from_signal=1."""
    _insert("sig-abc", "KXIPO-TEST", "YES", 0.30)

    with patch("kalshi.fetch_fills", return_value=[_mock_fill("KXIPO-TEST", side="YES")]):
        summary = logger.pull_real_fills({})

    assert summary["pulled"]  == 1
    assert summary["matched"] == 1
    assert summary["aligned"] == 1   # signal=YES, fill side=YES

    with logger._db() as conn:
        row = conn.execute(
            "SELECT from_signal, signal_call_id, direction_aligned, source "
            "FROM signals WHERE source='real_fill'"
        ).fetchone()
    assert row["from_signal"]      == 1
    assert row["signal_call_id"]   == "sig-abc"
    assert row["direction_aligned"] == 1
    assert row["source"]           == "real_fill"


def test_fill_direction_contradictory(tmp_db):
    """Fill side opposes signal direction → direction_aligned=0."""
    _insert("sig-xyz", "KXIPO-TEST", "YES", 0.30)

    with patch("kalshi.fetch_fills", return_value=[_mock_fill("KXIPO-TEST", side="NO")]):
        summary = logger.pull_real_fills({})

    assert summary["contradictory"] == 1

    with logger._db() as conn:
        row = conn.execute(
            "SELECT direction_aligned FROM signals WHERE source='real_fill'"
        ).fetchone()
    assert row["direction_aligned"] == 0


def test_fill_unrelated_ticker_no_match(tmp_db):
    """Fill on a ticker with no prior signal → from_signal=0."""
    _insert("sig-def", "KNOWN-TICKER", "YES", 0.30)

    with patch("kalshi.fetch_fills", return_value=[_mock_fill("UNKNOWN-TICKER")]):
        summary = logger.pull_real_fills({})

    assert summary["matched"] == 0

    with logger._db() as conn:
        row = conn.execute(
            "SELECT from_signal, signal_call_id FROM signals WHERE source='real_fill'"
        ).fetchone()
    assert row["from_signal"]    == 0
    assert row["signal_call_id"] is None


def test_pull_empty_fills_returns_zero_summary(tmp_db):
    """Empty fills response → summary all zeros, no crash."""
    with patch("kalshi.fetch_fills", return_value=[]):
        summary = logger.pull_real_fills({})
    assert summary == {"pulled": 0, "matched": 0, "aligned": 0, "contradictory": 0}


# ─── Real fills: resolve_outcomes P&L net of fees ─────────────────────────────

def _insert_real_fill(call_id, ticker, direction, entry_price,
                      fill_count=10, fill_fee=0.10, outcome=""):
    """Insert a real_fill row directly for testing resolution."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, direction, market_price, entry_price,
             fill_count, fill_fee, source, outcome, result)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id, "2026-06-15T10:00:00Z", ticker, direction,
            entry_price, entry_price, fill_count, fill_fee,
            "real_fill", outcome, "",
        ))


def test_real_fill_pnl_net_of_fees_yes_win(tmp_db):
    """YES fill: win → gross (1-p) minus fee per unit."""
    _insert_real_fill("fill-1", "KXTEST", "YES", 0.30, fill_count=10, fill_fee=0.10)
    # fee_per_unit = 0.10 / 10 = 0.01; gross = 1-0.30 = 0.70; net = 0.69

    with patch("kalshi.fetch_market", return_value={"result": "yes"}):
        count = logger.resolve_outcomes({})

    assert count == 1
    with logger._db() as conn:
        row = conn.execute("SELECT pnl_if_traded FROM signals WHERE call_id='fill-1'").fetchone()
    assert round(row["pnl_if_traded"], 4) == round(0.70 - 0.01, 4)


def test_real_fill_pnl_net_of_fees_yes_loss(tmp_db):
    """YES fill: loss → gross -p minus fee per unit."""
    _insert_real_fill("fill-2", "KXTEST", "YES", 0.30, fill_count=10, fill_fee=0.10)
    # fee_per_unit = 0.01; gross = -0.30; net = -0.31

    with patch("kalshi.fetch_market", return_value={"result": "no"}):
        logger.resolve_outcomes({})

    with logger._db() as conn:
        row = conn.execute("SELECT pnl_if_traded FROM signals WHERE call_id='fill-2'").fetchone()
    assert round(row["pnl_if_traded"], 4) == round(-0.30 - 0.01, 4)


def test_unresolved_real_fill_stays_unresolved(tmp_db):
    """Open market → real fill stays unresolved, no error."""
    _insert_real_fill("fill-open", "KXJULY", "YES", 0.40)

    with patch("kalshi.fetch_market", return_value={"result": ""}):
        count = logger.resolve_outcomes({})

    assert count == 0
    with logger._db() as conn:
        row = conn.execute("SELECT outcome FROM signals WHERE call_id='fill-open'").fetchone()
    assert row["outcome"] == ""


# ─── Stats separation ─────────────────────────────────────────────────────────

def test_get_stats_excludes_real_fills(tmp_db):
    """get_stats must count only paper signals — real fills must never appear."""
    _insert("paper-1", "PAPER", "YES", 0.30, outcome="YES", result="WIN", pnl=0.70)
    _insert_real_fill("fill-r", "REAL", "YES", 0.50)  # unresolved real fill

    # Resolve the real fill so it has a pnl too
    with patch("kalshi.fetch_market", return_value={"result": "yes"}):
        logger.resolve_outcomes({})

    stats = logger.get_stats()
    assert stats["total_calls"] == 1     # only paper-1
    assert stats["resolved"]    == 1


def test_get_stats_real_counts_only_fills(tmp_db):
    """get_stats_real must count only real_fill rows."""
    _insert("paper-2", "PAPER", "YES", 0.30, outcome="YES", result="WIN", pnl=0.70)
    _insert_real_fill("fill-s", "REAL", "NO", 0.60)

    with patch("kalshi.fetch_market", return_value={"result": "no"}):
        logger.resolve_outcomes({})

    stats_r = logger.get_stats_real()
    assert stats_r["total_fills"] == 1
    assert stats_r["resolved"]    == 1


# ─── flag_path persistence ────────────────────────────────────────────────────

def test_log_signal_stores_flag_path(tmp_db):
    """log_signal must persist flag_path into the signals table."""
    logger.log_signal({
        "ticker": "KXTEST-28", "title": "Test", "market_price": 0.30,
        "our_estimate": 0.50, "edge": 0.20, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "flag_path": "HEURISTIC", "watchlist_signal": False,
        "run_id": "r1",
    })
    with logger._db() as conn:
        row = conn.execute("SELECT flag_path, watchlist_signal FROM signals WHERE ticker='KXTEST-28'").fetchone()
    assert row["flag_path"]       == "HEURISTIC"
    assert row["watchlist_signal"] == 0


def test_log_signal_stores_watchlist_flag(tmp_db):
    """log_signal must persist watchlist_signal=1 when True."""
    logger.log_signal({
        "ticker": "KXWL-28", "title": "WL Market", "market_price": 0.40,
        "our_estimate": 0.60, "edge": 0.20, "direction": "YES",
        "confidence": "HIGH", "whale_detected": False, "whale_direction": "",
        "flag_path": "WATCHLIST", "watchlist_signal": True,
        "run_id": "r2",
    })
    with logger._db() as conn:
        row = conn.execute("SELECT flag_path, watchlist_signal FROM signals WHERE ticker='KXWL-28'").fetchone()
    assert row["flag_path"]       == "WATCHLIST"
    assert row["watchlist_signal"] == 1


def test_log_signal_flag_path_none(tmp_db):
    """log_signal must accept flag_path=None (no flag path set)."""
    logger.log_signal({
        "ticker": "KXNO-28", "title": "No Path", "market_price": 0.25,
        "our_estimate": 0.40, "edge": 0.15, "direction": "YES",
        "confidence": "LOW", "whale_detected": False, "whale_direction": "",
        "flag_path": None, "watchlist_signal": False,
        "run_id": "r3",
    })
    with logger._db() as conn:
        row = conn.execute("SELECT flag_path FROM signals WHERE ticker='KXNO-28'").fetchone()
    assert row["flag_path"] is None


def _insert_with_flag_path(call_id, ticker, direction, market_price,
                            flag_path, outcome="", result="", pnl=None):
    """Insert a resolved signal row with flag_path for stats tests."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, title, market_price, our_estimate,
             edge, direction, confidence, whale_detected, whale_direction,
             outcome, result, pnl_if_traded, run_id, source, flag_path)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id,
            "2026-06-17T10:00:00Z",
            ticker, "Test", market_price, 0.50, 0.10,
            direction, "MED", 0, "",
            outcome, result, pnl,
            "run-test", "paper", flag_path,
        ))


def test_get_stats_by_flag_path_basic(tmp_db):
    """get_stats_by_flag_path returns one row per flag_path with win counts."""
    _insert_with_flag_path("fp1", "T1", "YES", 0.30, "EDGE",      "YES", "WIN",  0.70)
    _insert_with_flag_path("fp2", "T2", "YES", 0.30, "EDGE",      "NO",  "LOSS", -0.30)
    _insert_with_flag_path("fp3", "T3", "YES", 0.30, "HEURISTIC", "YES", "WIN",  0.70)
    _insert_with_flag_path("fp4", "T4", "YES", 0.30, "WATCHLIST", "YES", "WIN",  0.70)

    rows = logger.get_stats_by_flag_path()
    paths = {r["flag_path"] for r in rows}
    assert "EDGE"      in paths
    assert "HEURISTIC" in paths
    assert "WATCHLIST" in paths

    edge_row = next(r for r in rows if r["flag_path"] == "EDGE")
    assert edge_row["total"] == 2
    assert edge_row["wins"]  == 1
    assert edge_row["win_rate"] == pytest.approx(50.0)


def test_get_stats_by_flag_path_only_resolved(tmp_db):
    """get_stats_by_flag_path ignores unresolved rows."""
    _insert_with_flag_path("fp5", "T5", "YES", 0.30, "EDGE", outcome="", result="")
    rows = logger.get_stats_by_flag_path()
    # No resolved rows → empty list
    assert rows == []


def test_get_stats_by_flag_path_excludes_real_fills(tmp_db):
    """get_stats_by_flag_path must not include real_fill rows."""
    _insert_with_flag_path("fp6", "T6", "YES", 0.30, "EDGE", "YES", "WIN", 0.70)
    # Insert a real_fill with a flag_path directly
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, direction, market_price,
             outcome, result, pnl_if_traded, source, flag_path)
            VALUES ('rf1','2026-06-17T10:00:00Z','T7','YES',0.30,
                    'YES','WIN',0.70,'real_fill','EDGE')
        """)
    rows = logger.get_stats_by_flag_path()
    edge_row = next(r for r in rows if r["flag_path"] == "EDGE")
    assert edge_row["total"] == 1  # only the paper signal, not the real_fill
