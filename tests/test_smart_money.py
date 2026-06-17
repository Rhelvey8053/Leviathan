"""
Offline tests for analysis/smart_money_scan.py.

No network calls — tests pure helper functions only.
Run: python -m pytest -q
"""

import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest

# Import the module under test
from analysis.smart_money_scan import (
    _is_binary_position,
    _is_sports_title,
    _normalize,
    _match_to_kalshi,
)


# ─── _is_binary_position ─────────────────────────────────────────────────────

@pytest.mark.parametrize("outcome,expected", [
    # Binary YES/NO — should pass
    ("Yes",                    True),
    ("yes",                    True),
    ("No",                     True),
    ("NO",                     True),
    # Sports outcomes — should be excluded
    ("Over",                   False),
    ("Under",                  False),
    ("Draw",                   False),
    ("Spread",                 False),
    ("Push",                   False),
    ("Tie",                    False),
    ("o/u",                    False),
    # Team names (short, non yes/no) — should be excluded
    ("Algeria",                False),
    ("Jordan",                 False),
    ("Brazil",                 False),
    ("Austria",                False),
    ("St. Louis Cardinals",    False),
    ("América FC",             False),
    # Score lines / numeric — should be excluded
    ("Over 2.5",               False),
    ("Algeria (-1.5)",         False),
    ("Spain (-2.5)",           False),
    # Multi-word non yes/no — excluded by default
    ("Washington Nationals",   False),
    ("Cleveland Guardians",    False),
])
def test_is_binary_position(outcome, expected):
    p = {"outcome": outcome}
    assert _is_binary_position(p) is expected, f"outcome={outcome!r}"


def test_is_binary_position_missing_outcome():
    assert _is_binary_position({}) is False


def test_is_binary_position_none_outcome():
    assert _is_binary_position({"outcome": None}) is False


# ─── _is_sports_title ────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    # Soccer game formats — should be excluded
    ("Will Tunisia vs. Japan end in a draw?",       True),
    ("Will Germany win on 2026-06-25?",             True),
    ("Will Paraguay vs. Australia end in a draw?",  True),
    ("Portugal vs. DR Congo: O/U 2.5",              True),
    ("Argentina vs. Algeria: 1st Half O/U 1.5",     True),
    ("Spread: Spain (-2.5)",                        True),
    # Sports competitions — should be excluded
    ("Will Croatia win the 2026 FIFA World Cup?",   True),
    ("Will France win the 2026 World Cup?",         True),
    ("Will the LA Lakers win the NBA Championship?", True),
    # Political / macro markets — should NOT be excluded
    ("Will Republicans win the 2028 presidential election?",   False),
    ("Will Iran withdraw from the NPT before 2027?",           False),
    ("Will Saudi Arabia join the Abraham Accords before 2030?", False),
    ("Will the U.S. invade Greenland in 2026?",                False),
    ("Will Trump declare a national emergency?",               False),
])
def test_is_sports_title(title, expected):
    assert _is_sports_title(title) is expected, f"title={title!r}"


# ─── _normalize ──────────────────────────────────────────────────────────────

def test_normalize_strips_stopwords():
    words = _normalize("Will the US win the 2026 World Cup?")
    assert "will" not in words
    assert "the" not in words
    assert "2026" not in words
    assert "us" in words or "world" in words  # at least some content words remain


def test_normalize_lowercases():
    words = _normalize("Trump Biden Election")
    assert "trump" in words
    assert "biden" in words


def test_normalize_removes_punctuation():
    words = _normalize("Fed-rate cut: yes/no?")
    assert all(c.isalnum() or c == " " for w in words for c in w)


def test_normalize_empty():
    assert _normalize("") == set()


def test_normalize_all_stopwords():
    words = _normalize("will the be")
    assert words == set()


def test_normalize_min_length():
    words = _normalize("Is it ok to do")
    # "ok" has 2 chars — should be excluded (len > 2)
    # "do" has 2 chars — excluded
    for w in words:
        assert len(w) > 2


# ─── _match_to_kalshi ────────────────────────────────────────────────────────

