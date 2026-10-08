import pytest

from app.drivers import NotSupportedError
from app.drivers.errors import ComplianceLockError


async def test_block_and_object_not_supported(oceanprotect):
    with pytest.raises(NotSupportedError):
        await oceanprotect.create_lun("x", 10)
    with pytest.raises(NotSupportedError):
        await oceanprotect.map_to_host("1", "host")
    with pytest.raises(NotSupportedError):
        await oceanprotect.create_bucket("b", "o")
    with pytest.raises(NotSupportedError):
        await oceanprotect.generate_s3_credentials("o")
    with pytest.raises(NotSupportedError):
        await oceanprotect.delete_bucket("b")


async def test_extreme_reduction_ratio(oceanprotect):
    ratio = await oceanprotect.get_reduction_ratio()
    assert 20 <= ratio.overall <= 42
    assert ratio.thin < 1.5


async def test_throughput_oriented_performance(oceanprotect):
    perf = await oceanprotect.get_performance_metrics()
    assert perf.throughput_mbps >= 6000
    assert perf.latency_ms >= 1.0


async def test_filesystem_and_worm(oceanprotect):
    fs = await oceanprotect.create_filesystem("repo-01", 1000)
    policy = await oceanprotect.create_worm_policy(fs.array_id, "worm-30", 30)
    assert policy.mode == "compliance" and policy.retention_days == 30
    with pytest.raises(ComplianceLockError):
        await oceanprotect.client.request("DELETE", f"/filesystem/{fs.array_id}")


async def test_backup_copies(oceanprotect):
    copies = {c.name: c for c in await oceanprotect.list_backup_copies()}
    assert copies["vm-prod-db-01"].state == "locked" and copies["vm-prod-db-01"].worm_locked
    assert copies["vm-dev-test"].state == "expired"
    assert copies["vm-prod-db-01"].expires_at > copies["vm-prod-db-01"].created_at
    alarms = await oceanprotect.get_active_alarms()
    assert alarms[0].severity == "low"


async def test_snapshot_of_filesystem(oceanprotect):
    fs = await oceanprotect.create_filesystem("repo-02", 10)
    snap = await oceanprotect.create_snapshot(fs.array_id, "s", "filesystem")
    assert snap.resource_type == "filesystem"
