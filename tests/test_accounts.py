"""
tests/test_accounts.py — Offline tests for accounts.py wallet-selection fix (Goal 2d PART D),
and for the 2026-09-08 true-resolution win/loss fix (see accounts.fetch_market_resolution).

All tests use synthesised position/resolution data only — no network calls.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from unittest.mock import patch

from sources.accounts import (
    _score_wallet, _is_winner, _is_coinflip, _classify_wallet,
    _distribution, diagnose_discovery, format_diagnostic_report,
    fetch_market_resolution, fetch_resolutions_for_positions,
    gate_checklist, get_wallet_profile, discover_winners,
    GATE_ORDER,
)


# ── helpers ───────────────────────────────────────────────────────────────────

_CID_COUNTER = [0]


def _pos(title: str, won: bool | None, realized: float = 0.0,
         initial: float = 100.0, size: float = 100.0, cid: str = None) -> dict:
    """
    Minimal resolved-shaped position dict + its resolution contribution.
    won=None means an open (non-redeemable) position — no resolution entry.
    Returns (position_dict, resolution_entry_or_None) where resolution_entry
    is (conditionId, {"Yes": won}).
    """
    if cid is None:
        _CID_COUNTER[0] += 1
        cid = f"cond{_CID_COUNTER[0]}"
    position = {
        "title":       title,
        "conditionId": cid,
        "outcome":     "Yes",
        "realizedPnl": realized,
        "initialValue": initial,
        "size":        size,
        "redeemable":  won is not None,
        "eventSlug":   "",
        # percentPnl still used for OPEN positions (active_markets) — irrelevant
        # to the payout math for won is not None, but harmless to include.
        "percentPnl":  0.0,
    }
    resolution_entry = (cid, {"Yes": won}) if won is not None else None
    return position, resolution_entry


def _build(specs: list[tuple]) -> tuple[list[dict], dict]:
    """specs: list of _pos(...) return values -> (positions, resolutions)."""
    positions = []
    resolutions = {}
    for position, resolution_entry in specs:
        positions.append(position)
        if resolution_entry:
            cid, res = resolution_entry
            resolutions[cid] = res
    return positions, resolutions


def _cfg(**overrides) -> dict:
    """Default qualifying config with optional overrides."""
    base = {
        "min_resolved_count": 10,
        "min_win_rate":       55.0,
        "min_positions":      5,
        "min_pct_pnl":        10.0,
        "min_cash_pnl":       100.0,
    }
    base.update(overrides)
    return {"accounts": base}


def _real_winners(n: int = 12, wins: int = 9) -> tuple[list[dict], dict]:
    """n resolved real-forecast positions; `wins` of them actually won."""
    specs = [
        _pos(f"Will Policy {i} happen?", i < wins,
             realized=0.0, initial=100.0, size=200.0 if i < wins else 100.0)
        for i in range(n)
    ]
    return _build(specs)


# ── PART D tests ──────────────────────────────────────────────────────────────

class TestLuckCaseExcluded(unittest.TestCase):
    """Core regression: open-position P&L does not qualify a wallet."""

    def test_open_position_high_pnl_does_not_qualify(self):
        """Wallet with 20 open positions (never resolved) is excluded (resolved_count=0)."""
        specs = [_pos(f"Open Market {i}", None) for i in range(20)]
        for p, _ in specs:
            p["percentPnl"] = 500.0
        positions, resolutions = _build(specs)
        stats = _score_wallet(positions, resolutions)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["resolved_count"], 0)
        self.assertFalse(_is_winner(stats, _cfg()))

    def test_coinflip_resolved_does_not_count(self):
        """A wallet whose entire resolved history is coinflip titles has resolved_count=0."""
        specs = [
            _pos("Bitcoin Up or Down 5m", True, realized=200.0, initial=100.0, size=300.0),
            _pos("Bitcoin Up or Down 1m", True, realized=100.0, initial=50.0, size=150.0),
            _pos("ETH up or down", False, realized=-50.0, initial=25.0, size=0.0),
        ]
        positions, resolutions = _build(specs)
        stats = _score_wallet(positions, resolutions)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["resolved_count"], 0,
                         "Coinflip resolved positions must not count toward track record")
        self.assertFalse(_is_winner(stats, _cfg()))

    def test_sports_game_resolved_does_not_count(self):
        """Positions whose titles match sports-game patterns are excluded from resolved scoring."""
        specs = [
            _pos("Will Germany win on 2026-06-25?", True, realized=150.0, initial=100.0, size=250.0),
            _pos("Will Brazil vs. Argentina end in a draw?", True, realized=80.0, initial=50.0, size=130.0),
            _pos("FIFA World Cup winner 2026", True, realized=120.0, initial=100.0, size=220.0),
        ]
        positions, resolutions = _build(specs)
        stats = _score_wallet(positions, resolutions)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["resolved_count"], 0,
                         "Sports-game resolved positions must not count toward track record")
        self.assertFalse(_is_winner(stats, _cfg()))


class TestVerifiedTrackRecordQualifies(unittest.TestCase):
    """Wallets with a real resolved track record pass the filter."""

    def test_twelve_resolved_real_positions_qualifies(self):
        """12 resolved non-coinflip positions with 75% win rate qualifies."""
        positions, resolutions = _real_winners(n=12, wins=9)
        stats = _score_wallet(positions, resolutions)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["resolved_count"], 12)
        self.assertAlmostEqual(stats["win_rate"], 75.0)
        self.assertTrue(_is_winner(stats, _cfg()))

    def test_below_resolved_count_threshold_excluded(self):
        """5 resolved positions (below threshold of 10) is excluded."""
        positions, resolutions = _real_winners(n=5, wins=4)
        stats = _score_wallet(positions, resolutions)
        self.assertEqual(stats["resolved_count"], 5)
        self.assertFalse(_is_winner(stats, _cfg()))

    def test_below_win_rate_threshold_excluded(self):
        """12 resolved positions with 40% win rate (below 55%) is excluded."""
        positions, resolutions = _real_winners(n=12, wins=5)
        stats = _score_wallet(positions, resolutions)
        self.assertEqual(stats["resolved_count"], 12)
        self.assertAlmostEqual(stats["win_rate"], round(5 / 12 * 100, 1))
        self.assertFalse(_is_winner(stats, _cfg()))

    def test_below_resolved_cash_pnl_threshold_excluded(self):
        """12 resolved positions with negative true cash PnL excluded even with high win rate."""
        specs = [
            _pos(f"Policy {i}", i < 9,
                 realized=1.0 if i < 9 else -1.0,
                 initial=100.0,
                 size=100.5 if i < 9 else 0.0)  # tiny win payout, big losses
            for i in range(12)
        ]
        positions, resolutions = _build(specs)
        stats = _score_wallet(positions, resolutions)
        self.assertEqual(stats["resolved_count"], 12)
        self.assertLess(stats["resolved_cash_pnl"], 100.0)
        self.assertFalse(_is_winner(stats, _cfg()))

    def test_unknown_resolution_excludes_position_not_misclassifies(self):
        """A resolved position with no matching entry in `resolutions` (fetch
        failed, or market genuinely unresolved per CLOB) is excluded from
        scoring entirely — never silently counted as a loss."""
        positions, resolutions = _real_winners(n=12, wins=9)
        # Drop one resolution entry — simulate a failed/missing CLOB fetch
        dropped_cid = next(iter(resolutions))
        del resolutions[dropped_cid]
        stats = _score_wallet(positions, resolutions)
        self.assertEqual(stats["resolved_count"], 11,
                         "Position with unknown true resolution must be excluded, not counted as a loss")

    def test_true_pnl_uses_realized_plus_payout_minus_cost_not_broken_fields(self):
        """
        Regression for the 2026-09-08 fix: true P&L = realizedPnl + payout -
        initialValue, where payout = size if won else 0 — NOT percentPnl/
        cashPnl (which read ~-100% for every resolved position regardless of
        outcome, per the live-confirmed Data API bug).
        """
        # A winner: bought 100 shares for $50 (avgPrice 0.50), sold none
        # (realizedPnl=0), held to resolution and won -> payout = size = 100.
        # True P&L = 0 + 100 - 50 = +50 (=100% pct).
        win_pos, win_res = _pos("Will X happen?", True, realized=0.0, initial=50.0, size=100.0)
        # A loser: same shape, but resolved False -> payout = 0.
        # True P&L = 0 + 0 - 50 = -50 (=-100% pct).
        lose_pos, lose_res = _pos("Will Y happen?", False, realized=0.0, initial=50.0, size=100.0)

        positions, resolutions = _build([(win_pos, win_res), (lose_pos, lose_res)])
        stats = _score_wallet(positions, resolutions)

        self.assertEqual(stats["resolved_count"], 2)
        self.assertAlmostEqual(stats["win_rate"], 50.0)
        self.assertAlmostEqual(stats["resolved_cash_pnl"], 0.0)  # +50 - 50
        self.assertAlmostEqual(stats["resolved_avg_pct_pnl"], 0.0)  # avg(100%, -100%)


class TestRankingOnResolvedMetrics(unittest.TestCase):
    """Ranking uses resolved win rate, not open-position avg_pct_pnl."""

    def test_ranking_prefers_higher_resolved_win_rate(self):
        """
        Wallet A: resolved win_rate=80%, higher resolved_cash_pnl
        Wallet B: resolved win_rate=60%, lower resolved_cash_pnl

        Ranking sorts on (win_rate, resolved_cash_pnl) — A ranks first.
        """
        specs_a = [
            _pos(f"Policy A{i}", i < 8, realized=0.0, initial=100.0,
                 size=160.0 if i < 8 else 80.0)
            for i in range(10)
        ]
        positions_a, resolutions_a = _build(specs_a)
        stats_a = _score_wallet(positions_a, resolutions_a)
        self.assertAlmostEqual(stats_a["win_rate"], 80.0)

        specs_b = [
            _pos(f"Policy B{i}", i < 6, realized=0.0, initial=100.0,
                 size=120.0 if i < 6 else 90.0)
            for i in range(10)
        ]
        positions_b, resolutions_b = _build(specs_b)
        stats_b = _score_wallet(positions_b, resolutions_b)
        self.assertAlmostEqual(stats_b["win_rate"], 60.0)

        wallets = [
            {"address": "A", **stats_a},
            {"address": "B", **stats_b},
        ]
        wallets.sort(
            key=lambda w: (w.get("win_rate") or 0, w.get("resolved_cash_pnl") or 0),
            reverse=True,
        )
        self.assertEqual(wallets[0]["address"], "A",
                         "Higher win_rate wallet should rank first (resolved metric wins)")

    def test_avg_pct_pnl_key_absent_only_resolved_variant_present(self):
        """Confirm the stats dict never exposes an all-positions avg_pct_pnl key."""
        specs = [_pos(f"Resolved {i}", True, realized=0.0, initial=50.0, size=100.0) for i in range(10)]
        positions, resolutions = _build(specs)
        stats = _score_wallet(positions, resolutions)
        self.assertNotIn("avg_pct_pnl", stats,
                         "avg_pct_pnl (all-positions) must not be in stats dict")
        self.assertIn("resolved_avg_pct_pnl", stats)


class TestEmptyWatchlistDoesNotCrash(unittest.TestCase):
    """Empty qualifying set produces an empty list without error."""

    def test_empty_positions_returns_none(self):
        stats = _score_wallet([])
        self.assertIsNone(stats)

    def test_no_resolutions_arg_defaults_to_empty(self):
        """resolutions=None (unset) must not crash — treated as no known resolutions."""
        positions, _ = _real_winners(n=3, wins=2)
        stats = _score_wallet(positions)  # no resolutions arg at all
        self.assertIsNotNone(stats)
        self.assertEqual(stats["resolved_count"], 0,
                         "With no resolutions supplied, every resolved position is excluded")

    def test_none_stats_is_not_winner(self):
        """_is_winner with None stats is safely handled by the caller check."""
        min_stats = {
            "resolved_count":      0,
            "win_rate":            None,
            "position_count":      3,
            "resolved_avg_pct_pnl": None,
            "resolved_cash_pnl":   0.0,
        }
        self.assertFalse(_is_winner(min_stats, _cfg()))

    def test_empty_winner_list_sort_does_not_crash(self):
        """Sorting an empty list of winners raises no error."""
        winners = []
        winners.sort(
            key=lambda w: (w.get("win_rate") or 0, w.get("resolved_cash_pnl") or 0),
            reverse=True,
        )
        self.assertEqual(winners, [])


class TestCoinflipPatterns(unittest.TestCase):
    """_is_coinflip correctly identifies tick-resolution markets."""

    def test_known_coinflip_patterns(self):
        for title in ["Bitcoin Up or Down 5m", "ETH up/down", "BTC Up 1m",
                      "Price up or down", "Higher or lower"]:
            self.assertTrue(_is_coinflip(title), f"Expected coinflip: {title}")

    def test_real_market_not_coinflip(self):
        for title in ["Will the Fed raise rates in June?",
                      "Will Biden win the 2024 election?",
                      "Will SpaceX launch by December?"]:
            self.assertFalse(_is_coinflip(title), f"False positive: {title}")

    def test_dollar_amount_not_coinflip(self):
        """$1M/$5M/$10M in a title must not trigger the minute-interval patterns."""
        for title in ["Will Bitcoin reach $1M by year end?",
                      "Will BTC reach $5M in 2027?",
                      "Will SpaceX raise $10M?"]:
            self.assertFalse(_is_coinflip(title), f"Dollar-amount false positive: {title}")


# ── Discovery diagnostic (funnel/gate instrumentation) ────────────────────────

def _stats(**over) -> dict:
    """All-gates-passing baseline stats dict, with overrides."""
    base = dict(resolved_count=20, win_rate=80.0, position_count=20,
                resolved_avg_pct_pnl=50.0, resolved_cash_pnl=500.0)
    base.update(over)
    return base


class TestClassifyWalletAttributesCorrectStage(unittest.TestCase):
    """_classify_wallet attributes a wallet to exactly the gate it fails."""

    def test_dies_at_resolved_count(self):
        stats = _stats(resolved_count=9)
        self.assertEqual(_classify_wallet(stats, _cfg()), "resolved_count")

    def test_dies_at_win_rate(self):
        stats = _stats(win_rate=54.9)
        self.assertEqual(_classify_wallet(stats, _cfg()), "win_rate")

    def test_dies_at_position_count(self):
        stats = _stats(position_count=4)
        self.assertEqual(_classify_wallet(stats, _cfg()), "position_count")

    def test_dies_at_pct_pnl(self):
        stats = _stats(resolved_avg_pct_pnl=9.9)
        self.assertEqual(_classify_wallet(stats, _cfg()), "pct_pnl")

    def test_dies_at_cash_pnl(self):
        stats = _stats(resolved_cash_pnl=99.9)
        self.assertEqual(_classify_wallet(stats, _cfg()), "cash_pnl")

    def test_no_other_stage_flagged(self):
        """A wallet dying at pct_pnl must not also register as dying elsewhere."""
        stats = _stats(resolved_avg_pct_pnl=9.9)
        result = _classify_wallet(stats, _cfg())
        for other in ("resolved_count", "win_rate", "position_count", "cash_pnl"):
            self.assertNotEqual(result, other)


class TestDiagnosticAgreesWithIsWinner(unittest.TestCase):
    """A wallet that passes every gate must be a WINNER in both, always."""

    def test_full_pass_is_winner_and_classified_pass(self):
        stats = _stats()
        self.assertEqual(_classify_wallet(stats, _cfg()), "PASS")
        self.assertTrue(_is_winner(stats, _cfg()))

    def test_classify_and_is_winner_never_disagree(self):
        """Across every boundary case, (classify == PASS) must equal is_winner()."""
        cases = [
            _stats(),
            _stats(resolved_count=10), _stats(resolved_count=9),
            _stats(win_rate=55.0), _stats(win_rate=54.9),
            _stats(position_count=5), _stats(position_count=4),
            _stats(resolved_avg_pct_pnl=10.0), _stats(resolved_avg_pct_pnl=9.9),
            _stats(resolved_cash_pnl=100.0), _stats(resolved_cash_pnl=99.9),
            dict(resolved_count=0, win_rate=None, position_count=3,
                 resolved_avg_pct_pnl=None, resolved_cash_pnl=0.0),
        ]
        for stats in cases:
            classified_pass = _classify_wallet(stats, _cfg()) == "PASS"
            self.assertEqual(classified_pass, _is_winner(stats, _cfg()),
                             f"Disagreement on {stats}")


class TestIsWinnerRegressionBoundaryBattery(unittest.TestCase):
    """
    REGRESSION: _is_winner's boolean output for 13 synthetic stats dicts
    spanning every gate boundary, captured from CURRENT (pre-diagnostic)
    behavior. A silent change to gate order or logic must fail this test.
    """

    CASES = [
        ("baseline_all_pass",                   _stats(),                                    True),
        ("resolved_count_at_min",               _stats(resolved_count=10),                   True),
        ("resolved_count_below_min",            _stats(resolved_count=9),                     False),
        ("win_rate_at_min",                     _stats(win_rate=55.0),                        True),
        ("win_rate_below_min",                  _stats(win_rate=54.9),                        False),
        ("position_count_at_min",               _stats(position_count=5),                     True),
        ("position_count_below_min",            _stats(position_count=4),                     False),
        ("pct_pnl_at_min",                       _stats(resolved_avg_pct_pnl=10.0),           True),
        ("pct_pnl_below_min",                    _stats(resolved_avg_pct_pnl=9.9),             False),
        ("cash_pnl_at_min",                      _stats(resolved_cash_pnl=100.0),              True),
        ("cash_pnl_below_min",                   _stats(resolved_cash_pnl=99.9),               False),
        ("zero_resolved_none_winrate",           dict(resolved_count=0, win_rate=None, position_count=3,
                                                       resolved_avg_pct_pnl=None, resolved_cash_pnl=0.0), False),
        ("high_resolved_none_winrate_defensive", dict(resolved_count=15, win_rate=None, position_count=20,
                                                       resolved_avg_pct_pnl=50.0, resolved_cash_pnl=500.0), False),
    ]

    def test_battery_matches_captured_expectations(self):
        self.assertGreaterEqual(len(self.CASES), 12)
        for name, stats, expected in self.CASES:
            with self.subTest(name=name):
                self.assertEqual(_is_winner(stats, _cfg()), expected,
                                 f"{name}: expected {expected}")


class TestDistributionHandlesNone(unittest.TestCase):
    """_distribution excludes None (e.g. win_rate=None) without crashing."""

    def test_none_excluded_and_counted(self):
        dist = _distribution([50.0, None, 60.0, None, 70.0])
        self.assertEqual(dist["excluded"], 2)
        self.assertEqual(dist["n"], 3)
        self.assertEqual(dist["min"], 50.0)
        self.assertEqual(dist["max"], 70.0)

    def test_all_none_does_not_crash(self):
        dist = _distribution([None, None])
        self.assertEqual(dist["n"], 0)
        self.assertEqual(dist["excluded"], 2)
        self.assertIsNone(dist["median"])

    def test_empty_list_does_not_crash(self):
        dist = _distribution([])
        self.assertEqual(dist["n"], 0)
        self.assertEqual(dist["excluded"], 0)


class TestDiagnoseDiscoveryEndToEnd(unittest.TestCase):
    """
    Full diagnose_discovery() run against a mocked/stubbed fetch layer —
    never the live API. Verifies the funnel table string contains every
    stage label and the winner count matches manual computation.
    """

    def setUp(self):
        # Built once so positions and their resolution entries share the
        # same conditionIds across every call in this test (regression: a
        # naive per-call rebuild would mint fresh conditionIds each time,
        # making positions and resolutions silently fail to line up).
        win_specs = [_pos(f"Policy {i}", True, realized=0.0, initial=100.0, size=160.0) for i in range(12)]
        low_specs = [_pos(f"Policy {i}", True, realized=0.0, initial=100.0, size=160.0) for i in range(3)]
        self.win_positions, win_res = _build(win_specs)
        self.low_positions, low_res = _build(low_specs)
        self.all_resolutions = {**win_res, **low_res}

    def _fake_trades(self, *_args, **_kwargs):
        return [
            {"proxyWallet": "0xWIN"},   # will pass every gate
            {"proxyWallet": "0xLOW"},   # will die at resolved_count
            {"proxyWallet": "0xNONE"},  # fetch_user_positions returns []
        ]

    def _fake_positions(self, address, *_args, **_kwargs):
        if address == "0xWIN":
            return self.win_positions
        if address == "0xLOW":
            return self.low_positions
        return []  # 0xNONE — no positions returned by the API

    def _fake_resolutions(self, positions, cache=None, fetch_new=True):
        """Stub for fetch_resolutions_for_positions — builds resolutions from
        the same fixtures directly instead of hitting CLOB."""
        return self.all_resolutions

    def test_end_to_end_with_stubbed_fetch(self):
        config = _cfg()
        config["accounts"]["discovery_sample_size"] = 300

        with patch("sources.accounts.fetch_recent_trades", side_effect=self._fake_trades), \
             patch("sources.accounts.fetch_user_positions", side_effect=self._fake_positions), \
             patch("sources.accounts.fetch_resolutions_for_positions", side_effect=self._fake_resolutions), \
             patch("sources.accounts._load_resolution_cache", return_value={}):
            result = diagnose_discovery(config)

        self.assertEqual(result["n_trades_fetched"], 3)
        self.assertEqual(result["n_winners"], 1)

        report = format_diagnostic_report(result)
        for label, _count in result["funnel"]:
            self.assertIn(label, report)
        self.assertIn("WINNERS: 1", report)

    def test_single_fetch_pass_per_wallet(self):
        """fetch_user_positions must be called exactly once per unique wallet."""
        config = _cfg()
        with patch("sources.accounts.fetch_recent_trades", side_effect=self._fake_trades), \
             patch("sources.accounts.fetch_user_positions", side_effect=self._fake_positions) as mock_pos, \
             patch("sources.accounts.fetch_resolutions_for_positions", side_effect=self._fake_resolutions), \
             patch("sources.accounts._load_resolution_cache", return_value={}):
            diagnose_discovery(config)
        self.assertEqual(mock_pos.call_count, 3)


# ── discover_winners() time budget (2026-09-09) ────────────────────────────────
# main.py's scheduled task has only a 10-minute ExecutionTimeLimit
# (scripts/setup_scheduler.ps1), and per-wallet scoring cost is highly
# variable and unbounded by sample_size alone -- see discover_winners()'s
# own docstring. discovery_time_budget_s must stop the loop early rather
# than risk hanging the entire daily pipeline.

class TestDiscoverWinnersTimeBudget(unittest.TestCase):

    def _fake_trades(self, *_args, **_kwargs):
        return [{"proxyWallet": f"0xW{i}"} for i in range(5)]

    def _fake_positions(self, address, *_args, **_kwargs):
        specs = [_pos(f"Policy {i}", True, realized=0.0, initial=100.0, size=160.0) for i in range(12)]
        positions, _ = _build(specs)
        return positions

    def _fake_resolutions(self, positions, cache=None, fetch_new=True):
        # Recompute matching resolutions fresh each call since _fake_positions
        # mints new conditionIds per call -- must line up 1:1 with what it just built.
        return {p["conditionId"]: {"Yes": True} for p in positions}

    def test_stops_early_once_budget_exceeded(self):
        from sources import accounts
        cfg = _cfg()
        cfg["accounts"]["discovery_time_budget_s"] = 120

        with patch("sources.accounts.fetch_recent_trades", side_effect=self._fake_trades), \
             patch("sources.accounts.fetch_user_positions", side_effect=self._fake_positions) as mock_pos, \
             patch("sources.accounts.fetch_resolutions_for_positions", side_effect=self._fake_resolutions), \
             patch("sources.accounts.fetch_user_trades", return_value=[]), \
             patch("sources.accounts._load_resolution_cache", return_value={}), \
             patch("sources.accounts.time.time", side_effect=[1000, 1000, 1005, 1200, 1200, 1200]):
            winners = accounts.discover_winners(cfg)

        # t0=1000; wallet0 check 1000-1000=0<=120 (processed);
        # wallet1 check 1005-1000=5<=120 (processed);
        # wallet2 check 1200-1000=200>120 -> break before processing.
        self.assertEqual(mock_pos.call_count, 2)
        self.assertEqual(len(winners), 2)

    def test_default_budget_used_when_unset(self):
        """Config with no discovery_time_budget_s key must not crash — default applies."""
        from sources import accounts
        cfg = _cfg()  # no discovery_time_budget_s key at all

        with patch("sources.accounts.fetch_recent_trades", return_value=[{"proxyWallet": "0xONE"}]), \
             patch("sources.accounts.fetch_user_positions", side_effect=self._fake_positions), \
             patch("sources.accounts.fetch_resolutions_for_positions", side_effect=self._fake_resolutions), \
             patch("sources.accounts.fetch_user_trades", return_value=[]), \
             patch("sources.accounts._load_resolution_cache", return_value={}):
            winners = accounts.discover_winners(cfg)  # real time.time() — must finish fast, well under any real budget
        self.assertEqual(len(winners), 1)


# ── True resolution fetch + cache (2026-09-08) ─────────────────────────────────

class TestFetchMarketResolution(unittest.TestCase):
    """fetch_market_resolution() — ground truth via CLOB, independent of the
    Data API's broken post-resolution position fields."""

    def test_closed_market_returns_outcome_winner_map(self):
        from sources import accounts
        with patch("sources.accounts.requests.get") as mock_get, \
             patch("sources.accounts.time.sleep"):
            mock_get.return_value.raise_for_status = lambda: None
            mock_get.return_value.json.return_value = {
                "closed": True,
                "tokens": [
                    {"outcome": "Yes", "winner": False},
                    {"outcome": "No", "winner": True},
                ],
            }
            result = accounts.fetch_market_resolution("0xabc")
        self.assertEqual(result, {"Yes": False, "No": True})

    def test_unclosed_market_returns_none(self):
        from sources import accounts
        with patch("sources.accounts.requests.get") as mock_get, \
             patch("sources.accounts.time.sleep"):
            mock_get.return_value.raise_for_status = lambda: None
            mock_get.return_value.json.return_value = {"closed": False, "tokens": []}
            result = accounts.fetch_market_resolution("0xabc")
        self.assertIsNone(result)

    def test_request_failure_returns_none_and_prints(self):
        from sources import accounts
        with patch("sources.accounts.requests.get", side_effect=Exception("boom")), \
             patch("sources.accounts.time.sleep"), \
             patch("builtins.print") as mock_print:
            result = accounts.fetch_market_resolution("0xabc")
        self.assertIsNone(result)
        mock_print.assert_called_once()


