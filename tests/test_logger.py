"""
Offline tests for logger.py.

All tests use a throwaway SQLite file (never leviathan.db).
Network and subprocess boundaries are mocked — no Kalshi calls, no email.
"""

import uuid
import pytest
from unittest.mock import patch, call
from datetime import datetime, timezone

from core import logger


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

    with patch("core.kalshi.fetch_market", return_value={"result": api_result}):
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

    with patch("core.kalshi.fetch_market", return_value={"result": "yes"}):
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

    with patch("core.kalshi.fetch_market", side_effect=fake_fetch):
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
    with patch("core.kalshi.fetch_market", return_value={"result": ""}):
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
    with patch("core.kalshi.fetch_market", return_value={"result": "yes"}):
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
                "flag_path", "watchlist_signal",
                "sig_edge", "sig_drift", "sig_br_none",
                "base_rate", "net_edge", "heuristic_direction", "short_horizon", "time_horizon",
                "close_time", "heuristic_label"):
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

    with patch("core.kalshi.fetch_fills", return_value=[_mock_fill("KXIPO-TEST", side="YES")]):
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

    with patch("core.kalshi.fetch_fills", return_value=[_mock_fill("KXIPO-TEST", side="NO")]):
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

    with patch("core.kalshi.fetch_fills", return_value=[_mock_fill("UNKNOWN-TICKER")]):
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
    with patch("core.kalshi.fetch_fills", return_value=[]):
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

    with patch("core.kalshi.fetch_market", return_value={"result": "yes"}):
        count = logger.resolve_outcomes({})

    assert count == 1
    with logger._db() as conn:
        row = conn.execute("SELECT pnl_if_traded FROM signals WHERE call_id='fill-1'").fetchone()
    assert round(row["pnl_if_traded"], 4) == round(0.70 - 0.01, 4)


def test_real_fill_pnl_net_of_fees_yes_loss(tmp_db):
    """YES fill: loss → gross -p minus fee per unit."""
    _insert_real_fill("fill-2", "KXTEST", "YES", 0.30, fill_count=10, fill_fee=0.10)
    # fee_per_unit = 0.01; gross = -0.30; net = -0.31

    with patch("core.kalshi.fetch_market", return_value={"result": "no"}):
        logger.resolve_outcomes({})

    with logger._db() as conn:
        row = conn.execute("SELECT pnl_if_traded FROM signals WHERE call_id='fill-2'").fetchone()
    assert round(row["pnl_if_traded"], 4) == round(-0.30 - 0.01, 4)


def test_unresolved_real_fill_stays_unresolved(tmp_db):
    """Open market → real fill stays unresolved, no error."""
    _insert_real_fill("fill-open", "KXJULY", "YES", 0.40)

    with patch("core.kalshi.fetch_market", return_value={"result": ""}):
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
    with patch("core.kalshi.fetch_market", return_value={"result": "yes"}):
        logger.resolve_outcomes({})

    stats = logger.get_stats()
    assert stats["total_calls"] == 1     # only paper-1
    assert stats["resolved"]    == 1


def test_get_stats_real_counts_only_fills(tmp_db):
    """get_stats_real must count only real_fill rows."""
    _insert("paper-2", "PAPER", "YES", 0.30, outcome="YES", result="WIN", pnl=0.70)
    _insert_real_fill("fill-s", "REAL", "NO", 0.60)

    with patch("core.kalshi.fetch_market", return_value={"result": "no"}):
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


