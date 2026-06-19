"""
Offline tests for the research probe experiment.

All tests use tmp_db (never leviathan.db) and mock the Claude CLI +
Kalshi API — no network, no subprocess calls.
"""

import uuid
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from core import logger
from core import scanner
from analysis import research_probe


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=False)
def tmp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(logger, "DB_PATH", db_file)
    logger._init_db()
    return db_file


def _close(days_out: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days_out)).isoformat()


def _raw_market(ticker="TEST", volume=1000, mid=0.40, days_out=30):
    """Minimal raw market dict (pre-filter, as it comes from the snapshot)."""
    half_spread = 0.01
    return {
        "ticker":           ticker,
        "title":            f"Will {ticker} happen",
        "yes_bid_dollars":  str(round(mid - half_spread, 4)),
        "yes_ask_dollars":  str(round(mid + half_spread, 4)),
        "volume_fp":        str(volume),
        "close_time":       _close(days_out),
        "last_price_dollars": str(mid),
    }


def _filter_cfg():
    return {
        "markets": {
            "min_volume": 500,
            "max_volume_filter": 150_000,
            "min_market_price": 0.05,
            "max_market_price": 0.95,
            "min_days_to_close": 0,
            "max_days_to_close": 180,
            "edge_threshold": 0.08,
            "drift_min_abs": 0.0,
            "drift_min_pct": 0.05,
            "bucket_min_volume": {"INTRADAY": 50, "WEEKLY": 150, "MONTHLY": 100,
                                   "QUARTERLY": 50, "LONG": 50},
            "efficient_market_keywords": ["CPI"],
            "categories": [],
        }
    }


# ─── Part A: stratified_sample ────────────────────────────────────────────────

def test_stratified_sample_includes_filter_rejects():
    """Sample must contain markets that filter_markets would reject."""
    # Build a mix: some pass filter, some fail (volume < 500)
    good   = [_raw_market(ticker=f"GOOD-{i}", volume=2000) for i in range(20)]
    thin   = [_raw_market(ticker=f"THIN-{i}", volume=50)   for i in range(20)]
    markets = good + thin

    sample = research_probe.stratified_sample(markets, target_n=20, filter_cfg=_filter_cfg())

    filter_rejects = [m for m in sample if not m.get("filter_pass")]
    assert len(filter_rejects) > 0, "Sample must include at least one filter reject"


def test_stratified_sample_respects_target_n():
    """Sample must not exceed target_n."""
    markets = [_raw_market(ticker=f"M-{i}", volume=i * 100) for i in range(1, 200)]
    sample  = research_probe.stratified_sample(markets, target_n=30, filter_cfg=_filter_cfg())
    assert len(sample) <= 30


def test_stratified_sample_annotates_filter_pass():
    """Every sampled market must have filter_pass bool set."""
    markets = [_raw_market(ticker=f"M-{i}", volume=1000) for i in range(20)]
    sample  = research_probe.stratified_sample(markets, target_n=10, filter_cfg=_filter_cfg())
    for m in sample:
        assert "filter_pass" in m
        assert isinstance(m["filter_pass"], bool)


def test_stratified_sample_covers_multiple_volume_tiers():
    """With markets spanning all tiers, multiple tiers should be represented."""
    markets = (
        [_raw_market(ticker=f"THIN-{i}",   volume=50)      for i in range(15)] +
        [_raw_market(ticker=f"LIGHT-{i}",  volume=1000)    for i in range(15)] +
        [_raw_market(ticker=f"MED-{i}",    volume=10_000)  for i in range(15)] +
        [_raw_market(ticker=f"HEAVY-{i}",  volume=80_000)  for i in range(15)] +
        [_raw_market(ticker=f"LIQUID-{i}", volume=200_000) for i in range(15)]
    )
    sample = research_probe.stratified_sample(markets, target_n=40, filter_cfg=_filter_cfg())
    tiers  = {m["vol_tier"] for m in sample}
    assert len(tiers) >= 3, f"Expected coverage of >= 3 tiers, got {tiers}"


def test_stratified_sample_max_days_to_close_excludes_far_markets():
    """Markets closing beyond max_days_to_close must be excluded before sampling."""
    near = [_raw_market(ticker=f"NEAR-{i}", volume=1000, days_out=30)  for i in range(10)]
    far  = [_raw_market(ticker=f"FAR-{i}",  volume=1000, days_out=500) for i in range(10)]
    sample = research_probe.stratified_sample(
        near + far, target_n=20, max_days_to_close=180
    )
    tickers = {m["ticker"] for m in sample}
    assert all(t.startswith("NEAR") for t in tickers), "Far markets must be excluded"
    assert not any(t.startswith("FAR") for t in tickers)


# ─── Part B/C: probe rows in DB ───────────────────────────────────────────────

def _mock_probe_result(ticker="KXTEST", market_price=0.30, estimate=0.50):
    divergence = round(estimate - market_price, 4)
    return {
        "ticker":                ticker,
        "title":                 f"Will {ticker} happen",
        "market_price_at_probe": market_price,
        "claude_estimate":       estimate,
        "divergence":            divergence,
        "predicted_direction":   "YES" if divergence > 0.03 else ("NO" if divergence < -0.03 else "PASS"),
        "confidence":            "MED",
        "rationale":             "Test rationale.",
        "runtime_ms":            1200,
        "filter_pass":           False,
        "vol_tier":              "thin     (<500)",
    }