class TestFetchResolutionsForPositions(unittest.TestCase):
    """fetch_resolutions_for_positions() — permanent disk cache, only fetches
    unresolved-in-cache conditionIds, only for redeemable positions."""

    def test_only_fetches_redeemable_uncached_condition_ids(self):
        from sources import accounts
        positions = [
            {"conditionId": "A", "redeemable": True},
            {"conditionId": "B", "redeemable": True},
            {"conditionId": "C", "redeemable": False},  # open — must not be fetched
        ]
        cache = {"A": {"Yes": True}}  # A already cached

        with patch("sources.accounts.fetch_market_resolution") as mock_fetch, \
             patch("sources.accounts._save_resolution_cache") as mock_save:
            mock_fetch.return_value = {"Yes": False}
            result = accounts.fetch_resolutions_for_positions(positions, cache=cache)

        mock_fetch.assert_called_once_with("B")
        self.assertEqual(result["A"], {"Yes": True})
        self.assertEqual(result["B"], {"Yes": False})
        mock_save.assert_called_once()

    def test_no_new_fetches_does_not_save(self):
        from sources import accounts
        positions = [{"conditionId": "A", "redeemable": True}]
        cache = {"A": {"Yes": True}}

        with patch("sources.accounts.fetch_market_resolution") as mock_fetch, \
             patch("sources.accounts._save_resolution_cache") as mock_save:
            accounts.fetch_resolutions_for_positions(positions, cache=cache)

        mock_fetch.assert_not_called()
        mock_save.assert_not_called()

    def test_fetch_new_false_skips_live_calls_entirely(self):
        """diagnose_discovery()'s live-sample path: fetch_new=False must
        never call fetch_market_resolution, no matter how many uncached
        conditionIds are present — bounds cost for a fresh, large sample."""
        from sources import accounts
        positions = [
            {"conditionId": f"cond{i}", "redeemable": True} for i in range(50)
        ]
        with patch("sources.accounts.fetch_market_resolution") as mock_fetch, \
             patch("sources.accounts._save_resolution_cache") as mock_save:
            result = accounts.fetch_resolutions_for_positions(positions, cache={"cond0": {"Yes": True}}, fetch_new=False)

        mock_fetch.assert_not_called()
        mock_save.assert_not_called()
        self.assertEqual(result, {"cond0": {"Yes": True}})

    def test_failed_fetch_is_not_cached(self):
        from sources import accounts
        positions = [{"conditionId": "A", "redeemable": True}]

        with patch("sources.accounts.fetch_market_resolution", return_value=None), \
             patch("sources.accounts._save_resolution_cache") as mock_save:
            result = accounts.fetch_resolutions_for_positions(positions, cache={})

        self.assertNotIn("A", result)
        mock_save.assert_not_called()


