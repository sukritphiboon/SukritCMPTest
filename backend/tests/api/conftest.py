from datetime import UTC, datetime, timedelta

import httpx
import pytest
from mock_server.main import create_app as create_mock
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_driver_factory, get_now
from app.core.crypto import get_cipher
from app.core.database import get_session
from app.drivers import DeviceConnection, OceanProtectDriver
from app.main import create_app
from app.models import Base
from app.services.backup.jobs import sync_active_jobs
from app.services.telemetry.collector import poll_all
from tests.conftest import MOCK_PASSWORD

KEY = {"X-API-Key": "test-key"}
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
GB = 1024**3


class Env:
    """A CMP app on SQLite wired to in-process mock appliances and a controllable clock."""

    def __init__(self, http, maker):
        self.http, self.maker = http, maker
        self.mocks: dict[str, object] = {}
        self.broken: set[str] = set()
        self.clock = T0

    def mock(self, name="op-1"):
        return self.mocks[name].state.mock

    def factory(self, target, cipher):
        username, password = target.get_credentials(cipher)
        conn = DeviceConnection("mock", 8088, username, password, https=False)
        if target.name in self.broken:

            def refuse(request):
                raise httpx.ConnectError("link down")

            return OceanProtectDriver(conn, transport=httpx.MockTransport(refuse))
        return OceanProtectDriver(conn, transport=httpx.ASGITransport(app=self.mocks[target.name]))

    def tick(self, seconds: float):
        """Move the CMP clock and every mock appliance's clock forward together."""
        self.clock += timedelta(seconds=seconds)
        for app in self.mocks.values():
            app.state.mock.advance_time(seconds)

    async def ok(self, method, url, status=None, **kw):
        r = await self.http.request(method, f"/api/v1{url}", **kw)
        allowed = (status,) if status else (200, 201)
        assert r.status_code in allowed, r.text
        return r.json() if r.content else None

    async def err(self, method, url, status, **kw):
        r = await self.http.request(method, f"/api/v1{url}", **kw)
        assert r.status_code == status, r.text
        return r.json()

    async def add_target(self, name="op-1", seed=1, model="x8000", **mock_options):
        self.mocks[name] = create_mock(seed=seed, **mock_options)
        return await self.ok(
            "POST",
            "/targets",
            json={
                "name": name,
                "ip_address": "mock",
                "username": "admin",
                "password": MOCK_PASSWORD,
                "model": model,
            },
        )

    async def collect(self, concurrency=1):
        return await poll_all(self.maker, self.factory, get_cipher(), now=self.clock, concurrency=concurrency)

    async def audit(self, **params):
        return (await self.ok("GET", "/audit-logs", params=params))["items"]

    async def add_policy(self, name="daily", **extra):
        body = {
            "name": name,
            "cron_schedule": "0 1 * * *",
            "backup_type": "full",
            "retention_days": 30,
            **extra,
        }
        return await self.ok("POST", "/backup-policies", json=body)

    async def add_asset(self, target, name="vm-prod-01", asset_type="vmware", **extra):
        body = {"backup_target_id": target["id"], "name": name, "asset_type": asset_type, **extra}
        return await self.ok("POST", "/assets", json=body)

    async def start(self, asset, **extra):
        return await self.ok("POST", "/backup-jobs", status=202, json={"asset_id": asset["id"], **extra})

    async def sync(self):
        return await sync_active_jobs(self.maker, self.factory, get_cipher())

    def next_backup(self, name="op-1", **hook):
        self.mock(name).next_backup = hook

    def pin(self, name="op-1", write=4000, read=900, iops=20000, streams=30):
        self.mock(name).perf_override = {
            "write_mbps": write,
            "read_mbps": read,
            "iops": iops,
            "streams_per_controller": streams,
        }


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

    async def session_override():
        async with maker() as session:
            yield session

    app = create_app()
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://cmp", headers=KEY
    )
    e = Env(http, maker)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_driver_factory] = lambda: e.factory
    app.dependency_overrides[get_now] = lambda: e.clock
    yield e
    await http.aclose()
    await engine.dispose()