def test_log_probe_inserts_research_probe_row(tmp_db):
    """log_probe must write source='research_probe' and segment='research_probe'."""
    logger.log_probe(_mock_probe_result())

    with logger._db() as conn:
        row = conn.execute(
            "SELECT source, segment, claude_estimate, divergence, predicted_direction "
            "FROM signals WHERE source='research_probe'"
        ).fetchone()

    assert row is not None
    assert row["source"]             == "research_probe"
    assert row["segment"]            == "research_probe"
    assert row["claude_estimate"]    == pytest.approx(0.50)
    assert row["divergence"]         == pytest.approx(0.20)
    assert row["predicted_direction"] == "YES"


def test_probe_rows_excluded_from_paper_stats(tmp_db):
    """get_stats() must never count research_probe rows."""
    # Insert a paper signal and a probe row
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals (call_id, timestamp, ticker, direction, market_price,
                                outcome, result, pnl_if_traded, source)
            VALUES ('p1','2026-06-01T00:00:00Z','PAPER','YES',0.30,'YES','WIN',0.70,'paper')
        """)
    logger.log_probe(_mock_probe_result(ticker="PROBE"))

    stats = logger.get_stats()
    assert stats["total_calls"] == 1   # only the paper row


def test_probe_rows_excluded_from_real_fill_stats(tmp_db):
    """get_stats_real() must never count research_probe rows."""
    logger.log_probe(_mock_probe_result(ticker="PROBE"))
    stats_r = logger.get_stats_real()
    assert stats_r["total_fills"] == 0


# ─── Part D: forward scoring ──────────────────────────────────────────────────

def _insert_probe_direct(call_id, ticker, market_price, estimate, direction, outcome=""):
    """Insert a probe row directly for resolution tests."""
    divergence = round(estimate - market_price, 4)
    with logger._db() as conn:
        conn.execute("""
            INSERT INTO signals
            (call_id, timestamp, ticker, direction, market_price,
             market_price_at_probe, claude_estimate, divergence, predicted_direction,
             source, segment, outcome, result)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            call_id, "2026-06-15T10:00:00Z", ticker,
            direction, market_price, market_price, estimate, divergence, direction,
            "research_probe", "research_probe", outcome, "",
        ))


def test_resolved_probe_direction_correct_scores_win(tmp_db):
    """Probe predicted YES, market resolved YES → result=WIN."""
    _insert_probe_direct("probe-1", "KXTEST", 0.30, 0.55, "YES")

    with patch("core.kalshi.fetch_market", return_value={"result": "yes"}):
        count = logger.resolve_outcomes({})

    assert count == 1
    with logger._db() as conn:
        row = conn.execute("SELECT result FROM signals WHERE call_id='probe-1'").fetchone()
    assert row["result"] == "WIN"


def test_resolved_probe_direction_wrong_scores_loss(tmp_db):
    """Probe predicted YES, market resolved NO → result=LOSS."""
    _insert_probe_direct("probe-2", "KXTEST", 0.30, 0.55, "YES")

    with patch("core.kalshi.fetch_market", return_value={"result": "no"}):
        logger.resolve_outcomes({})

    with logger._db() as conn:
        row = conn.execute("SELECT result FROM signals WHERE call_id='probe-2'").fetchone()
    assert row["result"] == "LOSS"


def test_unresolved_probe_stays_pending(tmp_db):
    """Market not yet settled → probe stays unresolved."""
    _insert_probe_direct("probe-open", "KXJULY", 0.40, 0.60, "YES")

    with patch("core.kalshi.fetch_market", return_value={"result": ""}):
        count = logger.resolve_outcomes({})

    assert count == 0


def test_get_stats_probe_pending_verdict(tmp_db):
    """Before any resolutions, verdict must say PENDING."""
    logger.log_probe(_mock_probe_result(ticker="PENDING-MKT"))
    stats = logger.get_stats_probe()
    assert stats["total_probes"] == 1
    assert stats["resolved"]     == 0
    assert stats["hit_rate"]     is None
    assert "PENDING" in stats["verdict"]


def test_get_stats_probe_high_divergence_subset(tmp_db):
    """High-divergence subset (|div| >= 0.10) computed correctly."""
    # High-div probe (|div|=0.25)
    _insert_probe_direct("hi-1", "KXHI", 0.30, 0.55, "YES", outcome="YES")
    with logger._db() as conn:
        conn.execute("UPDATE signals SET result='WIN' WHERE call_id='hi-1'")
    # Low-div probe (|div|=0.04)
    _insert_probe_direct("lo-1", "KXLO", 0.50, 0.54, "YES", outcome="YES")
    with logger._db() as conn:
        conn.execute("UPDATE signals SET result='WIN' WHERE call_id='lo-1'")

    stats = logger.get_stats_probe(high_divergence_threshold=0.10)
    assert stats["hi_div_total"]    == 1   # only hi-1 qualifies
    assert stats["hi_div_resolved"] == 1
    assert stats["hi_div_hit_rate"] == pytest.approx(100.0)


# ─── Cap: max_probe_markets ───────────────────────────────────────────────────

def test_run_probe_respects_max_probe_cap(tmp_db):
    """run_probe must stop at max_probe_markets regardless of sample size."""
    markets = [_raw_market(ticker=f"M{i}", volume=1000 + i * 100) for i in range(30)]

    call_count = []

    def fake_probe(market, config):
        call_count.append(market["ticker"])
        return _mock_probe_result(ticker=market["ticker"])

    cfg = _filter_cfg()
    cfg["scoring"] = {"max_probe_markets": 3}

    # Patch load_snapshot to return our markets
    with patch.object(research_probe, "probe_market", side_effect=fake_probe), \
         patch.object(research_probe, "load_snapshot", return_value=(markets, {"fetched_at": "test", "market_count": 30})):
        summary = research_probe.run_probe(cfg)

    assert summary["succeeded"] <= 3
    assert len(call_count)      <= 3