# ── gate_checklist() + get_wallet_profile() (2026-09-08, Trader Profile page) ──

class TestGateChecklist(unittest.TestCase):
    """Plain-language per-gate pass/fail breakdown, in GATE_ORDER."""

    def test_all_pass_labels_true(self):
        checklist = gate_checklist(_stats(), _cfg())
        self.assertEqual(len(checklist), 5)
        self.assertTrue(all(item["passed"] for item in checklist))

    def test_failing_gate_flagged_with_readable_detail(self):
        checklist = gate_checklist(_stats(win_rate=40.0), _cfg())
        win_rate_item = checklist[1]
        self.assertFalse(win_rate_item["passed"])
        self.assertIn("40.0%", win_rate_item["detail"])
        self.assertIn("55%", win_rate_item["detail"])

    def test_none_win_rate_does_not_crash_formatting(self):
        checklist = gate_checklist(_stats(win_rate=None), _cfg())
        self.assertFalse(checklist[1]["passed"])
        self.assertIn("no win rate yet", checklist[1]["detail"])


class TestGetWalletProfile(unittest.TestCase):
    """get_wallet_profile() assembles stats + checklist + current/history bets."""

    def test_no_positions_returns_none(self):
        from sources import accounts
        with patch("sources.accounts.fetch_user_positions", return_value=[]):
            profile = accounts.get_wallet_profile("0xabc", _cfg())
        self.assertIsNone(profile)

    def test_assembles_current_bets_and_history(self):
        from sources import accounts
        win_pos, win_entry = _pos("Will X happen?", True, realized=0.0, initial=50.0, size=100.0)
        open_pos, _ = _pos("Will Z happen?", None)
        open_pos["percentPnl"] = 25.0
        positions = [win_pos, open_pos]
        cid, res = win_entry
        resolutions = {cid: res}

        with patch("sources.accounts.fetch_user_positions", return_value=positions), \
             patch("sources.accounts.fetch_resolutions_for_positions", return_value=resolutions):
            profile = accounts.get_wallet_profile("0xabc", _cfg())

        self.assertIsNotNone(profile)
        self.assertEqual(profile["address"], "0xabc")
        self.assertEqual(len(profile["current_bets"]), 1)
        self.assertEqual(profile["current_bets"][0]["title"], "Will Z happen?")
        self.assertEqual(len(profile["bet_history"]), 1)
        self.assertEqual(profile["bet_history"][0]["title"], "Will X happen?")
        self.assertTrue(profile["bet_history"][0]["won"])
        self.assertAlmostEqual(profile["bet_history"][0]["pnl"], 50.0)  # 0 + 100 - 50
        self.assertEqual(len(profile["checklist"]), 5)

    def test_unresolved_outcome_omitted_from_history(self):
        """A resolved position with no matching resolution entry is omitted
        from bet_history entirely, never guessed."""
        from sources import accounts
        pos, _entry = _pos("Will X happen?", True, realized=0.0, initial=50.0, size=100.0)
        positions = [pos]

        with patch("sources.accounts.fetch_user_positions", return_value=positions), \
             patch("sources.accounts.fetch_resolutions_for_positions", return_value={}):
            profile = accounts.get_wallet_profile("0xabc", _cfg())

        self.assertEqual(profile["bet_history"], [])


