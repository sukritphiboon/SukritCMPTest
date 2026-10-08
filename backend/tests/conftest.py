import base64
import os
import sys
from pathlib import Path

import httpx
import pytest

# The mock server lives in a sibling project; make it importable for driver and API tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mock-server"))

os.environ.setdefault("CMP_ENCRYPTION_KEY", base64.b64encode(b"k" * 32).decode())
os.environ.setdefault("CMP_API_KEYS", "tester:test-key,auditor:other-key")

from mock_server.main import create_app  # noqa: E402

from app.drivers import DeviceConnection, OceanProtectDriver  # noqa: E402

MOCK_PASSWORD = "Admin@storage1"


@pytest.fixture
async def mock_app():
    return create_app(seed=7)


@pytest.fixture
async def driver(mock_app):
    drv = OceanProtectDriver(
        DeviceConnection("mock", https=False, password=MOCK_PASSWORD),
        transport=httpx.ASGITransport(app=mock_app),
    )
    await drv.connect()
    drv.mock = mock_app.state.mock
    yield drv
    await drv.close()