KALSHI_TITLES = {
    "KXPRESPARTY-2028-R": "Will Republicans win the 2028 presidential election?",
    "KXUKCOALITION-30JAN01-LABMAJ": "Will Labour win a UK majority government by 2030?",
    "KXSCOTUSRESIGN-29-BK": "Will Brett Kavanaugh resign from the Supreme Court by 2029?",
    "KXCAPCONTROL-29": "Will the US impose capital controls by 2029?",
    "KXCOLONIZEMARS-50": "Will humans colonize Mars by 2050?",
}


def test_match_returns_top_3():
    matches = _match_to_kalshi(
        "Will Republicans win the 2028 presidential election?",
        KALSHI_TITLES,
        min_score=0.30,
    )
    assert len(matches) <= 3
    assert all(isinstance(t, str) and isinstance(s, float) for t, s in matches)


def test_match_exact_title_scores_high():
    matches = _match_to_kalshi(
        "Will Republicans win the 2028 presidential election?",
        KALSHI_TITLES,
        min_score=0.30,
    )
    tickers = [t for t, _ in matches]
    assert "KXPRESPARTY-2028-R" in tickers
    # The top match should be the exact title
    assert matches[0][0] == "KXPRESPARTY-2028-R"


def test_match_min_score_filters():
    # At threshold 0.95 almost nothing should match a loose title
    matches = _match_to_kalshi(
        "Will something completely unrelated happen?",
        KALSHI_TITLES,
        min_score=0.95,
    )
    assert matches == []


def test_match_empty_title():
    matches = _match_to_kalshi("", KALSHI_TITLES)
    assert matches == []


def test_match_empty_kalshi_dict():
    matches = _match_to_kalshi("Will Republicans win in 2028?", {})
    assert matches == []


def test_match_sorted_descending():
    matches = _match_to_kalshi(
        "Will Republicans win the presidential election?",
        KALSHI_TITLES,
        min_score=0.20,
    )
    scores = [s for _, s in matches]
    assert scores == sorted(scores, reverse=True)


def test_match_sports_title_below_threshold():
    # A pure sports over/under title should not match political Kalshi markets at 0.40
    matches = _match_to_kalshi(
        "Portugal vs. DR Congo: O/U 2.5",
        KALSHI_TITLES,
        min_score=0.40,
    )
    assert matches == [], f"Expected no matches, got {matches}"


def test_match_requires_two_word_overlap():
    # Completely unrelated titles share zero keywords — must return empty even if
    # character sequences happen to overlap (the old SequenceMatcher-only false positive)
    matches = _match_to_kalshi(
        "Will Saudi Arabia join the Abraham Accords before 2030?",
        {"KXSCOTTIESLAM-28": "Will Scottie Scheffler win the career Grand Slam by 2028?"},
        min_score=0.30,
    )
    assert matches == [], f"Expected no match (zero keyword overlap), got {matches}"


def test_match_single_shared_word_rejected():
    # Titles sharing only one keyword (e.g., "mayor") should be rejected — prevents
    # LA Mayor vs London Mayor cross-topic false positives
    matches = _match_to_kalshi(
        "Will Spencer Pratt win the Los Angeles Mayoral race?",
        {"KXLONDONMAYOR-28": "Will Zoe Polanska win the London Mayoral election?"},
        min_score=0.30,
    )
    # "mayoral" is the only shared keyword — should be excluded (< 2 required)
    assert matches == [], f"Expected no match (single keyword 'mayoral'), got {matches}"


def test_match_two_shared_keywords_passes():
    # "Saudi Arabia" = 2 shared keywords → should still match Abraham Accords
    kalshi = {"KXABRAHAMSA-29": "Will Saudi Arabia join the Abraham Accords by 2029?"}
    matches = _match_to_kalshi(
        "Israel and Saudi Arabia normalize relations before 2030?",
        kalshi,
        min_score=0.30,
    )
    assert len(matches) == 1
    assert matches[0][0] == "KXABRAHAMSA-29"


def test_match_threshold_0_40_raises_bar():
    # "Capital controls" is loosely related to "capital" in financial contexts
    # but shouldn't match e.g. "humans colonize Mars"
    matches = _match_to_kalshi(
        "Will the US impose capital controls?",
        KALSHI_TITLES,
        min_score=0.40,
    )
    tickers = [t for t, _ in matches]
    assert "KXCAPCONTROL-29" in tickers
    assert "KXCOLONIZEMARS-50" not in tickers