def test_log_signal_stores_sig_fields(tmp_db):
    """log_signal must persist sig_edge, sig_drift, sig_br_none."""
    logger.log_signal({
        "ticker": "KXSIG-28", "title": "Sig Test", "market_price": 0.30,
        "our_estimate": 0.50, "edge": 0.20, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "flag_path": "EDGE", "watchlist_signal": False,
        "sig_edge": True, "sig_drift": False, "sig_br_none": False,
        "run_id": "r4",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT sig_edge, sig_drift, sig_br_none FROM signals WHERE ticker='KXSIG-28'"
        ).fetchone()
    assert row["sig_edge"]    == 1
    assert row["sig_drift"]   == 0
    assert row["sig_br_none"] == 0


def test_log_signal_sig_drift_stored(tmp_db):
    """log_signal persists sig_drift=True as 1."""
    logger.log_signal({
        "ticker": "KXDRIFT-28", "title": "Drift Test", "market_price": 0.45,
        "our_estimate": 0.55, "edge": 0.10, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "flag_path": "DRIFT", "watchlist_signal": False,
        "sig_edge": False, "sig_drift": True, "sig_br_none": False,
        "run_id": "r5",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT sig_edge, sig_drift FROM signals WHERE ticker='KXDRIFT-28'"
        ).fetchone()
    assert row["sig_drift"] == 1
    assert row["sig_edge"]  == 0


def test_log_signal_stores_base_rate(tmp_db):
    """log_signal must persist base_rate into the signals table."""
    logger.log_signal({
        "ticker": "KXBR-01", "title": "BR Test", "market_price": 0.30,
        "our_estimate": 0.50, "edge": 0.20, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "flag_path": "HEURISTIC", "watchlist_signal": False,
        "sig_edge": False, "sig_drift": False, "sig_br_none": False,
        "base_rate": 0.55, "heuristic_direction": "YES",
        "run_id": "r6",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT base_rate, heuristic_direction FROM signals WHERE ticker='KXBR-01'"
        ).fetchone()
    assert row["base_rate"]            == pytest.approx(0.55)
    assert row["heuristic_direction"]  == "YES"


def test_log_signal_stores_heuristic_direction_no(tmp_db):
    """log_signal persists heuristic_direction='NO' correctly."""
    logger.log_signal({
        "ticker": "KXHD-NO", "title": "HD NO", "market_price": 0.70,
        "our_estimate": 0.50, "edge": 0.20, "direction": "NO",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "flag_path": "HEURISTIC", "watchlist_signal": False,
        "sig_edge": False, "sig_drift": False, "sig_br_none": False,
        "base_rate": 0.35, "heuristic_direction": "NO",
        "run_id": "r7",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT base_rate, heuristic_direction FROM signals WHERE ticker='KXHD-NO'"
        ).fetchone()
    assert row["base_rate"]           == pytest.approx(0.35)
    assert row["heuristic_direction"] == "NO"


def test_log_signal_base_rate_none_when_absent(tmp_db):
    """log_signal stores NULL for base_rate and heuristic_direction when not provided."""
    logger.log_signal({
        "ticker": "KXNO-BR", "title": "No BR", "market_price": 0.40,
        "our_estimate": 0.50, "edge": 0.10, "direction": "YES",
        "confidence": "LOW", "whale_detected": False, "whale_direction": "",
        "flag_path": "DRIFT", "watchlist_signal": False,
        "run_id": "r8",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT base_rate, heuristic_direction FROM signals WHERE ticker='KXNO-BR'"
        ).fetchone()
    assert row["base_rate"]           is None
    assert row["heuristic_direction"] is None


def test_log_signal_stores_short_horizon_true(tmp_db):
    """log_signal persists short_horizon=True as 1."""
    logger.log_signal({
        "ticker": "KXSH-1", "title": "Short Horizon Test", "market_price": 0.40,
        "our_estimate": 0.60, "edge": 0.20, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "flag_path": "HEURISTIC", "watchlist_signal": False,
        "short_horizon": True, "run_id": "r9",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT short_horizon FROM signals WHERE ticker='KXSH-1'"
        ).fetchone()
    assert row["short_horizon"] == 1


def test_log_signal_stores_short_horizon_false_default(tmp_db):
    """log_signal stores 0 for short_horizon when not provided."""
    logger.log_signal({
        "ticker": "KXSH-0", "title": "Long Horizon", "market_price": 0.30,
        "our_estimate": 0.50, "edge": 0.20, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "flag_path": "HEURISTIC", "watchlist_signal": False,
        "run_id": "r10",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT short_horizon FROM signals WHERE ticker='KXSH-0'"
        ).fetchone()
    assert row["short_horizon"] == 0


def test_log_signal_stores_time_horizon(tmp_db):
    """log_signal persists time_horizon into the signals table."""
    logger.log_signal({
        "ticker": "KXTH-1", "title": "Monthly Market", "market_price": 0.40,
        "our_estimate": 0.55, "edge": 0.15, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "flag_path": "HEURISTIC", "watchlist_signal": False,
        "time_horizon": "MONTHLY", "run_id": "r11",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT time_horizon FROM signals WHERE ticker='KXTH-1'"
        ).fetchone()
    assert row["time_horizon"] == "MONTHLY"


def test_log_signal_stores_net_edge(tmp_db):
    """log_signal persists net_edge into the signals table."""
    logger.log_signal({
        "ticker": "KXNE-1", "title": "Net Edge Test", "market_price": 0.30,
        "our_estimate": 0.45, "edge": 0.15, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "flag_path": "HEURISTIC", "watchlist_signal": False,
        "base_rate": 0.45, "net_edge": 0.10, "run_id": "r12",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT net_edge FROM signals WHERE ticker='KXNE-1'"
        ).fetchone()
    assert abs(row["net_edge"] - 0.10) < 1e-9


def test_log_signal_net_edge_none_when_absent(tmp_db):
    """net_edge stored as NULL when not provided."""
    logger.log_signal({
        "ticker": "KXNE-2", "title": "No Net Edge", "market_price": 0.30,
        "our_estimate": 0.45, "edge": 0.15, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "flag_path": "HEURISTIC", "watchlist_signal": False,
        "run_id": "r13",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT net_edge FROM signals WHERE ticker='KXNE-2'"
        ).fetchone()
    assert row["net_edge"] is None


# ─── get_stats_by_net_edge ────────────────────────────────────────────────────

def _insert_net_edge(call_id, net_edge, result_val, pnl, edge=0.12):
    """Insert a resolved paper signal with net_edge for stats tests."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, direction, market_price, our_estimate,
             edge, net_edge, result, outcome, pnl_if_traded, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id, "2026-01-01T00:00:00+00:00",
            f"TKR-{call_id}", "YES", 0.40, 0.55,
            edge, net_edge,
            result_val, "YES_WIN" if result_val == "WIN" else "YES_LOSS",
            pnl, "paper",
        ))


def test_get_stats_by_net_edge_empty_db(tmp_db):
    """Empty DB returns all zeroes — no errors."""
    result = logger.get_stats_by_net_edge()
    for bucket in ("spread_dominant", "thin", "good", "strong", "no_data"):
        assert result[bucket]["total"] == 0
        assert result[bucket]["win_rate"] is None


def test_get_stats_by_net_edge_buckets(tmp_db):
    """Each net_edge value lands in the correct bucket."""
    _insert_net_edge("ne1", -0.02, "WIN",  0.6)   # spread_dominant
    _insert_net_edge("ne2",  0.03, "LOSS", -0.4)  # thin
    _insert_net_edge("ne3",  0.07, "WIN",  0.6)   # good
    _insert_net_edge("ne4",  0.15, "WIN",  0.6)   # strong
    _insert_net_edge("ne5",  None, "LOSS", -0.4)  # no_data

    result = logger.get_stats_by_net_edge()
    assert result["spread_dominant"]["total"] == 1
    assert result["spread_dominant"]["wins"]  == 1
    assert result["thin"]["total"]   == 1
    assert result["thin"]["losses"]  == 1
    assert result["good"]["total"]   == 1
    assert result["strong"]["total"] == 1
    assert result["no_data"]["total"] == 1


def test_get_stats_by_net_edge_boundary_zero(tmp_db):
    """net_edge=0 goes to spread_dominant (not thin)."""
    _insert_net_edge("ne0", 0.0, "WIN", 0.6)
    result = logger.get_stats_by_net_edge()
    assert result["spread_dominant"]["total"] == 1
    assert result["thin"]["total"] == 0


def test_get_stats_by_net_edge_excludes_real_fills(tmp_db):
    """Real fills are excluded from net_edge calibration."""
    _insert_net_edge("ne-real", 0.12, "WIN", 0.6)
    with logger._db() as conn:
        conn.execute("UPDATE signals SET source='real_fill' WHERE call_id='ne-real'")
    result = logger.get_stats_by_net_edge()
    assert result["strong"]["total"] == 0


# ─── close_time logging ───────────────────────────────────────────────────────

def test_log_signal_stores_close_time(tmp_db):
    """close_time is stored when provided in signal dict."""
    logger.log_signal({
        "ticker": "KXCLOSE1", "title": "T", "market_price": 0.50,
        "our_estimate": 0.65, "edge": 0.15, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "run_id": "rct1",
        "close_time": "2026-07-01T00:00:00+00:00",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT close_time FROM signals WHERE ticker='KXCLOSE1'"
        ).fetchone()
    assert row is not None
    assert row["close_time"] == "2026-07-01T00:00:00+00:00"


def test_log_signal_close_time_none_when_absent(tmp_db):
    """close_time is NULL when not provided."""
    logger.log_signal({
        "ticker": "KXCLOSE2", "title": "T", "market_price": 0.50,
        "our_estimate": 0.65, "edge": 0.15, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "run_id": "rct2",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT close_time FROM signals WHERE ticker='KXCLOSE2'"
        ).fetchone()
    assert row is not None
    assert row["close_time"] is None


# ─── get_stats_by_close_horizon ───────────────────────────────────────────────

def _insert_close_horizon(call_id, ts_iso, close_iso, result_val, pnl=0.5, edge=0.12):
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, direction, market_price, our_estimate,
             edge, result, outcome, pnl_if_traded, source, close_time)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (call_id, ts_iso, f"KX{call_id}", "YES", 0.40, 0.60,
              edge, result_val, "YES" if result_val == "WIN" else "NO",
              pnl, "paper", close_iso))


def test_get_stats_by_close_horizon_empty(tmp_db):
    result = logger.get_stats_by_close_horizon()
    for b in ("urgent", "short", "medium", "long", "no_close"):
        assert result[b]["total"] == 0
        assert result[b]["win_rate"] is None


def test_get_stats_by_close_horizon_buckets(tmp_db):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    sig_ts = now.isoformat()

    # urgent: closes in 6 hours
    _insert_close_horizon("ch-urgent", sig_ts, (now + timedelta(hours=6)).isoformat(), "WIN")
    # short: closes in 3 days
    _insert_close_horizon("ch-short", sig_ts, (now + timedelta(days=3)).isoformat(), "WIN")
    # medium: closes in 14 days
    _insert_close_horizon("ch-medium", sig_ts, (now + timedelta(days=14)).isoformat(), "LOSS")
    # long: closes in 60 days
    _insert_close_horizon("ch-long", sig_ts, (now + timedelta(days=60)).isoformat(), "WIN")

    result = logger.get_stats_by_close_horizon()
    assert result["urgent"]["total"] == 1
    assert result["urgent"]["wins"] == 1
    assert result["short"]["total"] == 1
    assert result["medium"]["total"] == 1
    assert result["medium"]["wins"] == 0
    assert result["long"]["total"] == 1


def test_get_stats_by_close_horizon_no_close_bucket(tmp_db):
    """Signal with no close_time goes to no_close bucket."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, direction, market_price, result, outcome,
             pnl_if_traded, source, close_time)
            VALUES ('noc','2026-06-01T00:00:00+00:00','KXNOC','YES',0.40,'WIN','YES',0.5,'paper',NULL)
        """)
    result = logger.get_stats_by_close_horizon()
    assert result["no_close"]["total"] == 1


# ─── get_stats_by_time_horizon ────────────────────────────────────────────────

def _insert_horizon(call_id, time_horizon, result_val, pnl, edge=0.12):
    """Insert a resolved paper signal with time_horizon for stats tests."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, direction, market_price, our_estimate,
             edge, result, outcome, pnl_if_traded, source, time_horizon)
            VALUES (?,datetime('now'),?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id, f"KX{call_id}", "YES", 0.40, 0.55,
            edge, result_val, "YES", pnl, "paper", time_horizon,
        ))


def test_get_stats_by_time_horizon_empty(tmp_db):
    """Empty DB → all buckets have total=0, win_rate=None."""
    stats = logger.get_stats_by_time_horizon()
    for bucket in ("INTRADAY", "WEEKLY", "MONTHLY", "QUARTERLY", "LONG"):
        assert stats[bucket]["total"]    == 0
        assert stats[bucket]["win_rate"] is None


def test_get_stats_by_time_horizon_counts_per_bucket(tmp_db):
    """Signals are counted in the correct time horizon bucket."""
    _insert_horizon("th1", "MONTHLY",   "WIN",  0.60)
    _insert_horizon("th2", "MONTHLY",   "LOSS", -0.40)
    _insert_horizon("th3", "WEEKLY",    "WIN",  0.60)
    _insert_horizon("th4", "QUARTERLY", "WIN",  0.60)
    stats = logger.get_stats_by_time_horizon()
    assert stats["MONTHLY"]["total"]   == 2
    assert stats["MONTHLY"]["wins"]    == 1
    assert stats["WEEKLY"]["total"]    == 1
    assert stats["QUARTERLY"]["total"] == 1
    assert stats["INTRADAY"]["total"]  == 0


def test_get_stats_by_time_horizon_excludes_real_fills(tmp_db):
    """Real fill rows must not contaminate horizon stats."""
    _insert_horizon("th5", "MONTHLY", "WIN", 0.60)
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, direction, market_price,
             result, outcome, pnl_if_traded, source, time_horizon)
            VALUES ('rf4',datetime('now'),'KXrf','YES',0.40,
                    'WIN','YES',0.60,'real_fill','MONTHLY')
        """)
    stats = logger.get_stats_by_time_horizon()
    assert stats["MONTHLY"]["total"] == 1  # only paper row


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


# ─── get_stats_by_sig ────────────────────────────────────────────────────────

def _insert_resolved_sig(call_id, ticker, direction, market_price, outcome, result, pnl,
                          sig_edge=False, sig_drift=False, sig_br_none=False):
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, title, market_price, our_estimate,
             edge, direction, confidence, whale_detected, whale_direction,
             outcome, result, pnl_if_traded, run_id, source,
             sig_edge, sig_drift, sig_br_none)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id, "2026-06-17T10:00:00Z",
            ticker, "Test", market_price, 0.50, 0.10,
            direction, "MED", 0, "",
            outcome, result, pnl,
            "run-test", "paper",
            1 if sig_edge    else 0,
            1 if sig_drift   else 0,
            1 if sig_br_none else 0,
        ))


def test_get_stats_by_sig_empty_db(tmp_db):
    """Empty DB → all three sig types return total=0, win_rate=None."""
    stats = logger.get_stats_by_sig()
    for key in ("sig_edge", "sig_drift", "sig_br_none"):
        assert stats[key]["total"]    == 0
        assert stats[key]["win_rate"] is None


def test_get_stats_by_sig_counts_sig_edge_wins(tmp_db):
    """sig_edge wins and losses are counted independently of other sig types."""
    _insert_resolved_sig("se1", "E1", "YES", 0.30, "YES", "WIN",  0.70, sig_edge=True)
    _insert_resolved_sig("se2", "E2", "YES", 0.30, "NO",  "LOSS", -0.30, sig_edge=True)
    _insert_resolved_sig("sd1", "D1", "YES", 0.30, "YES", "WIN",  0.70, sig_drift=True)

    stats = logger.get_stats_by_sig()

    assert stats["sig_edge"]["total"] == 2
    assert stats["sig_edge"]["wins"]  == 1
    assert stats["sig_edge"]["win_rate"] == pytest.approx(50.0)

    assert stats["sig_drift"]["total"] == 1
    assert stats["sig_drift"]["wins"]  == 1
    assert stats["sig_drift"]["win_rate"] == pytest.approx(100.0)

    assert stats["sig_br_none"]["total"] == 0


def test_get_stats_by_sig_excludes_unresolved(tmp_db):
    """Unresolved paper signals must not count toward win_rate."""
    _insert_resolved_sig("se3", "E3", "YES", 0.30, "YES", "WIN", 0.70, sig_edge=True)
    # Insert unresolved sig_edge row
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals (call_id, timestamp, ticker, direction, market_price,
                                 outcome, result, source, sig_edge)
            VALUES ('se4','2026-06-17T10:00:00Z','E4','YES',0.30,'','','paper',1)
        """)

    stats = logger.get_stats_by_sig()
    # Only the resolved row should count
    assert stats["sig_edge"]["total"] == 1
    assert stats["sig_edge"]["wins"]  == 1


def test_get_stats_by_sig_excludes_real_fills(tmp_db):
    """Real fill rows must not contaminate sig stats even if sig_edge=1."""
    _insert_resolved_sig("se5", "E5", "YES", 0.30, "YES", "WIN", 0.70, sig_edge=True)
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals (call_id, timestamp, ticker, direction, market_price,
                                 outcome, result, pnl_if_traded, source, sig_edge)
            VALUES ('rf2','2026-06-17T10:00:00Z','REALFILL','YES',0.30,
                    'YES','WIN',0.70,'real_fill',1)
        """)

    stats = logger.get_stats_by_sig()
    assert stats["sig_edge"]["total"] == 1  # real_fill row excluded


def test_get_stats_by_sig_overlap_counted_separately(tmp_db):
    """A signal with both sig_edge=1 and sig_drift=1 is counted in BOTH buckets."""
    _insert_resolved_sig("both1", "B1", "YES", 0.30, "YES", "WIN", 0.70,
                          sig_edge=True, sig_drift=True)

    stats = logger.get_stats_by_sig()
    assert stats["sig_edge"]["total"]  == 1
    assert stats["sig_drift"]["total"] == 1


# ─── get_recent_tickers / get_week_signals ────────────────────────────────────

def test_get_recent_tickers_returns_recent(tmp_db):
    """Tickers inserted now appear in get_recent_tickers(days=7)."""
    logger.log_signal({
        "ticker": "KXRECENT", "title": "T", "market_price": 0.40,
        "our_estimate": 0.55, "edge": 0.15, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "run_id": "r1",
    })
    tickers = logger.get_recent_tickers(days=7)
    assert "KXRECENT" in tickers


def test_get_recent_tickers_excludes_old(tmp_db):
    """Tickers with old timestamps must not appear in get_recent_tickers."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source)
            VALUES ('old1','2020-01-01T00:00:00+00:00','KXOLD','YES',0.30,'paper')
        """)
    tickers = logger.get_recent_tickers(days=7)
    assert "KXOLD" not in tickers


def test_get_week_signals_returns_recent_rows(tmp_db):
    """get_week_signals returns rows timestamped within the last 7 days."""
    logger.log_signal({
        "ticker": "KXWEEK", "title": "T", "market_price": 0.40,
        "our_estimate": 0.55, "edge": 0.15, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "run_id": "r2",
    })
    rows = logger.get_week_signals(days=7)
    tickers = [r["ticker"] for r in rows]
    assert "KXWEEK" in tickers


def test_get_week_signals_excludes_old_rows(tmp_db):
    """Rows older than days window must not appear in get_week_signals."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source)
            VALUES ('old2','2020-01-01T00:00:00+00:00','KXOLDWEEK','YES',0.30,'paper')
        """)
    rows = logger.get_week_signals(days=7)
    tickers = [r["ticker"] for r in rows]
    assert "KXOLDWEEK" not in tickers


# ─── get_signal_log / get_resolved_track_record / get_market_data (MCP) ───────

def test_get_signal_log_returns_rows(tmp_db):
    _insert("s1", "KXLOG1", "YES", 0.40)
    rows = logger.get_signal_log(limit=10)
    assert any(r["ticker"] == "KXLOG1" for r in rows)


def test_get_signal_log_excludes_pass(tmp_db):
    _insert("s2", "KXPASSED", "PASS", 0.40)
    rows = logger.get_signal_log(limit=10)
    assert not any(r["ticker"] == "KXPASSED" for r in rows)


def test_get_signal_log_respects_limit(tmp_db):
    for i in range(5):
        _insert(f"s{i}", f"KXLIM{i}", "YES", 0.40)
    rows = logger.get_signal_log(limit=2)
    assert len(rows) == 2


def test_get_signal_log_ticker_filter(tmp_db):
    _insert("s3", "KXFOO", "YES", 0.40)
    _insert("s4", "KXBAR", "YES", 0.40)
    rows = logger.get_signal_log(limit=10, ticker="KXFOO")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "KXFOO"


def test_get_signal_log_resolved_only(tmp_db):
    _insert("s5", "KXUNRES", "YES", 0.40)
    _insert("s6", "KXRES", "YES", 0.40, outcome="yes", result="WIN")
    rows = logger.get_signal_log(limit=10, resolved_only=True)
    tickers = [r["ticker"] for r in rows]
    assert "KXRES" in tickers
    assert "KXUNRES" not in tickers


def test_get_resolved_track_record_only_resolved(tmp_db):
    _insert("s7", "KXOPEN", "YES", 0.40)
    _insert("s8", "KXDONE", "YES", 0.40, outcome="yes", result="WIN", pnl=0.60)
    rows = logger.get_resolved_track_record()
    tickers = [r["ticker"] for r in rows]
    assert "KXDONE" in tickers
    assert "KXOPEN" not in tickers


def test_get_resolved_track_record_excludes_pass(tmp_db):
    _insert("s9", "KXPASSDONE", "PASS", 0.40, outcome="yes", result="")
    rows = logger.get_resolved_track_record()
    assert not any(r["ticker"] == "KXPASSDONE" for r in rows)


def test_get_resolved_track_record_has_score_and_outcome(tmp_db):
    _insert("s10", "KXSCORED", "YES", 0.40, outcome="yes", result="WIN", pnl=0.60)
    rows = logger.get_resolved_track_record()
    row = next(r for r in rows if r["ticker"] == "KXSCORED")
    assert row["our_estimate"] == 0.40  # _insert hardcodes our_estimate=0.40
    assert row["outcome"] == "yes"
    assert row["result"] == "WIN"


def test_get_market_data_by_ticker_partial_match(tmp_db):
    _insert("s11", "KXCABLEAVE-26MAY22", "YES", 0.40)
    rows = logger.get_market_data(ticker="CABLEAVE")
    assert any(r["ticker"] == "KXCABLEAVE-26MAY22" for r in rows)


def test_get_market_data_by_date(tmp_db):
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source)
            VALUES ('dated1','2026-05-01T12:00:00+00:00','KXDATED','YES',0.30,'paper')
        """)
    rows = logger.get_market_data(date="2026-05-01")
    assert any(r["ticker"] == "KXDATED" for r in rows)


def test_get_market_data_date_excludes_other_days(tmp_db):
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source)
            VALUES ('dated2','2026-05-02T12:00:00+00:00','KXOTHERDAY','YES',0.30,'paper')
        """)
    rows = logger.get_market_data(date="2026-05-01")
    assert not any(r["ticker"] == "KXOTHERDAY" for r in rows)


def test_get_market_data_no_filters_returns_empty(tmp_db):
    _insert("s12", "KXANY", "YES", 0.40)
    assert logger.get_market_data() == []


# ─── get_ticker_day_count ─────────────────────────────────────────────────────

def test_get_ticker_day_count_returns_zero_for_missing(tmp_db):
    """Unknown ticker returns 0."""
    assert logger.get_ticker_day_count("KXNEVER", days=14) == 0


def test_get_ticker_day_count_empty_ticker_returns_zero(tmp_db):
    """Empty-string ticker short-circuits to 0."""
    assert logger.get_ticker_day_count("", days=14) == 0


def test_get_ticker_day_count_counts_distinct_days(tmp_db):
    """Two signals on different days = count 2; same ticker repeated same day = count 1."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    day1 = (now - timedelta(days=2)).isoformat()
    day2 = (now - timedelta(days=1)).isoformat()
    with logger._db() as conn:
        conn.execute(
            "INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source) "
            "VALUES ('d1a',?,'KXCOUNT','YES',0.30,'paper')", (day1,)
        )
        conn.execute(
            "INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source) "
            "VALUES ('d1b',?,'KXCOUNT','YES',0.30,'paper')", (day1,)
        )
        conn.execute(
            "INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source) "
            "VALUES ('d2a',?,'KXCOUNT','YES',0.30,'paper')", (day2,)
        )
    assert logger.get_ticker_day_count("KXCOUNT", days=14) == 2


def test_get_ticker_day_count_ignores_old_entries(tmp_db):
    """Entries older than days window are excluded from the count."""
    with logger._db() as conn:
        conn.execute(
            "INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source) "
            "VALUES ('old','2020-01-01T00:00:00+00:00','KXOLD2','YES',0.30,'paper')"
        )
    assert logger.get_ticker_day_count("KXOLD2", days=14) == 0


# ─── log_pass / get_pass_tickers ─────────────────────────────────────────────

def test_log_pass_stores_direction_pass(tmp_db):
    """log_pass inserts a row with direction='PASS'."""
    logger.log_pass({
        "ticker": "KXPASS1", "title": "T", "market_price": 0.45,
        "our_estimate": 0.50, "edge": 0.05, "confidence": "LOW",
        "run_id": "rpass1", "flag_path": "HEURISTIC",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT direction, source FROM signals WHERE ticker='KXPASS1'"
        ).fetchone()
    assert row is not None
    assert row["direction"] == "PASS"
    assert row["source"] == "paper"


def test_log_pass_does_not_appear_in_get_stats(tmp_db):
    """PASS rows must be excluded from win-rate stats."""
    logger.log_pass({
        "ticker": "KXPASS2", "title": "T", "market_price": 0.45,
        "our_estimate": 0.50, "edge": 0.05, "confidence": "LOW", "run_id": "rpass2",
    })
    stats = logger.get_stats()
    assert stats["total_calls"] == 0  # PASS rows don't count as paper signals


def test_get_pass_tickers_returns_counts(tmp_db):
    """get_pass_tickers returns ticker→count dict from recent PASS rows."""
    for i in range(3):
        logger.log_pass({
            "ticker": "KXPASSCOUNT", "title": "T", "market_price": 0.45,
            "our_estimate": 0.50, "edge": 0.05, "confidence": "LOW",
            "run_id": f"rpc{i}",
        })
    result = logger.get_pass_tickers(days=14)
    assert "KXPASSCOUNT" in result
    assert result["KXPASSCOUNT"] == 3


def test_get_pass_tickers_empty_when_no_passes(tmp_db):
    result = logger.get_pass_tickers(days=14)
    assert result == {}


def test_get_pass_tickers_excludes_old_passes(tmp_db):
    with logger._db() as conn:
        conn.execute(
            "INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source) "
            "VALUES ('oldpass','2020-01-01T00:00:00+00:00','KXOLDPASS','PASS',0.40,'paper')"
        )
    result = logger.get_pass_tickers(days=14)
    assert "KXOLDPASS" not in result


# ─── get_signal_history_batch ─────────────────────────────────────────────────

def test_signal_history_batch_empty_tickers_returns_empty(tmp_db):
    result = logger.get_signal_history_batch([])
    assert result == {}


def test_signal_history_batch_missing_ticker_returns_empty(tmp_db):
    result = logger.get_signal_history_batch(["KXNEVEREXISTS"])
    assert "KXNEVEREXISTS" not in result


def test_signal_history_batch_returns_rows_for_ticker(tmp_db):
    logger.log_signal({
        "ticker": "KXHIST1", "title": "T", "market_price": 0.45,
        "our_estimate": 0.60, "edge": 0.15, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "run_id": "r-hist1",
    })
    result = logger.get_signal_history_batch(["KXHIST1"])
    assert "KXHIST1" in result
    assert len(result["KXHIST1"]) == 1
    assert result["KXHIST1"][0]["direction"] == "YES"
    assert result["KXHIST1"][0]["market_price"] == pytest.approx(0.45)


def test_signal_history_batch_batches_multiple_tickers(tmp_db):
    for ticker, price in [("KXBATCH-A", 0.40), ("KXBATCH-B", 0.55)]:
        logger.log_signal({
            "ticker": ticker, "title": "T", "market_price": price,
            "our_estimate": 0.65, "edge": 0.10, "direction": "YES",
            "confidence": "MED", "whale_detected": False, "whale_direction": "",
            "run_id": "rbatch",
        })
    result = logger.get_signal_history_batch(["KXBATCH-A", "KXBATCH-B"])
    assert "KXBATCH-A" in result
    assert "KXBATCH-B" in result


def test_signal_history_batch_excludes_old_entries(tmp_db):
    with logger._db() as conn:
        conn.execute(
            "INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source) "
            "VALUES ('oldhist','2020-01-01T00:00:00+00:00','KXHIST-OLD','YES',0.40,'paper')"
        )
    result = logger.get_signal_history_batch(["KXHIST-OLD"], days=14)
    assert result.get("KXHIST-OLD", []) == []


def test_signal_history_batch_excludes_non_paper_sources(tmp_db):
    with logger._db() as conn:
        conn.execute(
            "INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source) "
            "VALUES ('probe1',datetime('now'),'KXHIST-PROBE','YES',0.40,'research_probe')"
        )
    result = logger.get_signal_history_batch(["KXHIST-PROBE"])
    assert result.get("KXHIST-PROBE", []) == []


def test_signal_history_batch_multiple_days_counted(tmp_db):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    day1 = (now - timedelta(days=3)).isoformat()
    day2 = (now - timedelta(days=1)).isoformat()
    with logger._db() as conn:
        conn.execute(
            "INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source) "
            "VALUES ('mh1',?,'KXMULTI-HIST','YES',0.40,'paper')", (day1,)
        )
        conn.execute(
            "INSERT INTO signals (call_id, timestamp, ticker, direction, market_price, source) "
            "VALUES ('mh2',?,'KXMULTI-HIST','YES',0.38,'paper')", (day2,)
        )
    result = logger.get_signal_history_batch(["KXMULTI-HIST"])
    assert "KXMULTI-HIST" in result
    assert len(result["KXMULTI-HIST"]) == 2
    distinct_days = len({r["timestamp"][:10] for r in result["KXMULTI-HIST"]})
    assert distinct_days == 2


# ─── log_run ─────────────────────────────────────────────────────────────────

def test_log_run_writes_run_row(tmp_db):
    """log_run must insert a row into the runs table with correct fields."""
    logger.log_run({
        "run_id":            "run-unit-1",
        "timestamp":         "2026-06-17T10:00:00Z",
        "markets_scanned":   250,
        "signals_generated": 5,
        "whale_flags":       2,
        "model_used":        "claude-sonnet-4-6",
        "tokens_used":       12000,
        "cost_usd":          0.0,
        "runtime_ms":        45000,
    })
    with logger._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id='run-unit-1'").fetchone()
    assert row is not None
    assert row["markets_scanned"]   == 250
    assert row["signals_generated"] == 5
    assert row["model_used"]        == "claude-sonnet-4-6"


def test_log_run_idempotent_replace(tmp_db):
    """Logging the same run_id twice replaces the row (INSERT OR REPLACE)."""
    logger.log_run({"run_id": "run-idem", "timestamp": "2026-06-17T00:00:00Z",
                    "markets_scanned": 100, "signals_generated": 2,
                    "whale_flags": 0, "model_used": "m1",
                    "tokens_used": 5000, "cost_usd": 0.0, "runtime_ms": 10000})
    logger.log_run({"run_id": "run-idem", "timestamp": "2026-06-17T01:00:00Z",
                    "markets_scanned": 200, "signals_generated": 4,
                    "whale_flags": 1, "model_used": "m2",
                    "tokens_used": 8000, "cost_usd": 0.0, "runtime_ms": 20000})

    with logger._db() as conn:
        rows = conn.execute("SELECT * FROM runs WHERE run_id='run-idem'").fetchall()
    assert len(rows) == 1
    assert rows[0]["markets_scanned"] == 200  # second write wins


# ─── get_brier_score ──────────────────────────────────────────────────────────

def test_brier_score_pending_when_no_resolved(tmp_db):
    """Returns PENDING label and None score when no resolved signals exist."""
    result = logger.get_brier_score()
    assert result["brier_score"] is None
    assert result["n"] == 0
    assert "PENDING" in result["label"]


def test_brier_score_perfect_for_all_wins(tmp_db):
    """Brier score = 0 when every YES signal wins with estimate=1.0."""
    for i in range(3):
        with logger._db() as conn:
            conn.execute(
                "INSERT INTO signals "
                "(call_id,timestamp,ticker,direction,our_estimate,result,outcome,source) "
                "VALUES (?,datetime('now'),?,?,?,?,?,?)",
                (f"b{i}", f"KX{i}", "YES", 1.0, "WIN", "YES", "paper")
            )
    result = logger.get_brier_score()
    assert result["brier_score"] == pytest.approx(0.0)
    assert result["n"] == 3


def test_brier_score_25_for_random(tmp_db):
    """Brier score = 0.25 when estimate=0.5 and outcomes are 50/50."""
    for i, (win, est) in enumerate([(True, 0.5), (False, 0.5)]):
        outcome = "YES" if win else "NO"
        result_val = "WIN" if win else "LOSS"
        with logger._db() as conn:
            conn.execute(
                "INSERT INTO signals "
                "(call_id,timestamp,ticker,direction,our_estimate,result,outcome,source) "
                "VALUES (?,datetime('now'),?,?,?,?,?,?)",
                (f"c{i}", f"KXR{i}", "YES", est, result_val, outcome, "paper")
            )
    result = logger.get_brier_score()
    assert result["brier_score"] == pytest.approx(0.25)


def test_brier_score_excludes_probe_rows(tmp_db):
    """Probe rows (source=research_probe) are excluded from paper Brier score."""
    with logger._db() as conn:
        conn.execute(
            "INSERT INTO signals "
            "(call_id,timestamp,ticker,direction,our_estimate,result,outcome,source) "
            "VALUES ('probe1',datetime('now'),'KXP','YES',0.9,'WIN','YES','research_probe')"
        )
    result = logger.get_brier_score()
    assert result["n"] == 0  # probe not counted


# ─── get_stats_by_confidence ─────────────────────────────────────────────────

def _insert_conf(call_id, confidence, result_val, pnl, tmp_db):
    with logger._db() as conn:
        conn.execute(
            "INSERT INTO signals "
            "(call_id,timestamp,ticker,direction,market_price,our_estimate,"
            "confidence,result,outcome,pnl_if_traded) "
            "VALUES (?,datetime('now'),?,?,?,?,?,?,?,?)",
            (call_id, f"KX{call_id}", "YES", 0.4, 0.6, confidence,
             result_val, "YES", pnl)
        )


def test_conf_stats_empty_when_no_resolved(tmp_db):
    """Returns zero totals for all levels when DB is empty."""
    stats = logger.get_stats_by_confidence()
    for lvl in ("HIGH", "MED", "LOW"):
        assert stats[lvl]["total"] == 0
        assert stats[lvl]["win_rate"] is None


def test_conf_stats_accumulates_wins_and_losses(tmp_db):
    """Tracks wins and losses correctly per confidence level."""
    _insert_conf("h1", "HIGH", "WIN",  0.60, tmp_db)
    _insert_conf("h2", "HIGH", "WIN",  0.60, tmp_db)
    _insert_conf("h3", "HIGH", "LOSS", -0.40, tmp_db)
    _insert_conf("m1", "MED",  "WIN",  0.60, tmp_db)
    _insert_conf("l1", "LOW",  "LOSS", -0.40, tmp_db)

    stats = logger.get_stats_by_confidence()
    assert stats["HIGH"]["total"] == 3
    assert stats["HIGH"]["wins"]  == 2
    assert stats["HIGH"]["losses"] == 1
    assert abs(stats["HIGH"]["win_rate"] - 66.67) < 0.1
    assert stats["MED"]["total"]  == 1
    assert stats["LOW"]["total"]  == 1
    assert stats["LOW"]["wins"]   == 0


def test_conf_stats_pnl_sums_correctly(tmp_db):
    """total_pnl sums across all signals for a confidence level."""
    _insert_conf("a1", "HIGH", "WIN",  0.70, tmp_db)
    _insert_conf("a2", "HIGH", "LOSS", -0.30, tmp_db)
    stats = logger.get_stats_by_confidence()
    assert abs(stats["HIGH"]["total_pnl"] - 0.40) < 0.001


# ─── get_stats_by_heuristic_alignment ─────────────────────────────────────────

def _insert_align(call_id, direction, heuristic_direction, result_val, pnl, edge=0.12):
    """Insert a resolved paper signal with alignment fields."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, direction, market_price, our_estimate,
             edge, result, outcome, pnl_if_traded, source,
             heuristic_direction)
            VALUES (?,datetime('now'),?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id, f"KX{call_id}", direction, 0.40, 0.55,
            edge, result_val, "YES", pnl, "paper",
            heuristic_direction,
        ))


def test_heuristic_alignment_empty_db(tmp_db):
    """Empty DB → all groups return total=0, win_rate=None."""
    stats = logger.get_stats_by_heuristic_alignment()
    for grp in ("aligned", "override", "no_heuristic"):
        assert stats[grp]["total"]    == 0
        assert stats[grp]["win_rate"] is None


def test_heuristic_alignment_aligned_group(tmp_db):
    """Signal where Claude direction == heuristic_direction → counted in 'aligned'."""
    _insert_align("al1", "YES", "YES", "WIN",  0.60)
    _insert_align("al2", "YES", "YES", "LOSS", -0.40)
    stats = logger.get_stats_by_heuristic_alignment()
    assert stats["aligned"]["total"]    == 2
    assert stats["aligned"]["wins"]     == 1
    assert stats["aligned"]["win_rate"] == pytest.approx(50.0)
    assert stats["override"]["total"]   == 0
    assert stats["no_heuristic"]["total"] == 0


def test_heuristic_alignment_override_group(tmp_db):
    """Signal where Claude direction != heuristic_direction (and not NEUTRAL) → 'override'."""
    _insert_align("ov1", "YES", "NO",  "WIN",  0.60)
    _insert_align("ov2", "NO",  "YES", "LOSS", -0.40)
    stats = logger.get_stats_by_heuristic_alignment()
    assert stats["override"]["total"] == 2
    assert stats["override"]["wins"]  == 1
    assert stats["aligned"]["total"]  == 0


def test_heuristic_alignment_neutral_goes_to_no_heuristic(tmp_db):
    """NEUTRAL heuristic_direction is not a directional lean → counted in 'no_heuristic'."""
    _insert_align("nh1", "YES", "NEUTRAL", "WIN", 0.60)
    stats = logger.get_stats_by_heuristic_alignment()
    assert stats["no_heuristic"]["total"] == 1
    assert stats["aligned"]["total"]      == 0
    assert stats["override"]["total"]     == 0


def test_heuristic_alignment_null_heuristic_goes_to_no_heuristic(tmp_db):
    """NULL heuristic_direction → counted in 'no_heuristic'."""
    _insert_align("nh2", "YES", None, "WIN", 0.60)
    stats = logger.get_stats_by_heuristic_alignment()
    assert stats["no_heuristic"]["total"] == 1


def test_heuristic_alignment_excludes_real_fills(tmp_db):
    """Real fill rows must not appear in alignment stats even if heuristic_direction is set."""
    _insert_align("al3", "YES", "YES", "WIN", 0.60)  # paper
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, direction, market_price,
             result, outcome, pnl_if_traded, source, heuristic_direction)
            VALUES ('rf3',datetime('now'),'KXrf','YES',0.40,
                    'WIN','YES',0.60,'real_fill','YES')
        """)
    stats = logger.get_stats_by_heuristic_alignment()
    assert stats["aligned"]["total"] == 1  # only paper row


# ─── leviathan_score: schema + stats ──────────────────────────────────────────

def test_schema_includes_leviathan_score(tmp_db):
    """leviathan_score column exists in the signals table."""
    with logger._db() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    assert "leviathan_score" in cols


def test_log_signal_stores_leviathan_score(tmp_db):
    """leviathan_score value is persisted when included in the signal dict."""
    sig = {
        "ticker": "KXLV1", "title": "LV test", "market_price": 0.30,
        "our_estimate": 0.50, "edge": 0.20, "direction": "YES",
        "confidence": "HIGH", "run_id": "test", "leviathan_score": 72,
    }
    logger.log_signal(sig)
    with logger._db() as conn:
        row = conn.execute(
            "SELECT leviathan_score FROM signals WHERE ticker='KXLV1'"
        ).fetchone()
    assert row is not None
    assert row["leviathan_score"] == 72


def test_log_signal_leviathan_score_none_when_absent(tmp_db):
    """leviathan_score is NULL when not provided in signal dict."""
    sig = {
        "ticker": "KXLV2", "title": "No score", "market_price": 0.25,
        "our_estimate": 0.40, "edge": 0.15, "direction": "YES",
        "confidence": "MED", "run_id": "test",
    }
    logger.log_signal(sig)
    with logger._db() as conn:
        row = conn.execute(
            "SELECT leviathan_score FROM signals WHERE ticker='KXLV2'"
        ).fetchone()
    assert row["leviathan_score"] is None


def test_get_stats_by_leviathan_score_empty(tmp_db):
    """Empty DB → all bands have total=0, win_rate=None."""
    stats = logger.get_stats_by_leviathan_score()
    for band in ("A", "B", "C", "D", "unscored"):
        assert stats[band]["total"]    == 0
        assert stats[band]["win_rate"] is None


def test_get_stats_by_leviathan_score_buckets_a_and_d(tmp_db):
    """Score=75 goes to A-band; score=30 goes to D-band."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id,timestamp,ticker,direction,market_price,result,outcome,
             pnl_if_traded,source,leviathan_score,edge)
            VALUES
            ('lva','2026-06-01T00:00:00+00:00','KXA','YES',0.40,'WIN','YES',0.60,'paper',75,0.20),
            ('lvd','2026-06-01T00:00:00+00:00','KXD','YES',0.40,'LOSS','NO',-0.40,'paper',30,0.10)
        """)
    stats = logger.get_stats_by_leviathan_score()
    assert stats["A"]["total"] == 1
    assert stats["A"]["wins"]  == 1
    assert stats["A"]["win_rate"] == pytest.approx(100.0)
    assert stats["D"]["total"] == 1
    assert stats["D"]["wins"]  == 0
    assert stats["D"]["win_rate"] == pytest.approx(0.0)


def test_get_stats_by_leviathan_score_unscored_bucket(tmp_db):
    """NULL leviathan_score goes to the unscored bucket."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id,timestamp,ticker,direction,market_price,result,outcome,
             pnl_if_traded,source,leviathan_score)
            VALUES ('lvn','2026-06-01T00:00:00+00:00','KXN','YES',0.40,'WIN','YES',0.60,'paper',NULL)
        """)
    stats = logger.get_stats_by_leviathan_score()
    assert stats["unscored"]["total"] == 1


def test_get_stats_by_leviathan_score_excludes_pass(tmp_db):
    """PASS direction rows are excluded from leviathan_score stats."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id,timestamp,ticker,direction,market_price,result,outcome,
             pnl_if_traded,source,leviathan_score)
            VALUES ('lvp','2026-06-01T00:00:00+00:00','KXP','PASS',0.40,'WIN','YES',0.60,'paper',70)
        """)
    stats = logger.get_stats_by_leviathan_score()
    assert stats["A"]["total"] == 0  # PASS row excluded


