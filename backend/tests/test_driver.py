import httpx
import pytest
from mock_server.main import create_app

from app.drivers import DeviceConnection, OceanProtectDriver, create_driver
from app.drivers.errors import (
    AuthenticationError,
    ComplianceLockError,
    ConnectionFailedError,
    ResourceExistsError,
    ResourceNotFoundError,
)
from app.models import AlarmSeverity, BackupType, HardwareHealth, JobStatus
from app.services.analytics import ratios
from tests.conftest import MOCK_PASSWORD

GB = 1024**3


async def test_system_info(driver):
    info = await driver.get_system_info()
    assert info.model == "OceanProtect X8000" and info.serial_number == driver.mock.serial_number
    assert info.device_id == driver.mock.device_id


async def test_pool_metrics_follow_the_formulas(driver):
    pool = await driver.get_pool_metrics()
    n = driver.mock.pool_numbers()
    assert pool.raw_capacity_gb == pytest.approx(n["raw"] / GB, rel=1e-6)
    assert pool.used_physical_gb == pytest.approx(n["physical"] / GB, rel=1e-6)
    assert pool.logic_written_gb == pytest.approx(n["ingested"] / GB, rel=1e-6)
    assert pool.post_dedup_gb == pytest.approx(n["post_dedup"] / GB, rel=1e-6)
    assert pool.free_gb == pytest.approx(pool.raw_capacity_gb - pool.used_physical_gb, abs=0.01)
    assert pool.reduction_ratio == pytest.approx(
        ratios.reduction_ratio(n["ingested"], n["physical"]), rel=1e-6
    )
    assert pool.dedup_ratio == pytest.approx(ratios.dedup_ratio(n["ingested"], n["post_dedup"]), rel=1e-6)
    assert pool.compression_ratio == pytest.approx(driver.mock.compression_x, rel=1e-3)
    assert pool.dedup_factor == pytest.approx(driver.mock.dedupe_x, rel=1e-3)
    assert 20 <= pool.reduction_ratio <= 42


async def test_performance_and_hardware(driver):
    driver.mock.perf_override = {
        "write_mbps": 4000,
        "read_mbps": 900,
        "iops": 20000,
        "streams_per_controller": 25,
    }
    perf = await driver.get_performance()
    assert (perf.write_throughput_mb_s, perf.read_throughput_mb_s, perf.iops) == (4000, 900, 20000)
    assert perf.active_streams == 50  # two controllers

    hw = await driver.get_hardware_status()
    assert (len(hw.controllers), len(hw.nvram), len(hw.power_modules), len(hw.disks)) == (2, 2, 4, 24)
    assert hw.overall == HardwareHealth.OK
    assert hw.controllers[0].cpu_percent == int(driver.mock.hardware["controller"]["0A"]["CPUUSAGE"])
    assert hw.nvram[0].controller == "0A" and 0 <= hw.nvram[0].dedup_cache_hit_percent <= 100
    assert hw.disks[0].location == "DAE000.0" and hw.disks[23].role == "hot-spare"


async def test_hardware_health_mapping(driver):
    driver.mock.set_health("power", "PSU2", "degraded")
    assert (await driver.get_hardware_status()).overall == HardwareHealth.DEGRADED
    driver.mock.set_health("disk", "5", "fault")
    hw = await driver.get_hardware_status()
    assert hw.overall == HardwareHealth.FAULT and hw.disks[5].status == HardwareHealth.FAULT


async def test_alarm_severities(driver):
    driver.mock.add_alarm("Critical", "Controller down", "0xF00CF0200")
    alarms = {a.name: a for a in await driver.get_active_alarms()}
    assert alarms["Controller down"].severity == AlarmSeverity.CRITICAL
    assert alarms["Storage pool capacity usage is above 40%"].severity == AlarmSeverity.MAJOR
    assert alarms["Backup copy retention is about to expire"].severity == AlarmSeverity.WARNING
    assert all(a.raised_at.tzinfo is not None for a in alarms.values())


