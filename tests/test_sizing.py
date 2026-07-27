"""
tests/test_sizing.py — Offline tests for core/sizing.py (confidence-weighted
hypothetical stake sizing, 2026-07-27).

No network calls. is_dynamic_sizing_eligible() reads live DB metrics via
backlog.checker.compute_metrics(), which is mocked throughout so these
tests never depend on the real leviathan.db's actual resolved-signal count.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core import sizing


def _config(enabled=True, unit_size=10, multipliers=None):
    betting = {"unit_size": unit_size, "dynamic_sizing_enabled": enabled}
    if multipliers is not None:
        betting["confidence_stake_multipliers"] = multipliers
    return {"betting": betting}


# ─── is_dynamic_sizing_eligible ────────────────────────────────────────────────

def test_ineligible_when_config_flag_disabled():
    """Even if live metrics clear the gate, the human opt-in must also be True."""
    with patch("core.sizing.compute_metrics", return_value={
        "resolved_count": 100, "resolved_count_per_category_max": 100,
    }):
        assert sizing.is_dynamic_sizing_eligible(_config(enabled=False)) is False


def test_ineligible_when_resolved_count_too_low():
    with patch("core.sizing.compute_metrics", return_value={
        "resolved_count": 29, "resolved_count_per_category_max": 15,
    }):
        assert sizing.is_dynamic_sizing_eligible(_config(enabled=True)) is False


def test_ineligible_when_per_category_max_too_low():
    with patch("core.sizing.compute_metrics", return_value={
        "resolved_count": 30, "resolved_count_per_category_max": 14,
    }):
        assert sizing.is_dynamic_sizing_eligible(_config(enabled=True)) is False


def test_eligible_when_both_gates_and_opt_in_clear():
    with patch("core.sizing.compute_metrics", return_value={
        "resolved_count": 30, "resolved_count_per_category_max": 15,
    }):
        assert sizing.is_dynamic_sizing_eligible(_config(enabled=True)) is True


def test_gate_matches_auto_calibration_loop_trigger():
    """The hardcoded thresholds must mirror backlog.json's auto-calibration-loop
    trigger exactly -- this is the same class of change and needs the same floor."""
    import json
    d = json.load(open(ROOT / "backlog" / "backlog.json", encoding="utf-8"))
    item = next(i for i in d["items"] if i["id"] == "auto-calibration-loop")
    trigger_values = {t["metric"]: t["value"] for t in item["trigger"]["all"]}
    assert sizing.MIN_RESOLVED_COUNT == trigger_values["resolved_count"]
    assert sizing.MIN_RESOLVED_PER_CATEGORY == trigger_values["resolved_count_per_category_max"]


def test_eligibility_never_cached_across_calls():
    """Re-checks the DB every call -- can't report stale eligibility as more
    signals resolve, and can't be fooled by calling it once early."""
    with patch("core.sizing.compute_metrics", return_value={
        "resolved_count": 5, "resolved_count_per_category_max": 2,
    }):
        assert sizing.is_dynamic_sizing_eligible(_config(enabled=True)) is False
    with patch("core.sizing.compute_metrics", return_value={
        "resolved_count": 40, "resolved_count_per_category_max": 20,
    }):
        assert sizing.is_dynamic_sizing_eligible(_config(enabled=True)) is True


# ─── compute_stake_multiplier ───────────────────────────────────────────────────

def test_multiplier_high_confidence():
    assert sizing.compute_stake_multiplier("HIGH", _config()) == pytest.approx(1.5)


def test_multiplier_med_confidence():
    assert sizing.compute_stake_multiplier("MED", _config()) == pytest.approx(1.0)


def test_multiplier_low_confidence():
    assert sizing.compute_stake_multiplier("LOW", _config()) == pytest.approx(0.5)


def test_multiplier_case_insensitive():
    assert sizing.compute_stake_multiplier("high", _config()) == pytest.approx(1.5)


def test_multiplier_unknown_confidence_falls_back_to_flat():
    """An unrecognized/blank confidence must never silently reduce or inflate
    a stake it doesn't understand."""
    assert sizing.compute_stake_multiplier("", _config()) == pytest.approx(1.0)
    assert sizing.compute_stake_multiplier("MAYBE", _config()) == pytest.approx(1.0)


def test_multiplier_respects_config_override():
    """confidence_stake_multipliers is config-driven, tunable without code changes."""
    custom = {"HIGH": 3.0, "MED": 1.0, "LOW": 0.1}
    assert sizing.compute_stake_multiplier("HIGH", _config(multipliers=custom)) == pytest.approx(3.0)


# ─── compute_stake_size ─────────────────────────────────────────────────────────

def test_stake_size_flat_when_ineligible():
    with patch("core.sizing.compute_metrics", return_value={
        "resolved_count": 8, "resolved_count_per_category_max": 8,
    }):
        size = sizing.compute_stake_size({"confidence": "HIGH"}, _config(enabled=True, unit_size=10))
    assert size == pytest.approx(10.0)


def test_stake_size_flat_when_disabled_even_if_metrics_clear():
    with patch("core.sizing.compute_metrics", return_value={
        "resolved_count": 100, "resolved_count_per_category_max": 100,
    }):
        size = sizing.compute_stake_size({"confidence": "HIGH"}, _config(enabled=False, unit_size=10))
    assert size == pytest.approx(10.0)


def test_stake_size_scaled_when_eligible():
    with patch("core.sizing.compute_metrics", return_value={
        "resolved_count": 30, "resolved_count_per_category_max": 15,
    }):
        high = sizing.compute_stake_size({"confidence": "HIGH"}, _config(enabled=True, unit_size=10))
        med  = sizing.compute_stake_size({"confidence": "MED"},  _config(enabled=True, unit_size=10))
        low  = sizing.compute_stake_size({"confidence": "LOW"},  _config(enabled=True, unit_size=10))
    assert high == pytest.approx(15.0)
    assert med  == pytest.approx(10.0)
    assert low  == pytest.approx(5.0)
