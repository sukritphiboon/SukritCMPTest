import pytest

from app.drivers import DeviceConnection, DoradoV7Driver
from app.drivers.errors import (
    AuthenticationError,
    ConnectionFailedError,
    InsufficientSpaceError,
    ResourceExistsError,
    ResourceNotFoundError,
)
from tests.conftest import MOCK_PASSWORD


async def test_lun_lifecycle(dorado):
    lun = await dorado.create_lun("db-data", 100)
    assert lun.size_gb == 100 and lun.thin and len(lun.wwn) == 32
    grown = await dorado.expand_lun(lun.array_id, 250)
    assert grown.size_gb == 250
    await dorado.delete_lun(lun.array_id)
    with pytest.raises(ResourceNotFoundError):
        await dorado.delete_lun(lun.array_id)


async def test_thick_lun_and_duplicates(dorado):
    lun = await dorado.create_lun("thick", 10, thin=False)
    assert not lun.thin
    with pytest.raises(ResourceExistsError):
        await dorado.create_lun("thick", 10)


async def test_insufficient_space(dorado):
    with pytest.raises(InsufficientSpaceError):
        await dorado.create_lun("huge", 10_000_000)


async def test_mapping_and_snapshot(dorado):
    lun = await dorado.create_lun("vol1", 50)
    group = await dorado.create_lun_group("lg1", [lun.array_id])
    mapping = await dorado.map_to_host(group.array_id, "esx-01")
    assert mapping.lun_group_id == group.array_id
    with pytest.raises(ResourceExistsError):
        await dorado.map_to_host(group.array_id, "esx-01")
    snap = await dorado.create_snapshot(lun.array_id, "snap-1")
    assert snap.resource_type == "lun" and snap.resource_id == lun.array_id
    # a second LUN group can reuse the same host
    other = await dorado.create_lun_group("lg2")
    assert (await dorado.map_to_host(other.array_id, "esx-01")).host_id == mapping.host_id


async def test_file_services(dorado):
    fs = await dorado.create_filesystem("fs-home", 20)
    assert fs.size_gb == 20
    nfs = await dorado.create_share(fs.array_id, "nfs")
    assert nfs.path == "/fs-home/"
    cifs = await dorado.create_share(fs.array_id, "cifs")
    assert cifs.name == "fs-home"
    quota = await dorado.set_quota(fs.array_id, 10)
    assert quota.hard_gb == 10 and quota.soft_gb == 8
    snap = await dorado.create_snapshot(fs.array_id, "fs-snap", "filesystem")
    assert snap.resource_type == "filesystem"


async def test_object_services(dorado):
    creds = await dorado.generate_s3_credentials("tenant-a")
    assert creds.access_key.startswith("AK") and creds.secret_key not in repr(creds)
    bucket = await dorado.create_bucket("tenant-a-data", "tenant-a", quota_gb=5)
    assert bucket.quota_gb == 5 and bucket.endpoint.endswith("/s3")
    assert (await dorado.set_bucket_quota("tenant-a-data", 50)).quota_gb == 50


async def test_telemetry(dorado):
    await dorado.create_lun("t1", 500, thin=False)
    capacity = await dorado.get_capacity_metrics()
    assert capacity.total_gb > 100_000 and capacity.provisioned_gb == 500
    assert capacity.used_gb + capacity.free_gb == pytest.approx(capacity.total_gb, abs=0.1)
    ratio = await dorado.get_reduction_ratio()
    assert ratio.dedupe > 1 and ratio.compression > 1
    alarms = await dorado.get_active_alarms()
    assert alarms and alarms[0].severity == "medium"
    perf = await dorado.get_performance_metrics()
    assert perf.iops >= 300_000 and perf.latency_ms < 1.0


async def test_bad_credentials_and_unreachable(dorado):
    import httpx
    from mock_server.main import create_app

    bad = DoradoV7Driver(
        DeviceConnection("mock", https=False, password="wrong"),
        transport=httpx.ASGITransport(app=create_app("dorado")),
    )
    with pytest.raises(AuthenticationError):
        await bad.connect()

    def refuse(request):
        raise httpx.ConnectError("refused")

    down = DoradoV7Driver(
        DeviceConnection("mock", https=False, password=MOCK_PASSWORD), transport=httpx.MockTransport(refuse)
    )
    with pytest.raises(ConnectionFailedError):
        await down.connect()


async def test_transparent_relogin_after_session_expiry(dorado):
    dorado.client._http._transport.app.state.mock.expire_all_sessions()
    assert (await dorado.get_capacity_metrics()).total_gb > 0
