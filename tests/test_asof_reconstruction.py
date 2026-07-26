"""
tests/test_asof_reconstruction.py — Offline tests for
backtesting/asof_reconstruction.py (replay-asof-reconstruction).

All tests use a throwaway SQLite file and throwaway snapshot files under
tmp_path — never touches leviathan.db or data/snapshots. Kalshi HTTP calls
(the candlestick tier) are monkeypatched — no network calls.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from backtesting import asof_reconstruction as asof
from backtesting.settled_fetcher import _init_table


@pytest.fixture()
def tmp_db(tmp_path):
    return str(tmp_path / "test_asof.db")


@pytest.fixture(autouse=True)
def _isolate_snapshots_dir(tmp_path, monkeypatch):
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    monkeypatch.setattr(asof, "SNAPSHOTS_DIR", str(snap_dir))
    return snap_dir


def _write_snapshot(snap_dir, filename, fetched_at, markets):
    path = snap_dir / filename
    path.write_text(json.dumps({
        "header": {"fetched_at": fetched_at, "environment": "prod",
                   "event_count": 0, "market_count": len(markets)},
        "markets": markets,
    }), encoding="utf-8")


def _snapshot_market(ticker, **overrides):
    m = {
        "ticker": ticker,
        "event_ticker": "EVT-1",
        "series_ticker": "SER",
        "title": f"Market {ticker}",
        "close_time": "2026-08-01T00:00:00Z",
        "yes_bid_dollars": "0.3000",
        "yes_ask_dollars": "0.3500",
        "last_price_dollars": "0.3200",
        "previous_price_dollars": "0.3100",
        "volume_fp": "1000.00",
        "open_interest_fp": "500.00",
    }
    m.update(overrides)
    return m


def _insert_settled(db_path, ticker, **overrides):
    _init_table(db_path)
    row = {
        "ticker": ticker, "event_ticker": "EVT-1", "series_ticker": "SER",
        "category": "Politics", "title": f"Market {ticker}", "result": "YES",
        "close_time": "2026-05-01T00:00:00Z", "settlement_ts": "2026-05-01T00:05:00Z",
        "volume": 100.0, "open_interest": 50.0, "last_price": 0.9,
        "fetched_at": "2026-07-25T00:00:00Z",
    }
    row.update(overrides)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO settled_markets "
        "(ticker, event_ticker, series_ticker, category, title, result, close_time, "
        " settlement_ts, volume, open_interest, last_price, fetched_at) "
        "VALUES (:ticker, :event_ticker, :series_ticker, :category, :title, :result, "
        ":close_time, :settlement_ts, :volume, :open_interest, :last_price, :fetched_at)",
        row,
    )
    conn.commit()
    conn.close()


CONFIG = {"markets": {}, "betting": {"unit_size": 10}}


# ─── snapshot index ───────────────────────────────────────────────────────

def test_load_snapshot_index_empty_when_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(asof, "SNAPSHOTS_DIR", str(tmp_path / "nonexistent"))
    assert asof._load_snapshot_index() == []


def test_load_snapshot_index_sorted_ascending(_isolate_snapshots_dir):
    _write_snapshot(_isolate_snapshots_dir, "b.json", "2026-06-02T00:00:00+00:00", [])
    _write_snapshot(_isolate_snapshots_dir, "a.json", "2026-06-01T00:00:00+00:00", [])
    index = asof._load_snapshot_index()
    assert [Path(p).name for _, p in index] == ["a.json", "b.json"]


def test_load_snapshot_index_skips_malformed_file(_isolate_snapshots_dir):
    (_isolate_snapshots_dir / "bad.json").write_text("not json", encoding="utf-8")
    _write_snapshot(_isolate_snapshots_dir, "good.json", "2026-06-01T00:00:00+00:00", [])
    index = asof._load_snapshot_index()
    assert len(index) == 1


def test_load_snapshot_index_skips_valid_json_wrong_top_level_type(_isolate_snapshots_dir):
    """
    Regression guard: a bare JSON array (valid JSON, wrong shape -- e.g.
    analysis/resolve_first.py's snapshot fallback wrote exactly this
    before it was fixed to use the shared {"header":...,"markets":...}
    envelope) must be skipped, not crash the whole index with an
    AttributeError from calling .get() on a list.
    """
    import json as _json
    (_isolate_snapshots_dir / "bare_list.json").write_text(
        _json.dumps([{"ticker": "T1"}]), encoding="utf-8",
    )
    _write_snapshot(_isolate_snapshots_dir, "good.json", "2026-06-01T00:00:00+00:00", [])
    index = asof._load_snapshot_index()
    assert len(index) == 1
    assert Path(index[0][1]).name == "good.json"


# ─── exact tier: snapshot lookup ──────────────────────────────────────────

def test_find_snapshot_market_picks_latest_at_or_before(_isolate_snapshots_dir):
    _write_snapshot(_isolate_snapshots_dir, "early.json", "2026-06-01T00:00:00+00:00",
                     [_snapshot_market("T1", last_price_dollars="0.1000")])
    _write_snapshot(_isolate_snapshots_dir, "late.json", "2026-06-10T00:00:00+00:00",
                     [_snapshot_market("T1", last_price_dollars="0.9000")])
    hit = asof._find_snapshot_market("T1", asof._parse_dt("2026-06-15"))
    market, source = hit
    assert market["last_price_dollars"] == "0.9000"
    assert source == "late.json"


def test_find_snapshot_market_ignores_snapshots_after_as_of(_isolate_snapshots_dir):
    _write_snapshot(_isolate_snapshots_dir, "future.json", "2026-07-01T00:00:00+00:00",
                     [_snapshot_market("T1")])
    hit = asof._find_snapshot_market("T1", asof._parse_dt("2026-06-01"))
    assert hit is None


def test_find_snapshot_market_none_when_ticker_absent(_isolate_snapshots_dir):
    _write_snapshot(_isolate_snapshots_dir, "s.json", "2026-06-01T00:00:00+00:00",
                     [_snapshot_market("OTHER")])
    hit = asof._find_snapshot_market("T1", asof._parse_dt("2026-06-15"))
    assert hit is None


# ─── static metadata ──────────────────────────────────────────────────────

def test_static_metadata_prefers_settled_markets(tmp_db, _isolate_snapshots_dir):
    _insert_settled(tmp_db, "T1", category="Economics")
    meta = asof._static_metadata("T1", tmp_db)
    assert meta["category"] == "Economics"


def test_static_metadata_falls_back_to_snapshot_merge(tmp_db, _isolate_snapshots_dir):
    _init_table(tmp_db)
    _write_snapshot(_isolate_snapshots_dir, "old.json", "2026-06-01T00:00:00+00:00",
                     [_snapshot_market("T1", series_ticker="")])
    _write_snapshot(_isolate_snapshots_dir, "new.json", "2026-06-10T00:00:00+00:00",
                     [_snapshot_market("T1", series_ticker="SER-NEW")])
    meta = asof._static_metadata("T1", tmp_db)
    assert meta["series_ticker"] == "SER-NEW"


def test_static_metadata_none_for_unknown_ticker(tmp_db, _isolate_snapshots_dir):
    _init_table(tmp_db)
    assert asof._static_metadata("NOPE", tmp_db) is None


# ─── candlestick tier ──────────────────────────────────────────────────────

def _candle(end_ts, close="0.5000"):
    c = {"end_period_ts": end_ts, "volume_fp": "10.00", "open_interest_fp": "5.00",
         "yes_bid": {"close_dollars": close}, "yes_ask": {"close_dollars": close}}
    if close is not None:
        c["price"] = {"close_dollars": close, "previous_dollars": close}
    else:
        c["price"] = {}
    return c


def test_find_candlestick_picks_latest_at_or_before_as_of(monkeypatch):
    import datetime as _dt
    candles = [_candle(1000000000), _candle(1000086400), _candle(1000172800)]
    monkeypatch.setattr(asof.kalshi, "fetch_market_candlesticks",
                        lambda *a, **k: list(candles))
    as_of = _dt.datetime.fromtimestamp(1000086400, tz=_dt.timezone.utc)
    picked = asof._find_candlestick(CONFIG, "SER", "T1", as_of)
    assert picked["end_period_ts"] == 1000086400


def test_find_candlestick_never_selects_a_candle_ending_after_as_of(monkeypatch):
    """
    Regression guard: a candle whose period ends after as_of_dt would leak
    look-ahead price data into a "historical" reconstruction — confirmed as
    a real bug against a live ticker before this fix (see module docstring).
    """
    import datetime as _dt
    candles = [_candle(1000000000), _candle(1000086400), _candle(1000172800)]
    monkeypatch.setattr(asof.kalshi, "fetch_market_candlesticks",
                        lambda *a, **k: list(candles))
    as_of = _dt.datetime.fromtimestamp(1000000001, tz=_dt.timezone.utc)  # 1s after the first candle
    picked = asof._find_candlestick(CONFIG, "SER", "T1", as_of)
    assert picked["end_period_ts"] == 1000000000


def test_find_candlestick_falls_back_to_latest_when_all_before(monkeypatch):
    import datetime as _dt
    candles = [_candle(1000000000), _candle(1000086400)]
    monkeypatch.setattr(asof.kalshi, "fetch_market_candlesticks",
                        lambda *a, **k: list(candles))
    as_of = _dt.datetime.fromtimestamp(2000000000, tz=_dt.timezone.utc)
    picked = asof._find_candlestick(CONFIG, "SER", "T1", as_of)
    assert picked["end_period_ts"] == 1000086400


def test_find_candlestick_skips_candles_with_no_price_data(monkeypatch):
    """
    A no-trade day's candle has price.close_dollars missing (confirmed on a
    real thin market). Silently accepting it would flow None into
    scanner.score_market()'s `float(x or 0)` coercions, indistinguishable
    from a genuine zero price — must be skipped in favor of an earlier
    candle that has real data.
    """
    import datetime as _dt
    candles = [_candle(1000000000), _candle(1000086400, close=None)]
    monkeypatch.setattr(asof.kalshi, "fetch_market_candlesticks",
                        lambda *a, **k: list(candles))
    as_of = _dt.datetime.fromtimestamp(1000086400, tz=_dt.timezone.utc)
    picked = asof._find_candlestick(CONFIG, "SER", "T1", as_of)
    assert picked["end_period_ts"] == 1000000000


def test_find_candlestick_none_when_no_candle_has_price_data(monkeypatch):
    import datetime as _dt
    candles = [_candle(1000000000, close=None)]
    monkeypatch.setattr(asof.kalshi, "fetch_market_candlesticks",
                        lambda *a, **k: list(candles))
    as_of = _dt.datetime.fromtimestamp(1000000000, tz=_dt.timezone.utc)
    assert asof._find_candlestick(CONFIG, "SER", "T1", as_of) is None


def test_find_candlestick_none_when_empty(monkeypatch):
    import datetime as _dt
    monkeypatch.setattr(asof.kalshi, "fetch_market_candlesticks", lambda *a, **k: [])
    as_of = _dt.datetime.fromtimestamp(1000000000, tz=_dt.timezone.utc)
    assert asof._find_candlestick(CONFIG, "SER", "T1", as_of) is None


def test_find_candlestick_none_on_exception(monkeypatch):
    import datetime as _dt

    def _raise(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(asof.kalshi, "fetch_market_candlesticks", _raise)
    as_of = _dt.datetime.fromtimestamp(1000000000, tz=_dt.timezone.utc)
    assert asof._find_candlestick(CONFIG, "SER", "T1", as_of) is None


# ─── reconstruct_market_state: full integration ───────────────────────────

def test_reconstruct_returns_none_for_unknown_ticker(tmp_db, _isolate_snapshots_dir):
    _init_table(tmp_db)
    result = asof.reconstruct_market_state(CONFIG, "GHOST", "2026-06-01", tmp_db)
    assert result is None


def test_reconstruct_exact_tier_end_to_end(tmp_db, _isolate_snapshots_dir):
    _init_table(tmp_db)
    _write_snapshot(_isolate_snapshots_dir, "s.json", "2026-06-10T00:00:00+00:00",
                     [_snapshot_market("T1")])
    result = asof.reconstruct_market_state(CONFIG, "T1", "2026-06-15", tmp_db)
    assert result is not None
    assert result["reconstruction_tier"] == "exact"
    assert result["reconstruction_source"] == "s.json"
    assert result["ticker"] == "T1"
    assert "mid_price" in result  # confirms scanner.score_market() actually ran


def test_reconstruct_approximate_tier_end_to_end(tmp_db, _isolate_snapshots_dir, monkeypatch):
    _insert_settled(tmp_db, "T1")
    candle = {
        "end_period_ts": 1780000000,
        "open_interest_fp": "10.00",
        "volume_fp": "20.00",
        "price": {"close_dollars": "0.4000", "previous_dollars": "0.3900"},
        "yes_bid": {"close_dollars": "0.3900"},
        "yes_ask": {"close_dollars": "0.4100"},
    }
    monkeypatch.setattr(asof.kalshi, "fetch_market_candlesticks",
                        lambda *a, **k: [candle])
    import datetime as _dt
    as_of = _dt.datetime.fromtimestamp(1780000000, tz=_dt.timezone.utc)
    result = asof.reconstruct_market_state(CONFIG, "T1", as_of, tmp_db)
    assert result is not None
    assert result["reconstruction_tier"] == "approximate"
    assert result["reconstruction_source"] == "kalshi_candlestick"
    assert result["yes_bid_dollars"] == "0.3900"
    assert result["yes_ask_dollars"] == "0.4100"


def test_reconstruct_none_when_neither_tier_has_data(tmp_db, _isolate_snapshots_dir, monkeypatch):
    _insert_settled(tmp_db, "T1")
    monkeypatch.setattr(asof.kalshi, "fetch_market_candlesticks", lambda *a, **k: [])
    result = asof.reconstruct_market_state(CONFIG, "T1", "2026-06-01", tmp_db)
    assert result is None


def test_reconstruct_sets_time_horizon_from_as_of_not_wall_clock(tmp_db, _isolate_snapshots_dir):
    _init_table(tmp_db)
    _write_snapshot(_isolate_snapshots_dir, "s.json", "2026-06-01T00:00:00+00:00",
                     [_snapshot_market("T1", close_time="2026-06-05T00:00:00Z")])
    result = asof.reconstruct_market_state(CONFIG, "T1", "2026-06-02", tmp_db)
    # 3 days between as_of (06-02) and close (06-05) -> WEEKLY bucket,
    # regardless of how far today's real wall-clock date is from either.
    assert result["time_horizon"] == "WEEKLY"


def test_reconstruct_exact_tier_wins_over_approximate_when_both_available(
    tmp_db, _isolate_snapshots_dir, monkeypatch,
):
    _init_table(tmp_db)
    _write_snapshot(_isolate_snapshots_dir, "s.json", "2026-06-01T00:00:00+00:00",
                     [_snapshot_market("T1", yes_bid_dollars="0.5000")])

    def _fail(*a, **k):
        raise AssertionError("candlestick tier should not be reached when snapshot has the ticker")

    monkeypatch.setattr(asof.kalshi, "fetch_market_candlesticks", _fail)
    result = asof.reconstruct_market_state(CONFIG, "T1", "2026-06-03", tmp_db)
    assert result["reconstruction_tier"] == "exact"
    assert result["yes_bid_dollars"] == "0.5000"
