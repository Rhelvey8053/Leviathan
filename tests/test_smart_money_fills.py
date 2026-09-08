"""
Offline tests for core.logger's smart_money_fills persistence
(record_smart_money_fills / backfill_smart_money_resolutions /
get_resolved_count_per_wallet_max).

All tests use a throwaway SQLite file (never leviathan.db). No network
calls -- trader_data dicts are constructed directly, matching the shape
analysis.smart_money_scan.fetch_watchlist_positions actually returns.
"""

import pytest
from core import logger


@pytest.fixture(autouse=False)
def tmp_db(tmp_path, monkeypatch):
    """Fresh throwaway DB for each test -- never touches leviathan.db."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(logger, "DB_PATH", db_file)
    logger._init_db()
    return db_file


def _position(slug="event-a", outcome="Yes", title="Will A happen?",
              curPrice=0.60, currentValue=500.0, redeemable=False,
              percentPnl=0.0, cashPnl=0.0):
    return {
        "eventSlug": slug, "outcome": outcome, "title": title,
        "curPrice": curPrice, "currentValue": currentValue,
        "redeemable": redeemable, "percentPnl": percentPnl, "cashPnl": cashPnl,
    }


def _trader_data(wallet="0xWALLET1", name="traderA", positions=None,
                  all_positions=None, verified=True):
    return {
        name: {
            "address": wallet,
            "monthly_pnl": 100000,
            "positions": positions if positions is not None else [],
            "all_positions": all_positions if all_positions is not None else (positions or []),
            "verified": verified,
            "fail_reason": None if verified else "unverified",
            "stats": {},
        }
    }


# ─── record_smart_money_fills ──────────────────────────────────────────────

def test_record_inserts_new_position(tmp_db):
    data = _trader_data(positions=[_position()])
    result = logger.record_smart_money_fills(data)
    assert result == {"inserted": 1, "refreshed": 0}

    with logger._db() as conn:
        row = conn.execute("SELECT * FROM smart_money_fills").fetchone()
    assert row["wallet"] == "0xWALLET1"
    assert row["trader_name"] == "traderA"
    assert row["poly_slug"] == "event-a"
    assert row["outcome"] == "Yes"
    assert row["resolved"] == 0
    assert row["entry_price"] == 0.60
    assert row["position_val"] == 500.0
    assert row["first_seen_at"] == row["last_seen_at"]


def test_record_refreshes_existing_position_without_duplicating(tmp_db):
    data1 = _trader_data(positions=[_position(currentValue=500.0)])
    logger.record_smart_money_fills(data1)

    data2 = _trader_data(positions=[_position(currentValue=750.0)])
    result = logger.record_smart_money_fills(data2)
    assert result == {"inserted": 0, "refreshed": 1}

    with logger._db() as conn:
        rows = conn.execute("SELECT * FROM smart_money_fills").fetchall()
    assert len(rows) == 1
    assert rows[0]["position_val"] == 750.0


def test_record_entry_price_never_overwritten_on_refresh(tmp_db):
    """
    entry_price approximates the fill price via first observation --
    refreshing an already-open position must not silently replace it with
    today's current price.
    """
    logger.record_smart_money_fills(_trader_data(positions=[_position(curPrice=0.40)]))
    logger.record_smart_money_fills(_trader_data(positions=[_position(curPrice=0.85)]))

    with logger._db() as conn:
        row = conn.execute("SELECT entry_price FROM smart_money_fills").fetchone()
    assert row["entry_price"] == 0.40


def test_record_skips_unverified_traders(tmp_db):
    data = _trader_data(positions=[_position()], verified=False)
    result = logger.record_smart_money_fills(data)
    assert result == {"inserted": 0, "refreshed": 0}
    with logger._db() as conn:
        n = conn.execute("SELECT count(*) AS n FROM smart_money_fills").fetchone()["n"]
    assert n == 0


def test_record_skips_position_missing_slug_or_outcome(tmp_db):
    bad = _position()
    bad["eventSlug"] = ""
    bad["slug"] = ""
    data = _trader_data(positions=[bad])
    result = logger.record_smart_money_fills(data)
    assert result == {"inserted": 0, "refreshed": 0}


def test_record_two_different_wallets_both_persisted(tmp_db):
    data = {**_trader_data(wallet="0xA", name="a", positions=[_position(slug="e1")]),
            **_trader_data(wallet="0xB", name="b", positions=[_position(slug="e2")])}
    result = logger.record_smart_money_fills(data)
    assert result == {"inserted": 2, "refreshed": 0}


# ─── backfill_smart_money_resolutions ──────────────────────────────────────

def test_backfill_marks_resolved_with_hit(tmp_db):
    logger.record_smart_money_fills(_trader_data(positions=[_position(slug="e1", outcome="Yes")]))

    resolved_pos = _position(slug="e1", outcome="Yes", redeemable=True,
                              percentPnl=42.0, cashPnl=210.0)
    data = _trader_data(positions=[], all_positions=[resolved_pos])
    result = logger.backfill_smart_money_resolutions(data)
    assert result == {"resolved": 1}

    with logger._db() as conn:
        row = conn.execute("SELECT * FROM smart_money_fills").fetchone()
    assert row["resolved"] == 1
    assert row["hit"] == 1
    assert row["resolved_pct_pnl"] == 42.0
    assert row["resolved_cash_pnl"] == 210.0
    assert row["resolved_at"] is not None


def test_backfill_marks_resolved_with_miss(tmp_db):
    logger.record_smart_money_fills(_trader_data(positions=[_position(slug="e1", outcome="Yes")]))
    resolved_pos = _position(slug="e1", outcome="Yes", redeemable=True, percentPnl=-100.0)
    result = logger.backfill_smart_money_resolutions(
        _trader_data(positions=[], all_positions=[resolved_pos])
    )
    assert result == {"resolved": 1}
    with logger._db() as conn:
        row = conn.execute("SELECT hit FROM smart_money_fills").fetchone()
    assert row["hit"] == 0


def test_backfill_never_infers_resolution_from_absence(tmp_db):
    """
    A fill missing entirely from this scan's all_positions (API pagination
    cap, temporarily dropped, etc.) must stay unresolved -- absence is not
    evidence of resolution, only a positive redeemable=True observation is.
    """
    logger.record_smart_money_fills(_trader_data(positions=[_position(slug="e1", outcome="Yes")]))
    result = logger.backfill_smart_money_resolutions(
        _trader_data(positions=[], all_positions=[])  # e1/Yes absent entirely
    )
    assert result == {"resolved": 0}
    with logger._db() as conn:
        row = conn.execute("SELECT resolved FROM smart_money_fills").fetchone()
    assert row["resolved"] == 0


def test_backfill_ignores_still_open_positions(tmp_db):
    logger.record_smart_money_fills(_trader_data(positions=[_position(slug="e1", outcome="Yes")]))
    still_open = _position(slug="e1", outcome="Yes", redeemable=False)
    result = logger.backfill_smart_money_resolutions(
        _trader_data(positions=[], all_positions=[still_open])
    )
    assert result == {"resolved": 0}


def test_backfill_skips_unverified_traders(tmp_db):
    logger.record_smart_money_fills(_trader_data(positions=[_position(slug="e1", outcome="Yes")]))
    resolved_pos = _position(slug="e1", outcome="Yes", redeemable=True, percentPnl=10.0)
    result = logger.backfill_smart_money_resolutions(
        _trader_data(positions=[], all_positions=[resolved_pos], verified=False)
    )
    assert result == {"resolved": 0}


# ─── get_resolved_count_per_wallet_max ──────────────────────────────────────

def test_wallet_max_zero_when_table_empty(tmp_db):
    assert logger.get_resolved_count_per_wallet_max() == 0


def test_wallet_max_ignores_unresolved_fills(tmp_db):
    logger.record_smart_money_fills(_trader_data(positions=[_position(slug="e1", outcome="Yes")]))
    assert logger.get_resolved_count_per_wallet_max() == 0


def test_wallet_max_returns_real_max_across_wallets(tmp_db):
    # Wallet A: 3 resolved fills. Wallet B: 1 resolved fill.
    for i in range(3):
        slug = f"a-event-{i}"
        logger.record_smart_money_fills(
            _trader_data(wallet="0xA", name="a", positions=[_position(slug=slug, outcome="Yes")])
        )
        logger.backfill_smart_money_resolutions(
            _trader_data(wallet="0xA", name="a", positions=[],
                          all_positions=[_position(slug=slug, outcome="Yes", redeemable=True, percentPnl=5.0)])
        )
    logger.record_smart_money_fills(
        _trader_data(wallet="0xB", name="b", positions=[_position(slug="b-event", outcome="No")])
    )
    logger.backfill_smart_money_resolutions(
        _trader_data(wallet="0xB", name="b", positions=[],
                      all_positions=[_position(slug="b-event", outcome="No", redeemable=True, percentPnl=-5.0)])
    )
    assert logger.get_resolved_count_per_wallet_max() == 3
