import httpx
import pytest
from mock_server.main import create_app as create_mock
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_driver_factory
from app.core.database import get_session
from app.drivers import DeviceConnection, DoradoV7Driver, OceanProtectDriver
from app.main import create_app
from app.models import Base, DeviceModel
from tests.conftest import MOCK_PASSWORD

KEY = {"X-API-Key": "test-key"}


class Env:
    """A CMP app on SQLite, wired to two in-process mock arrays."""

    def __init__(self, http, maker, mocks):
        self.http, self.maker, self.mocks = http, maker, mocks

    @property
    def dorado_mock(self):
        return self.mocks[DeviceModel.DORADO_V7].state.mock

    @property
    def protect_mock(self):
        return self.mocks[DeviceModel.OCEANPROTECT].state.mock

    async def ok(self, method, url, status=None, **kw):
        r = await self.http.request(method, f"/api/v1{url}", **kw)
        allowed = (status,) if status else (200, 201)
        assert r.status_code in allowed, r.text
        return r.json() if r.content else None

    async def err(self, method, url, status, **kw):
        r = await self.http.request(method, f"/api/v1{url}", **kw)
        assert r.status_code == status, r.text
        return r.json()

    async def device(self, model="dorado_v7", name=None, **extra):
        body = {
            "name": name or model,
            "ip_address": "mock",
            "username": "admin",
            "password": MOCK_PASSWORD,
            "model": model,
            **extra,
        }
        return await self.ok("POST", "/devices", json=body)

    async def tenant(self, name="acme", block=1000, file=1000, obj=1000, **extra):
        return await self.ok(
            "POST",
            "/tenants",
            json={
                "name": name,
                "quota_block_gb": block,
                "quota_file_gb": file,
                "quota_object_gb": obj,
                **extra,
            },
        )

    async def audit(self, **params):
        return (await self.ok("GET", "/audit-logs", params=params))["items"]


@pytest.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    mocks = {
        DeviceModel.DORADO_V7: create_mock("dorado", seed=1),
        DeviceModel.OCEANPROTECT: create_mock("oceanprotect", seed=2),
    }

    def factory(device, cipher):
        username, password = device.get_credentials(cipher)
        connection = DeviceConnection("mock", 8088, username, password, https=False)
        cls = DoradoV7Driver if device.model == DeviceModel.DORADO_V7 else OceanProtectDriver
        return cls(connection, transport=httpx.ASGITransport(app=mocks[device.model]))

    async def session_override():
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_driver_factory] = lambda: factory
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://cmp",
        headers=KEY,
    )
    yield Env(http, maker, mocks)
    await http.aclose()
    await engine.dispose()
