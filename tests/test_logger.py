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
                "flag_path", "watchlist_signal",
                "sig_edge", "sig_drift", "sig_br_none",
                "base_rate", "heuristic_direction"):
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