# ─── heuristic_label: schema + persistence ────────────────────────────────────

def test_schema_includes_heuristic_label(tmp_db):
    """heuristic_label column exists in the signals table after migration."""
    with logger._db() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()}
    assert "heuristic_label" in cols


def test_log_signal_stores_heuristic_label(tmp_db):
    """log_signal persists heuristic_label when provided."""
    logger.log_signal({
        "ticker": "KXHL-1", "title": "PDUFA test", "market_price": 0.30,
        "our_estimate": 0.85, "edge": 0.55, "direction": "YES",
        "confidence": "HIGH", "whale_detected": False, "whale_direction": "",
        "flag_path": "HEURISTIC", "watchlist_signal": False,
        "base_rate": 0.85, "heuristic_direction": "YES",
        "heuristic_label": "PDUFA date",
        "run_id": "rhl1",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT heuristic_label FROM signals WHERE ticker='KXHL-1'"
        ).fetchone()
    assert row is not None
    assert row["heuristic_label"] == "PDUFA date"


def test_log_signal_heuristic_label_none_when_absent(tmp_db):
    """heuristic_label is NULL when not provided in signal dict."""
    logger.log_signal({
        "ticker": "KXHL-2", "title": "No label", "market_price": 0.40,
        "our_estimate": 0.55, "edge": 0.15, "direction": "YES",
        "confidence": "MED", "whale_detected": False, "whale_direction": "",
        "run_id": "rhl2",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT heuristic_label FROM signals WHERE ticker='KXHL-2'"
        ).fetchone()
    assert row is not None
    assert row["heuristic_label"] is None


def test_log_pass_stores_heuristic_label(tmp_db):
    """log_pass persists heuristic_label for PASS rows."""
    logger.log_pass({
        "ticker": "KXHLPASS-1", "title": "T", "market_price": 0.45,
        "our_estimate": 0.50, "edge": 0.05, "confidence": "LOW",
        "flag_path": "HEURISTIC", "heuristic_label": "crypto ETF",
        "run_id": "rhlp1",
    })
    with logger._db() as conn:
        row = conn.execute(
            "SELECT heuristic_label, direction FROM signals WHERE ticker='KXHLPASS-1'"
        ).fetchone()
    assert row is not None
    assert row["direction"] == "PASS"
    assert row["heuristic_label"] == "crypto ETF"


# ─── get_stats_by_heuristic_label ─────────────────────────────────────────────

def _insert_hl_signal(call_id, heuristic_label, direction, result_val, pnl, edge=0.12):
    """Insert a resolved paper signal with heuristic_label for stats tests."""
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, direction, market_price, our_estimate,
             edge, result, outcome, pnl_if_traded, source, heuristic_label)
            VALUES (?,datetime('now'),?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id, f"KX{call_id}", direction, 0.40, 0.55,
            edge, result_val, "YES" if result_val == "WIN" else "NO",
            pnl, "paper", heuristic_label,
        ))