# ── _get() rate-limit pacing + real-error visibility (2026-08-23) ─────────────
# Root cause of "no positions returned from API" excluding real wallets in
# every pipeline run: no pacing at all against Polymarket's own documented
# 150 req/10s (IP-based) /positions cap, and a bare except that couldn't
# distinguish a timeout from a genuinely empty result. requests.get and
# time.sleep are both mocked -- no live network calls, no real sleeping.

class TestGetPacingAndErrorVisibility(unittest.TestCase):

    def test_sleeps_before_every_request(self):
        from sources import accounts
        with patch("sources.accounts.requests.get") as mock_get, \
             patch("sources.accounts.time.sleep") as mock_sleep:
            mock_get.return_value.raise_for_status = lambda: None
            mock_get.return_value.json.return_value = []
            accounts._get("positions", {"user": "0xabc"})
        mock_sleep.assert_called_once_with(accounts._MIN_REQUEST_INTERVAL_S)

    def test_successful_empty_response_prints_nothing(self):
        """A wallet with genuinely zero positions is silent -- not a failure."""
        from sources import accounts
        with patch("sources.accounts.requests.get") as mock_get, \
             patch("sources.accounts.time.sleep"), \
             patch("builtins.print") as mock_print:
            mock_get.return_value.raise_for_status = lambda: None
            mock_get.return_value.json.return_value = []
            result = accounts._get("positions", {"user": "0xabc"})
        self.assertEqual(result, [])
        mock_print.assert_not_called()

    def test_timeout_is_visible_and_distinct_from_empty_result(self):
        """A real failure (timeout, connection error, non-2xx) must print,
        so it's distinguishable in logs from a genuinely empty response --
        both still return None to the caller (unchanged contract)."""
        from sources import accounts
        import requests as _requests
        with patch("sources.accounts.requests.get", side_effect=_requests.exceptions.Timeout("timed out")), \
             patch("sources.accounts.time.sleep"), \
             patch("builtins.print") as mock_print:
            result = accounts._get("positions", {"user": "0xabc"})
        self.assertIsNone(result)
        mock_print.assert_called_once()
        self.assertIn("positions", mock_print.call_args[0][0])
        self.assertIn("failed", mock_print.call_args[0][0])


