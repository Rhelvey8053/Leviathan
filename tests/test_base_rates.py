"""
tests/test_base_rates.py — Tests for base_rates.py (Section 7 — empirical-base-rates-poly).

No network calls. Uses in-memory CSV data.
"""

import csv
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from base_rates import BASE_RATES, load_empirical_rates, merge_rates


# ── BASE_RATES ────────────────────────────────────────────────────────────────

def test_base_rates_has_expected_keys():
    for key in ("EDGE", "DRIFT", "HEURISTIC", "BR_NONE", "ELECTION_WIN"):
        assert key in BASE_RATES, f"Missing BASE_RATES key: {key}"


def test_base_rates_values_in_range():
    for cat, rate in BASE_RATES.items():
        assert 0.0 <= rate <= 1.0, f"{cat} rate {rate} out of [0, 1]"


# ── load_empirical_rates ──────────────────────────────────────────────────────

def _write_emp_csv(path: str, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["category", "resolved", "hits"])
        w.writeheader()
        w.writerows(rows)


def test_load_empirical_rates_basic(tmp_path):
    p = str(tmp_path / "emp.csv")
    _write_emp_csv(p, [
        {"category": "EDGE",    "resolved": "20", "hits": "12"},
        {"category": "UNKNOWN", "resolved": "10", "hits": "5"},
    ])
    rates, ns = load_empirical_rates(p)
    assert abs(rates["EDGE"] - 0.6) < 0.001
    assert ns["EDGE"] == 20
    assert abs(rates["UNKNOWN"] - 0.5) < 0.001


def test_load_empirical_rates_skips_zero_resolved(tmp_path):
    p = str(tmp_path / "emp.csv")
    _write_emp_csv(p, [{"category": "EDGE", "resolved": "0", "hits": "0"}])
    rates, ns = load_empirical_rates(p)
    assert "EDGE" not in rates


def test_load_empirical_rates_skips_bad_rows(tmp_path):
    p = str(tmp_path / "emp.csv")
    _write_emp_csv(p, [{"category": "BAD", "resolved": "abc", "hits": "xyz"}])
    rates, ns = load_empirical_rates(p)
    assert "BAD" not in rates


# ── merge_rates ───────────────────────────────────────────────────────────────

def test_merge_rates_replaces_when_n_meets_threshold():
    """Empirical rate replaces prior when n >= min_n."""
    priors   = {"EDGE": 0.52}
    empirical = {"EDGE": 0.65}
    ns = {"EDGE": 20}
    merged = merge_rates(priors, empirical, min_n=15, empirical_ns=ns)
    assert abs(merged["EDGE"] - 0.65) < 0.001


def test_merge_rates_keeps_prior_when_n_below_threshold():
    """Empirical rate is ignored when n < min_n."""
    priors    = {"EDGE": 0.52}
    empirical = {"EDGE": 0.80}
    ns = {"EDGE": 10}
    merged = merge_rates(priors, empirical, min_n=15, empirical_ns=ns)
    assert abs(merged["EDGE"] - 0.52) < 0.001


def test_merge_rates_adds_new_category_if_n_met():
    """New category not in priors gets added if n >= min_n."""
    priors    = {}
    empirical = {"NEW_CAT": 0.45}
    ns = {"NEW_CAT": 20}
    merged = merge_rates(priors, empirical, min_n=15, empirical_ns=ns)
    assert abs(merged["NEW_CAT"] - 0.45) < 0.001


def test_merge_rates_preserves_unmatched_priors():
    """Categories in priors but not in empirical are preserved."""
    priors    = {"EDGE": 0.52, "DRIFT": 0.54}
    empirical = {"EDGE": 0.65}
    ns = {"EDGE": 20}
    merged = merge_rates(priors, empirical, min_n=15, empirical_ns=ns)
    assert "DRIFT" in merged
    assert abs(merged["DRIFT"] - 0.54) < 0.001


def test_merge_rates_with_real_base_rates():
    """merge_rates works correctly with the actual BASE_RATES dict."""
    empirical = {"EDGE": 0.60, "DRIFT": 0.55, "BRAND_NEW": 0.40}
    ns = {"EDGE": 20, "DRIFT": 5, "BRAND_NEW": 25}
    merged = merge_rates(BASE_RATES, empirical, min_n=15, empirical_ns=ns)
    assert abs(merged["EDGE"] - 0.60) < 0.001    # replaced (n=20 >= 15)
    assert abs(merged["DRIFT"] - 0.54) < 0.001   # kept prior (n=5 < 15)
    assert abs(merged["BRAND_NEW"] - 0.40) < 0.001  # added (n=25 >= 15)
    # All original keys still present
    for key in BASE_RATES:
        assert key in merged


def test_merge_rates_no_empirical_ns_assumes_min_n_met():
    """When empirical_ns=None, all empirical entries are treated as meeting min_n."""
    priors    = {"EDGE": 0.52}
    empirical = {"EDGE": 0.75}
    merged = merge_rates(priors, empirical, min_n=15, empirical_ns=None)
    assert abs(merged["EDGE"] - 0.75) < 0.001
