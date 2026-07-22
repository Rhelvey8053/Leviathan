import sys
import os
import pytest

# Ensure project root is on sys.path so tests can import logger, scanner, kalshi, etc.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_addoption(parser):
    parser.addoption(
        "--network", action="store_true", default=False,
        help="Run tests marked @pytest.mark.network (real HTTP calls). "
             "Skipped by default so `pytest -q` stays fully offline.",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--network"):
        return
    skip_network = pytest.mark.skip(reason="need --network to run (real HTTP call)")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)