def test_get_stats_by_heuristic_label_empty_db(tmp_db):
    """Empty DB returns empty list — no errors."""
    result = logger.get_stats_by_heuristic_label()
    assert result == []


def test_get_stats_by_heuristic_label_groups_by_label(tmp_db):
    """Signals with same label are grouped together."""
    _insert_hl_signal("hl1", "PDUFA date",         "YES", "WIN",  0.60)
    _insert_hl_signal("hl2", "PDUFA date",         "YES", "WIN",  0.60)
    _insert_hl_signal("hl3", "PDUFA date",         "YES", "LOSS", -0.40)
    _insert_hl_signal("hl4", "crypto protocol upgrade", "YES", "WIN",  0.60)

    result = logger.get_stats_by_heuristic_label()
    labels = {r["heuristic_label"] for r in result}
    assert "PDUFA date"              in labels
    assert "crypto protocol upgrade" in labels

    pdufa = next(r for r in result if r["heuristic_label"] == "PDUFA date")
    assert pdufa["total"]    == 3
    assert pdufa["wins"]     == 2
    assert pdufa["losses"]   == 1
    assert abs(pdufa["win_rate"] - 66.7) < 0.1


def test_get_stats_by_heuristic_label_excludes_null_label(tmp_db):
    """Signals with NULL heuristic_label are excluded from the grouped result."""
    _insert_hl_signal("hl5", "PDUFA date", "YES", "WIN",  0.60)
    # Insert a resolved signal with no label
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id,timestamp,ticker,direction,market_price,result,outcome,
             pnl_if_traded,source,heuristic_label)
            VALUES ('hl-null',datetime('now'),'KXNULL','YES',0.40,'WIN','YES',0.60,'paper',NULL)
        """)
    result = logger.get_stats_by_heuristic_label()
    # Only the labelled signal should appear
    assert len(result) == 1
    assert result[0]["heuristic_label"] == "PDUFA date"


def test_get_stats_by_heuristic_label_excludes_unresolved(tmp_db):
    """Unresolved signals (empty outcome) are not counted."""
    _insert_hl_signal("hl6", "credit rating change", "YES", "WIN", 0.60)
    # Unresolved row with same label
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id,timestamp,ticker,direction,market_price,source,heuristic_label,
             outcome,result)
            VALUES ('hl-unres',datetime('now'),'KXUNRES','YES',0.40,'paper',
                    'credit rating change','','')
        """)
    result = logger.get_stats_by_heuristic_label()
    assert len(result) == 1
    assert result[0]["total"] == 1  # only the resolved row


