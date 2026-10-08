from datetime import UTC, timedelta

import pytest
from sqlalchemy import func, select

from app.core.crypto import get_cipher
from app.models import (
    AlarmRecord,
    AlarmSeverity,
    BackupTarget,
    CapacityMetric,
    HardwareSnapshot,
    ThroughputSample,
)
from app.schemas.backup import AlarmInfo
from app.services.telemetry.collector import housekeeping, sync_alarms
from tests.api.conftest import GB, T0
from tests.conftest import MOCK_PASSWORD


async def rows(env, model, **where):
    async with env.maker() as s:
        return list((await s.scalars(select(model).filter_by(**where))).all())


async def target_row(env, name="op-1"):
    async with env.maker() as s:
        return (await s.scalars(select(BackupTarget).where(BackupTarget.name == name))).one()


async def test_one_poll_stores_every_kind_of_reading(env):
    await env.add_target()
    env.pin(write=4000, read=900, iops=20000, streams=30)
    [result] = await env.collect()
    assert result.ok and result.error is None

    [tp] = await rows(env, ThroughputSample)
    assert (tp.write_throughput_mb_s, tp.read_throughput_mb_s, tp.iops, tp.active_streams) == (
        4000,
        900,
        20000,
        60,
    )

    [cap] = await rows(env, CapacityMetric)
    n = env.mock().pool_numbers()
    assert cap.raw_capacity_gb == pytest.approx(n["raw"] / GB, rel=1e-6)
    assert cap.used_physical_gb == pytest.approx(n["physical"] / GB, rel=1e-6)
    assert cap.logic_written_gb == pytest.approx(n["ingested"] / GB, rel=1e-6)
    assert cap.post_dedup_gb == pytest.approx(n["post_dedup"] / GB, rel=1e-6)
    assert cap.reduction_ratio == pytest.approx(cap.logic_written_gb / cap.used_physical_gb)
    assert cap.dedup_ratio == pytest.approx((cap.logic_written_gb - cap.post_dedup_gb) / cap.logic_written_gb)
    assert cap.compression_ratio == pytest.approx(cap.post_dedup_gb / cap.used_physical_gb)
    assert 20 <= cap.reduction_ratio <= 42
    assert cap.timestamp.replace(tzinfo=UTC) == T0

    [hw] = await rows(env, HardwareSnapshot)
    assert hw.overall_status.value == "ok" and hw.summary is None
    assert {k: len(v) for k, v in hw.components.items()} == {
        "controllers": 2,
        "nvram": 2,
        "power_modules": 4,
        "disks": 24,
    }
    ctrl = env.mock().hardware["controller"]
    assert hw.max_cpu_percent == max(c["CPUUSAGE"] for c in ctrl.values())
    assert hw.max_memory_percent == max(c["MEMORYUSAGE"] for c in ctrl.values())

    t = await target_row(env)
    assert t.serial_number == env.mock().serial_number and t.device_id == env.mock().device_id
    assert t.firmware_version == "1.6.0" and t.last_error is None
    assert t.last_seen_at.replace(tzinfo=UTC) == T0


async def test_every_poll_adds_a_new_sample(env):
    await env.add_target()
    for _ in range(3):
        env.tick(30)
        await env.collect()
    assert len(await rows(env, ThroughputSample)) == len(await rows(env, CapacityMetric)) == 3
    assert len(await rows(env, HardwareSnapshot)) == 3


async def test_alarms_are_classified_and_synced(env):
    t = await env.add_target()
    await env.collect()
    alarms = {a.name: a for a in await rows(env, AlarmRecord)}
    assert alarms["Storage pool capacity usage is above 40%"].severity == AlarmSeverity.MAJOR
    assert alarms["Backup copy retention is about to expire"].severity == AlarmSeverity.WARNING
    assert (await target_row(env)).health_status.value == "degraded"  # a major alarm degrades health

    env.tick(30)
    await env.collect()  # same alarms again: no duplicates, last_seen moves
    assert len(await rows(env, AlarmRecord)) == 2
    assert all(
        a.last_seen_at.replace(tzinfo=UTC) == T0 + timedelta(seconds=30) for a in await rows(env, AlarmRecord)
    )

    env.mock().add_alarm("Critical", "Controller 0B offline", "0xF00CF0300")
    env.tick(30)
    await env.collect()
    assert len(await rows(env, AlarmRecord)) == 3
    assert (await target_row(env)).health_status.value == "fault"
    assert t["id"]


async def test_alarm_that_disappears_is_cleared_and_ack_survives_polls(env):
    await env.add_target()
    await env.collect()
    first = (await rows(env, AlarmRecord))[0]
    async with env.maker() as s:
        rec = await s.get(AlarmRecord, first.id)
        rec.acknowledged, rec.acknowledged_by = True, "tester"
        await s.commit()
    env.tick(30)
    await env.collect()
    assert (await rows(env, AlarmRecord, id=first.id))[0].acknowledged is True

    env.mock().clear_alarm(first.sequence)
    env.tick(30)
    await env.collect()
    cleared = (await rows(env, AlarmRecord, id=first.id))[0]
    assert cleared.cleared_at is not None and cleared.acknowledged is True
    assert sum(1 for a in await rows(env, AlarmRecord) if a.cleared_at is None) == 1


