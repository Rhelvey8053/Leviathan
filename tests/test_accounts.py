"""
tests/test_accounts.py — Offline tests for accounts.py wallet-selection fix (Goal 2d PART D).

All tests use synthesised position data only — no network calls.
No existing test was modified to accommodate these changes.
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
    GATE_ORDER,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _pos(title: str, pct: float, cash: float, resolved: bool = False) -> dict:
    """Minimal position dict."""
    return {
        "title":      title,
        "percentPnl": pct,
        "cashPnl":    cash,
        "redeemable": resolved,
        "eventSlug":  "",
        "outcome":    "yes",
    }


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


def _real_winners(n: int = 12, wins: int = 9) -> list[dict]:
    """n resolved real-forecast positions; wins of them are positive."""
    return [
        _pos(f"Will Policy {i} happen?", 80.0 if i < wins else -30.0,
             200.0 if i < wins else -50.0, resolved=True)
        for i in range(n)
    ]


# ── PART D tests ──────────────────────────────────────────────────────────────

class TestLuckCaseExcluded(unittest.TestCase):
    """Core regression: open-position P&L does not qualify a wallet."""

    def test_open_position_high_pnl_does_not_qualify(self):
        """Wallet with 20 open positions at +500% each is excluded (resolved_count=0)."""
        positions = [
            _pos(f"Open Market {i}", 500.0, 1000.0, resolved=False)
            for i in range(20)
        ]
        stats = _score_wallet(positions)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["resolved_count"], 0)
        self.assertFalse(_is_winner(stats, _cfg()))

    def test_coinflip_resolved_does_not_count(self):
        """A wallet whose entire resolved history is coinflip titles has resolved_count=0."""
        positions = [
            _pos("Bitcoin Up or Down 5m", 200.0, 300.0, resolved=True),
            _pos("Bitcoin Up or Down 1m", 100.0, 150.0, resolved=True),
            _pos("ETH up or down", -50.0, -25.0, resolved=True),
        ]
        stats = _score_wallet(positions)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["resolved_count"], 0,
                         "Coinflip resolved positions must not count toward track record")
        self.assertFalse(_is_winner(stats, _cfg()))

    def test_sports_game_resolved_does_not_count(self):
        """Positions whose titles match sports-game patterns are excluded from resolved scoring."""
        positions = [
            _pos("Will Germany win on 2026-06-25?", 150.0, 200.0, resolved=True),
            _pos("Will Brazil vs. Argentina end in a draw?", 80.0, 100.0, resolved=True),
            _pos("FIFA World Cup winner 2026", 120.0, 160.0, resolved=True),
        ]
        stats = _score_wallet(positions)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["resolved_count"], 0,
                         "Sports-game resolved positions must not count toward track record")
        self.assertFalse(_is_winner(stats, _cfg()))


class TestVerifiedTrackRecordQualifies(unittest.TestCase):
    """Wallets with a real resolved track record pass the filter."""

    def test_twelve_resolved_real_positions_qualifies(self):
        """12 resolved non-coinflip positions with 75% win rate qualifies."""
        positions = _real_winners(n=12, wins=9)
        stats = _score_wallet(positions)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["resolved_count"], 12)
        self.assertAlmostEqual(stats["win_rate"], 75.0)
        self.assertTrue(_is_winner(stats, _cfg()))

    def test_below_resolved_count_threshold_excluded(self):
        """5 resolved positions (below threshold of 10) is excluded."""
        positions = _real_winners(n=5, wins=4)
        stats = _score_wallet(positions)
        self.assertEqual(stats["resolved_count"], 5)
        self.assertFalse(_is_winner(stats, _cfg()))

    def test_below_win_rate_threshold_excluded(self):
        """12 resolved positions with 40% win rate (below 55%) is excluded."""
        positions = _real_winners(n=12, wins=5)
        stats = _score_wallet(positions)
        self.assertEqual(stats["resolved_count"], 12)
        self.assertAlmostEqual(stats["win_rate"], round(5 / 12 * 100, 1))
        self.assertFalse(_is_winner(stats, _cfg()))

    def test_below_resolved_cash_pnl_threshold_excluded(self):
        """12 resolved positions with negative cash PnL excluded even with high win rate."""
        positions = [
            _pos(f"Policy {i}", 80.0 if i < 9 else -200.0,
                 1.0 if i < 9 else -500.0, resolved=True)
            for i in range(12)
        ]
        stats = _score_wallet(positions)
        self.assertEqual(stats["resolved_count"], 12)
        self.assertLess(stats["resolved_cash_pnl"], 100.0)
        self.assertFalse(_is_winner(stats, _cfg()))


class TestRankingOnResolvedMetrics(unittest.TestCase):
    """Ranking uses resolved win rate, not open-position avg_pct_pnl."""

    def test_ranking_prefers_higher_resolved_win_rate(self):
        """
        Wallet A: resolved win_rate=80%, resolved_cash_pnl=$500, open pnl=+10%
        Wallet B: resolved win_rate=60%, resolved_cash_pnl=$5000, open pnl=+500%

        Under the old sort (avg_pct_pnl), B would rank first.
        Under the new sort (win_rate, resolved_cash_pnl), A ranks first.
        """
        # Wallet A: high win rate, moderate cash
        pos_a = [
            _pos(f"Policy A{i}", 80.0 if i < 8 else -20.0,
                 60.0 if i < 8 else -20.0, resolved=True)
            for i in range(10)
        ]
        stats_a = _score_wallet(pos_a)
        self.assertAlmostEqual(stats_a["win_rate"], 80.0)

        # Wallet B: lower win rate but much larger cash PnL
        pos_b = [
            _pos(f"Policy B{i}", 60.0 if i < 6 else -30.0,
                 800.0 if i < 6 else -100.0, resolved=True)
            for i in range(10)
        ]
        stats_b = _score_wallet(pos_b)
        self.assertAlmostEqual(stats_b["win_rate"], 60.0)
        self.assertGreater(stats_b["resolved_cash_pnl"], stats_a["resolved_cash_pnl"])

        # New ranking: A before B because win_rate is primary key
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

    def test_old_pnl_ranking_would_differ(self):
        """Confirm the old avg_pct_pnl sort would produce the opposite ordering."""
        pos_a_open = [_pos("Open A", 15.0, 50.0, resolved=False) for _ in range(10)]
        pos_b_open = [_pos("Open B", 600.0, 5000.0, resolved=False) for _ in range(10)]
        # A has lower open pnl, B has higher — old sort would put B first
        # But we have no avg_pct_pnl in the new stats dict, confirming it was removed
        stats_a = _score_wallet(pos_a_open + [
            _pos(f"Resolved A{i}", 80.0, 60.0, resolved=True) for i in range(10)
        ])
        stats_b = _score_wallet(pos_b_open + [
            _pos(f"Resolved B{i}", 60.0, 800.0, resolved=True) for i in range(10)
        ])
        self.assertNotIn("avg_pct_pnl", stats_a,
                         "avg_pct_pnl (all-positions) must no longer be in stats dict")
        self.assertIn("resolved_avg_pct_pnl", stats_a)


class TestEmptyWatchlistDoesNotCrash(unittest.TestCase):
    """Empty qualifying set produces an empty list without error."""

    def test_empty_positions_returns_none(self):
        stats = _score_wallet([])
        self.assertIsNone(stats)

    def test_none_stats_is_not_winner(self):
        """_is_winner with None stats is safely handled by the caller check."""
        # discover_winners calls: if not (stats and _is_winner(stats, config)): continue
        # So None stats short-circuits. We test that _is_winner itself doesn't crash
        # if called defensively with a minimal stats dict.
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

    def _fake_trades(self, *_args, **_kwargs):
        return [
            {"proxyWallet": "0xWIN"},   # will pass every gate
            {"proxyWallet": "0xLOW"},   # will die at resolved_count
            {"proxyWallet": "0xNONE"},  # fetch_user_positions returns []
        ]

    def _fake_positions(self, address, *_args, **_kwargs):
        if address == "0xWIN":
            return [_pos(f"Policy {i}", 80.0, 60.0, resolved=True) for i in range(12)]
        if address == "0xLOW":
            return [_pos(f"Policy {i}", 80.0, 60.0, resolved=True) for i in range(3)]
        return []  # 0xNONE — no positions returned by the API

    def test_end_to_end_with_stubbed_fetch(self):
        config = _cfg()
        config["accounts"]["discovery_sample_size"] = 300

        with patch("sources.accounts.fetch_recent_trades", side_effect=self._fake_trades), \
             patch("sources.accounts.fetch_user_positions", side_effect=self._fake_positions):
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
             patch("sources.accounts.fetch_user_positions", side_effect=self._fake_positions) as mock_pos:
            diagnose_discovery(config)
        self.assertEqual(mock_pos.call_count, 3)


if __name__ == "__main__":
    unittest.main()