def test_get_stats_by_heuristic_label_excludes_real_fills(tmp_db):
    """Real fill rows are never included even if labelled."""
    _insert_hl_signal("hl7", "FDA approval", "YES", "WIN", 0.60)
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id,timestamp,ticker,direction,market_price,result,outcome,
             pnl_if_traded,source,heuristic_label)
            VALUES ('hl-rf',datetime('now'),'KXRF','YES',0.40,'WIN','YES',0.60,
                    'real_fill','FDA approval')
        """)
    result = logger.get_stats_by_heuristic_label()
    fda = next((r for r in result if r["heuristic_label"] == "FDA approval"), None)
    assert fda is not None
    assert fda["total"] == 1  # only paper row


def test_get_stats_by_heuristic_label_sorted_by_win_rate(tmp_db):
    """Result list is sorted descending by win_rate."""
    _insert_hl_signal("hl8", "bond/debt issuance",  "YES", "WIN",  0.60)
    _insert_hl_signal("hl9", "bond/debt issuance",  "YES", "WIN",  0.60)
    _insert_hl_signal("hl10", "unionization vote",  "YES", "LOSS", -0.40)
    result = logger.get_stats_by_heuristic_label()
    if len(result) >= 2:
        assert result[0]["win_rate"] >= result[1]["win_rate"]
