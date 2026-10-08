import base64
import os
import sys
from pathlib import Path

import httpx
import pytest

# The mock server lives in a sibling project; make it importable for driver tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mock-server"))

os.environ.setdefault("CMP_ENCRYPTION_KEY", base64.b64encode(b"k" * 32).decode())
os.environ.setdefault("CMP_API_KEYS", "tester:test-key,auditor:other-key")

from mock_server.main import create_app  # noqa: E402

from app.drivers import DeviceConnection, DoradoV7Driver, OceanProtectDriver  # noqa: E402

MOCK_PASSWORD = "Admin@storage1"


def _transport(profile: str):
    return httpx.ASGITransport(app=create_app(profile=profile, seed=7))


@pytest.fixture
async def dorado():
    driver = DoradoV7Driver(
        DeviceConnection("mock", https=False, password=MOCK_PASSWORD), transport=_transport("dorado")
    )
    await driver.connect()
    yield driver
    await driver.close()


@pytest.fixture
async def oceanprotect():
    driver = OceanProtectDriver(
        DeviceConnection("mock", https=False, password=MOCK_PASSWORD), transport=_transport("oceanprotect")
    )
    await driver.connect()
    yield driver
    await driver.close()
