"""
tests/test_net_edge_analysis.py — Smoke test for analysis/net_edge_analysis.py.

Runs main(use_snapshot=True) against a real, already-on-disk snapshot file
(no network calls) with the real config.json (dedup_by_event=true). This
is a regression guard for a real bug: scanner.score_markets() returns a
(scored_list, hp_filtered_count) tuple, and this script previously used
the return value unpacked -- `dedup_by_event_scored(scored)` then iterated
the tuple itself, crashing with AttributeError on every real run.
"""

import glob
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis import net_edge_analysis

_SNAPSHOTS_EXIST = bool(glob.glob(os.path.join(str(ROOT), "data", "snapshots", "markets_*.json")))


@pytest.mark.skipif(not _SNAPSHOTS_EXIST, reason="no local snapshot files to run against")
def test_main_use_snapshot_does_not_crash(capsys):
    net_edge_analysis.main(use_snapshot=True)
    captured = capsys.readouterr()
    assert "NET-OF-SPREAD EDGE ANALYSIS" in captured.out
    assert "Flagged markets:" in captured.out