async def test_policy_and_asset_management(driver):
    policy = await driver.create_policy("weekly-full", "0 2 * * 0", "full", 90, worm_enabled=True)
    assert policy.backup_type == BackupType.FULL and policy.worm_enabled and policy.retention_days == 90
    with pytest.raises(ResourceExistsError):
        await driver.create_policy("weekly-full", "0 2 * * 0", "full", 90)
    assert [p.name for p in await driver.list_policies()] == ["weekly-full"]
    asset = await driver.create_asset("esx-cluster-1", "vmware", "10.0.0.8", "1.6.0")
    assert asset.asset_type == "vmware" and asset.source_ip == "10.0.0.8"
    await driver.delete_policy(policy.array_id)
    with pytest.raises(ResourceNotFoundError):
        await driver.delete_policy(policy.array_id)


async def test_backup_is_asynchronous_and_polled_by_task_id(driver):
    asset = await driver.create_asset("db-prod", "database")
    task_id = await driver.trigger_backup(asset.array_id, backup_type="full")
    first = await driver.get_task(task_id)
    assert first.status == JobStatus.PENDING and first.started_at is None and first.ended_at is None
    driver.mock.advance_time(5)
    running = await driver.get_task(task_id)
    assert running.status == JobStatus.RUNNING and running.started_at is not None
    assert not running.status.is_final
    driver.mock.advance_time(3600)
    await driver.client.login()  # the session lifetime is simulated too
    done = await driver.get_task(task_id)
    assert done.status.is_final and done.ended_at > done.started_at
    assert done.data_transferred_gb > 0 and done.logs[0].startswith("Task")


async def test_cancel_task(driver):
    asset = await driver.create_asset("fs-share", "file_share")
    task_id = await driver.trigger_backup(asset.array_id)
    driver.mock.advance_time(10)
    assert (await driver.cancel_task(task_id)).status == JobStatus.CANCELLED
    with pytest.raises(Exception, match="already CANCELLED"):
        await driver.cancel_task(task_id)


async def test_backup_of_unknown_asset(driver):
    with pytest.raises(ResourceNotFoundError):
        await driver.trigger_backup("404")


async def test_worm_and_retention(driver):
    fs_id = await driver.create_filesystem("repo-01", 1000)
    policy = await driver.create_worm_policy(fs_id, "worm-30", 30, "compliance")
    assert policy.mode == "compliance" and policy.retention_days == 30
    with pytest.raises(ComplianceLockError):
        await driver.client.request("DELETE", f"/filesystem/{fs_id}")
    copies = {c.name: c for c in await driver.list_backup_copies()}
    assert copies["vm-prod-db-01"].state == "locked" and copies["vm-prod-db-01"].worm_locked
    assert copies["vm-dev-test"].state == "expired"
    assert copies["vm-prod-db-01"].expires_at > copies["vm-prod-db-01"].created_at


async def test_errors():
    app = create_app(seed=1)
    bad = OceanProtectDriver(
        DeviceConnection("mock", https=False, password="wrong"), transport=httpx.ASGITransport(app=app)
    )
    with pytest.raises(AuthenticationError):
        await bad.connect()

    def refuse(request):
        raise httpx.ConnectError("refused")

    down = OceanProtectDriver(
        DeviceConnection("mock", https=False, password=MOCK_PASSWORD), transport=httpx.MockTransport(refuse)
    )
    with pytest.raises(ConnectionFailedError):
        await down.connect()


async def test_transparent_relogin_after_session_expiry(driver):
    driver.mock.expire_all_sessions()
    assert (await driver.get_pool_metrics()).raw_capacity_gb > 0


def test_factory():
    conn = DeviceConnection("10.0.0.1")
    for model in ("x3000", "x6000", "x8000", "x9000"):
        assert isinstance(create_driver(model, conn), OceanProtectDriver)
    with pytest.raises(ValueError):
        create_driver("dorado_v7", conn)