async def test_a_raised_again_alarm_starts_a_new_occurrence(env):
    await env.add_target()
    await env.collect()
    tid = (await target_row(env)).id
    info = AlarmInfo(
        sequence="99", event_id="E1", name="Fan fault", severity=AlarmSeverity.WARNING, raised_at=T0
    )
    async with env.maker() as s:
        await sync_alarms(s, tid, [info], T0)
        await s.commit()
        rec = (await s.scalars(select(AlarmRecord).where(AlarmRecord.sequence == "99"))).one()
        rec.acknowledged, rec.acknowledged_by = True, "tester"
        await s.commit()
        await sync_alarms(s, tid, [], T0 + timedelta(minutes=1))
        await s.commit()
        assert rec.cleared_at is not None
        await sync_alarms(s, tid, [info], T0 + timedelta(minutes=2))
        await s.commit()
        assert rec.cleared_at is None and rec.acknowledged is False and rec.acknowledged_by is None
        assert (
            await s.scalar(select(func.count()).select_from(AlarmRecord).where(AlarmRecord.sequence == "99"))
        ) == 1


async def test_hardware_fault_sets_health_and_summary(env):
    await env.add_target()
    env.mock().set_health("disk", "7", "fault")
    env.mock().set_health("power", "PSU1", "degraded")
    env.mock().clear_alarm("2")  # remove the seeded major alarm so only hardware speaks
    await env.collect()
    [hw] = await rows(env, HardwareSnapshot)
    assert hw.overall_status.value == "fault"
    assert "DAE000.7: fault" in hw.summary and "Power module 1: degraded" in hw.summary
    assert (await target_row(env)).health_status.value == "fault"


async def test_health_recovers_when_the_problem_is_gone(env):
    await env.add_target()
    env.mock().set_health("disk", "7", "fault")
    await env.collect()
    assert (await target_row(env)).health_status.value == "fault"
    env.mock().set_health("disk", "7", "ok")
    env.mock().clear_alarm("2")
    env.tick(30)
    await env.collect()
    assert (await target_row(env)).health_status.value == "healthy"


async def test_a_broken_appliance_does_not_stop_the_others(env):
    good = await env.add_target("good", seed=1)
    bad = await env.add_target("bad", seed=2)
    await env.ok("PATCH", f"/targets/{bad['id']}", json={"password": "wrong"})
    results = {r.target_id.hex: r for r in await env.collect()}
    assert results[good["id"].replace("-", "")].ok
    failed = results[bad["id"].replace("-", "")]
    assert not failed.ok and "incorrect" in failed.error
    assert len(await rows(env, CapacityMetric)) == 1  # only the good appliance
    bad_row = await target_row(env, "bad")
    assert bad_row.health_status.value == "fault" and "incorrect" in bad_row.last_error
    assert await rows(env, ThroughputSample, backup_target_id=bad_row.id) == []  # nothing invented

    await env.ok("PATCH", f"/targets/{bad['id']}", json={"password": MOCK_PASSWORD})
    env.tick(30)
    assert all(r.ok for r in await env.collect())
    recovered = await target_row(env, "bad")
    assert recovered.last_error is None and recovered.health_status.value != "fault"


async def test_unreachable_appliance_is_marked_fault(env):
    await env.add_target()
    env.broken.add("op-1")
    [result] = await env.collect()
    assert not result.ok and "link down" in (await target_row(env)).last_error
    assert await rows(env, CapacityMetric) == []


async def test_parallel_polling_gives_the_same_result(env):
    for i in range(3):
        await env.add_target(f"op-{i}", seed=i)
    results = await env.collect(concurrency=1)
    assert [r.ok for r in results] == [True] * 3
    assert len(await rows(env, CapacityMetric)) == 3


async def test_housekeeping_trims_old_data_but_keeps_one_capacity_point_per_day(env):
    await env.add_target()
    await env.collect()
    tid = (await target_row(env)).id
    old = T0 - timedelta(days=40)
    recent_old = T0 - timedelta(days=20)
    async with env.maker() as s:
        s.add(
            ThroughputSample(
                backup_target_id=tid, timestamp=old, write_throughput_mb_s=1, read_throughput_mb_s=1
            )
        )
        s.add(
            ThroughputSample(
                backup_target_id=tid,
                timestamp=T0 - timedelta(days=1),
                write_throughput_mb_s=1,
                read_throughput_mb_s=1,
            )
        )
        for hour in (1, 5, 9):  # three readings on one old day
            s.add(
                CapacityMetric(
                    backup_target_id=tid,
                    timestamp=recent_old.replace(hour=hour),
                    raw_capacity_gb=1,
                    used_physical_gb=hour,
                    logic_written_gb=1,
                    post_dedup_gb=1,
                    dedup_ratio=0,
                    compression_ratio=1,
                    reduction_ratio=1,
                )
            )
        await s.commit()
    removed = await housekeeping(env.maker, now=T0)
    assert removed["throughput"] == 1 and removed["capacity"] == 2
    kept = [
        c.used_physical_gb for c in await rows(env, CapacityMetric) if c.timestamp.replace(tzinfo=UTC) < T0
    ]
    assert kept == [9]  # the last reading of that day
    assert len(await rows(env, ThroughputSample)) == 2  # the poll sample and yesterday's


async def test_get_cipher_is_configured():
    assert get_cipher() is not None
