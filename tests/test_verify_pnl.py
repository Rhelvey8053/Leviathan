"""
tests/test_verify_pnl.py — Offline tests for scripts/verify_pnl.py's new
alerting logic (2026-09-08). The core recompute/diff logic isn't covered
here (no prior test harness existed for this script -- consistent with
this project's precedent for direct-DB/subprocess orchestration scripts);
this file covers only the new send_alert_if_needed()/compose_alert() path
that makes the (now-scheduled) check actually notify someone.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import scripts.verify_pnl as vp

CONFIG = {"report": {"email_to": "test@example.com"}}


def _diff(call_id="c1", delta=0.05):
    return {
        "call_id": call_id, "ticker": "KXTEST-01", "direction": "YES",
        "outcome": "NO", "stored_pnl": -0.30, "recomputed": -0.30 + delta,
        "delta": delta,
    }


def test_send_alert_if_needed_no_diffs_sends_nothing(tmp_path):
    with patch.object(vp, "send_report") as mock_send:
        sent = vp.send_alert_if_needed([], CONFIG, state_path=tmp_path / "state.json")
    assert sent is False
    mock_send.assert_not_called()


def test_send_alert_if_needed_sends_on_new_diffs(tmp_path):
    state_path = tmp_path / "state.json"
    with patch.object(vp, "send_report") as mock_send:
        sent = vp.send_alert_if_needed([_diff()], CONFIG, state_path=state_path)
    assert sent is True
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert "1 row(s) mismatched" in kwargs["subject_override"]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "c1" in list(state["alerted"].keys())[0]


def test_send_alert_if_needed_dedups_same_day(tmp_path):
    """A still-unresolved problem (identical call_id set) must not
    re-alert on a second run the same day."""
    state_path = tmp_path / "state.json"
    with patch.object(vp, "send_report") as mock_send:
        vp.send_alert_if_needed([_diff()], CONFIG, state_path=state_path)
        sent_again = vp.send_alert_if_needed([_diff()], CONFIG, state_path=state_path)
    assert sent_again is False
    mock_send.assert_called_once()


def test_send_alert_if_needed_resends_for_a_different_diff_set(tmp_path):
    """A genuinely new/different set of mismatched rows still alerts even
    on the same day -- dedup is keyed on the actual signature, not just
    'already sent once today'."""
    state_path = tmp_path / "state.json"
    with patch.object(vp, "send_report") as mock_send:
        vp.send_alert_if_needed([_diff(call_id="c1")], CONFIG, state_path=state_path)
        vp.send_alert_if_needed([_diff(call_id="c2")], CONFIG, state_path=state_path)
    assert mock_send.call_count == 2


def test_send_alert_if_needed_send_failure_does_not_persist_state(tmp_path):
    """If send_report raises, the state must NOT be written -- otherwise a
    genuinely-failed send would look like a delivered alert and the next
    run would wrongly skip retrying."""
    state_path = tmp_path / "state.json"
    with patch.object(vp, "send_report", side_effect=RuntimeError("smtp down")):
        sent = vp.send_alert_if_needed([_diff()], CONFIG, state_path=state_path)
    assert sent is False
    assert not state_path.exists()


def test_compose_alert_lists_every_diff():
    diffs = [_diff(call_id="c1"), _diff(call_id="c2")]
    body, subject = vp.compose_alert(diffs)
    assert "c1" in body
    assert "c2" in body
    assert "2 row(s) mismatched" in subject
