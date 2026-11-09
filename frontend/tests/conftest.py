import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _test_provider_defaults(monkeypatch):
    """Unit tests exercise configured-SEC and legacy yfinance paths explicitly."""
    monkeypatch.setenv("SEC_USER_AGENT", "Financial Bot tests test@example.com")
    monkeypatch.setenv("YFINANCE_PRIMARY_US_HK", "0")
    monkeypatch.setenv("YFINANCE_A_SHARE_FALLBACK", "0")
    from api.datasources import health

    health.reset()
    yield
    health.reset()
