"""
Tests for main.py's downgrade_reason tracking (backlog: downgrade-reason-field,
2026-09-14) -- which of the three HIGH-confidence downgrade rules fired when
confidence_downgraded=1. Purely observational: never read by any scoring/
direction/edge logic, so this is not a confidence-scoring change.

No network calls, no Claude CLI, no DB access -- pure function tests against
main._append_downgrade_reason directly.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from main import _append_downgrade_reason


def test_append_downgrade_reason_sets_first_reason():
    signal = {}
    _append_downgrade_reason(signal, "edge_below_min")
    assert signal["downgrade_reason"] == "edge_below_min"


def test_append_downgrade_reason_appends_second_reason():
    """
    A signal can theoretically be hit by more than one downgrade rule in
    sequence (HIGH -> MED via the edge-floor rule, then MED -> LOW via the
    thin-liquidity rule, since that third rule's own condition checks
    confidence in (HIGH, MED) and runs after the first two). Never observed
    live as of 2026-09-14, but the second reason must not silently
    overwrite and lose the first.
    """
    signal = {"downgrade_reason": "edge_below_min"}
    _append_downgrade_reason(signal, "thin_liquidity")
    assert signal["downgrade_reason"] == "edge_below_min,thin_liquidity"


def test_append_downgrade_reason_does_not_mutate_other_keys():
    signal = {"ticker": "KXTEST", "confidence": "MED"}
    _append_downgrade_reason(signal, "short_horizon_uncorroborated")
    assert signal["ticker"] == "KXTEST"
    assert signal["confidence"] == "MED"
    assert signal["downgrade_reason"] == "short_horizon_uncorroborated"