class TestReadCachedWinners(unittest.TestCase):
    """
    backlog: smart-money-winning-whales-panel. read_cached_winners() must
    be a pure, read-only file peek -- never triggers a live
    discover_winners() Polymarket crawl (unlike load_winners()) -- so the
    Smart Money dashboard's Winning Whales panel never blocks a page load
    on a multi-minute live fetch.
    """

    def test_reads_existing_cache(self):
        import json as _json
        import tempfile, os
        from sources import accounts
        with tempfile.TemporaryDirectory() as d:
            cache_path = os.path.join(d, "winning_accounts.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                _json.dump({"updated_at": 12345.0, "winners": [{"address": "0xabc", "win_rate": 60.0}]}, f)
            with patch.object(accounts, "CACHE_FILE", cache_path):
                winners, updated_at = accounts.read_cached_winners()
        self.assertEqual(winners, [{"address": "0xabc", "win_rate": 60.0}])
        self.assertEqual(updated_at, 12345.0)

    def test_missing_cache_file_returns_empty_not_raise(self):
        from sources import accounts
        with patch.object(accounts, "CACHE_FILE", "/nonexistent/path/winning_accounts.json"):
            winners, updated_at = accounts.read_cached_winners()
        self.assertEqual(winners, [])
        self.assertIsNone(updated_at)

    def test_never_calls_discover_winners(self):
        """The defining difference from load_winners() -- must not import/
        invoke any live-fetch code path regardless of cache staleness."""
        from sources import accounts
        with patch.object(accounts, "discover_winners") as mock_discover, \
             patch.object(accounts, "CACHE_FILE", "/nonexistent/path/winning_accounts.json"):
            accounts.read_cached_winners()
        mock_discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
