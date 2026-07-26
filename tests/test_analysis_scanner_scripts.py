"""
tests/test_analysis_scanner_scripts.py — Smoke tests for
analysis/drift_diagnosis.py, analysis/flag_mode_compare.py, and
analysis/threshold_sweep.py.

Regression guard for two real bugs found in a full codebase sweep
(2026-07-25):
  1. All three still did `import scanner`, a pre-refactor style from
     before scanner.py moved to core/scanner.py -- ModuleNotFoundError on
     the very first line executed.
  2. flag_mode_compare.py and threshold_sweep.py also called
     scanner.score_markets() without unpacking the (scored_list,
     hp_filtered_count) tuple it actually returns, crashing immediately
     after the import was fixed.

All three scripts load real, already-on-disk snapshot data (no network
calls) via their own main(), so this runs them for real rather than mocking
around the exact bug.
"""

import glob
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from analysis import drift_diagnosis, flag_mode_compare, threshold_sweep

_SNAPSHOTS_EXIST = bool(glob.glob(os.path.join(str(ROOT), "data", "snapshots", "markets_*.json")))


@pytest.mark.skipif(not _SNAPSHOTS_EXIST, reason="no local snapshot files to run against")
def test_drift_diagnosis_main_does_not_crash(capsys):
    drift_diagnosis.main()
    captured = capsys.readouterr()
    assert "drift-fire rate grid" in captured.out


@pytest.mark.skipif(not _SNAPSHOTS_EXIST, reason="no local snapshot files to run against")
def test_flag_mode_compare_main_does_not_crash(capsys):
    flag_mode_compare.main()
    captured = capsys.readouterr()
    assert "passthrough" in captured.out
    assert "strict_with_heuristic" in captured.out


@pytest.mark.skipif(not _SNAPSHOTS_EXIST, reason="no local snapshot files to run against")
def test_threshold_sweep_main_does_not_crash(capsys):
    threshold_sweep.main()
    captured = capsys.readouterr()
    assert "Report written" in captured.out
