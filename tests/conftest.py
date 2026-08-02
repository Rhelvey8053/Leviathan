import sys
import os
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Ensure project root is on sys.path so tests can import logger, scanner, kalshi, etc.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_DUMMY_PRIVATE_KEY_PEM = rsa.generate_private_key(
    public_exponent=65537, key_size=2048,
).private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


@pytest.fixture(autouse=True)
def _kalshi_auth_env(monkeypatch):
    """
    core.kalshi._auth_headers() raises ValueError if KALSHI_KEY_ID /
    KALSHI_PRIVATE_KEY aren't set. Locally a gitignored .env supplies these;
    a clean CI checkout has neither, which fails every test that touches
    Kalshi auth-header generation even though the HTTP layer itself is
    mocked. Provide throwaway-but-valid values for every test so auth-header
    generation succeeds without needing real credentials.
    """
    monkeypatch.setenv("KALSHI_KEY_ID", "test-key-id-00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", _DUMMY_PRIVATE_KEY_PEM)


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
