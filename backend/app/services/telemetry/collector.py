"""Metrics collector: poll one appliance, store throughput, capacity, hardware and alarms.

All readings of one poll are fetched first and stored in one transaction, so a failed poll never
leaves half a sample behind. A target that cannot be reached is marked ``fault`` and no data is
invented for it. Each target is handled independently by ``poll_all``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.crypto import SecretCipher
from app.drivers import BackupDriverBase, StorageDriverError
from app.models import (
    AlarmRecord,
    AlarmSeverity,
    BackupTarget,
    CapacityMetric,
    HardwareHealth,
    HardwareSnapshot,
    HealthStatus,
    ThroughputSample,
)
from app.schemas.backup import AlarmInfo, HardwareStatus

log = logging.getLogger(__name__)
DriverFactory = Callable[[BackupTarget, SecretCipher], BackupDriverBase]


@dataclass
class CollectionResult:
    target_id: uuid.UUID
    ok: bool
    health: HealthStatus
    error: str | None = None


def health_of(hardware: HardwareHealth, alarms: list[AlarmInfo]) -> HealthStatus:
    """fault: failed hardware or a critical alarm; degraded: degraded hardware or a major alarm."""
    severities = {a.severity for a in alarms}
    if hardware == HardwareHealth.FAULT or AlarmSeverity.CRITICAL in severities:
        return HealthStatus.FAULT
    if hardware == HardwareHealth.DEGRADED or AlarmSeverity.MAJOR in severities:
        return HealthStatus.DEGRADED
    return HealthStatus.HEALTHY


def _hardware_summary(hw: HardwareStatus) -> str | None:
    bad = [c for c in hw.all_components() if c.status != HardwareHealth.OK]
    if not bad:
        return None
    return "; ".join(f"{c.name}: {c.status.value}" for c in bad)[:255]


async def sync_alarms(
    session: AsyncSession, target_id: uuid.UUID, alarms: list[AlarmInfo], now: datetime
) -> None:
    existing = {
        (a.sequence, a.event_id): a
        for a in await session.scalars(select(AlarmRecord).where(AlarmRecord.backup_target_id == target_id))
    }
    reported = set()
    for info in alarms:
        key = (info.sequence, info.event_id)
        reported.add(key)
        record = existing.get(key)
        if record is None:
            session.add(
                AlarmRecord(
                    backup_target_id=target_id,
                    sequence=info.sequence,
                    event_id=info.event_id,
                    name=info.name,
                    severity=info.severity,
                    location=info.location,
                    raised_at=info.raised_at,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            continue
        if record.cleared_at is not None:  # raised again: a new occurrence, so acknowledgement starts over
            record.cleared_at = None
            record.first_seen_at = now
            record.acknowledged, record.acknowledged_by, record.acknowledged_at, record.ack_note = (
                False,
                None,
                None,
                None,
            )
        record.name, record.severity, record.location, record.last_seen_at = (
            info.name,
            info.severity,
            info.location,
            now,
        )
    for key, record in existing.items():
        if key not in reported and record.cleared_at is None:
            record.cleared_at = now


async def collect_target(
    session: AsyncSession, target: BackupTarget, driver: BackupDriverBase, now: datetime | None = None
) -> CollectionResult:
    """Poll a connected driver. ``now`` exists so tests can replay several days of readings."""
    now = now or datetime.now(UTC)
    try:
        results = await asyncio.gather(
            driver.get_pool_metrics(),
            driver.get_performance(),
            driver.get_hardware_status(),
            driver.get_active_alarms(),
            return_exceptions=True,
        )
        for result in results:  # report the first failure only after every call has finished
            if isinstance(result, BaseException):
                raise result
        pool, perf, hardware, alarms = results
        info = (
            await driver.get_system_info()
            if target.serial_number is None or target.device_id is None
            else None
        )
    except StorageDriverError as exc:
        return await mark_unreachable(session, target, str(exc))

    session.add_all(
        [
            CapacityMetric(
                backup_target_id=target.id,
                timestamp=now,
                raw_capacity_gb=pool.raw_capacity_gb,
                used_physical_gb=pool.used_physical_gb,
                logic_written_gb=pool.logic_written_gb,
                post_dedup_gb=pool.post_dedup_gb,
                dedup_ratio=pool.dedup_ratio,
                compression_ratio=pool.compression_ratio,
                reduction_ratio=pool.reduction_ratio,
            ),
            ThroughputSample(
                backup_target_id=target.id,
                timestamp=now,
                write_throughput_mb_s=perf.write_throughput_mb_s,
                read_throughput_mb_s=perf.read_throughput_mb_s,
                iops=perf.iops,
                active_streams=perf.active_streams,
            ),
            HardwareSnapshot(
                backup_target_id=target.id,
                timestamp=now,
                overall_status=hardware.overall,
                max_cpu_percent=max((c.cpu_percent for c in hardware.controllers), default=0.0),
                max_memory_percent=max((c.memory_percent for c in hardware.controllers), default=0.0),
                components={
                    "controllers": [c.model_dump(mode="json") for c in hardware.controllers],
                    "nvram": [n.model_dump(mode="json") for n in hardware.nvram],
                    "power_modules": [p.model_dump(mode="json") for p in hardware.power_modules],
                    "disks": [d.model_dump(mode="json") for d in hardware.disks],
                },
                summary=_hardware_summary(hardware),
            ),
        ]
    )
    await sync_alarms(session, target.id, alarms, now)
    target.health_status = health_of(hardware.overall, alarms)
    target.last_seen_at, target.last_error = now, None
    if info is not None:
        target.serial_number = target.serial_number or info.serial_number
        target.device_id = target.device_id or info.device_id
        target.firmware_version = info.firmware_version
    await session.commit()
    return CollectionResult(target.id, True, target.health_status)


async def mark_unreachable(session: AsyncSession, target: BackupTarget, error: str) -> CollectionResult:
    await session.rollback()
    target = await session.merge(target)
    target.health_status, target.last_error = HealthStatus.FAULT, error[:500]
    await session.commit()
    log.warning("Telemetry poll of %s failed: %s", target.name, error)
    return CollectionResult(target.id, False, HealthStatus.FAULT, error)


async def poll_all(
    maker: async_sessionmaker[AsyncSession],
    factory: DriverFactory,
    cipher: SecretCipher,
    *,
    now: datetime | None = None,
    concurrency: int = 4,
) -> list[CollectionResult]:
    """Poll every target; one slow or broken appliance never blocks or fails the others."""
    async with maker() as session:
        ids = list((await session.scalars(select(BackupTarget.id).order_by(BackupTarget.name))).all())
    gate = asyncio.Semaphore(max(concurrency, 1))

    async def one(target_id: uuid.UUID) -> CollectionResult:
        async with gate, maker() as session:
            target = await session.get(BackupTarget, target_id)
            if target is None:
                return CollectionResult(target_id, False, HealthStatus.UNKNOWN, "target was deleted")
            try:
                driver = factory(target, cipher)
                async with driver:
                    return await collect_target(session, target, driver, now)
            except StorageDriverError as exc:  # login failed or the link dropped
                return await mark_unreachable(session, target, str(exc))
            except Exception as exc:  # a bug must not stop the polling of other targets
                log.exception("Unexpected error while polling %s", target_id)
                await session.rollback()
                return CollectionResult(target_id, False, HealthStatus.UNKNOWN, f"unexpected error: {exc}")

    return list(await asyncio.gather(*(one(i) for i in ids)))


async def housekeeping(
    maker: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
    *,
    throughput_days: int = 30,
    hardware_days: int = 30,
    full_capacity_days: int = 14,
    capacity_days: int = 400,
) -> dict[str, int]:
    """Drop old samples; capacity readings older than ``full_capacity_days`` keep one per day."""
    now = now or datetime.now(UTC)
    removed = {"throughput": 0, "hardware": 0, "capacity": 0, "alarms": 0}
    async with maker() as session:
        for key, model, days in (
            ("throughput", ThroughputSample, throughput_days),
            ("hardware", HardwareSnapshot, hardware_days),
        ):
            cutoff = now - timedelta(days=days)
            removed[key] = (
                await session.execute(delete(model).where(model.timestamp < cutoff))
            ).rowcount or 0
        old = (
            await session.execute(
                select(CapacityMetric.id, CapacityMetric.backup_target_id, CapacityMetric.timestamp)
                .where(CapacityMetric.timestamp < now - timedelta(days=full_capacity_days))
                .order_by(CapacityMetric.backup_target_id, CapacityMetric.timestamp)
            )
        ).all()
        last_of_day: dict[tuple, tuple] = {}
        for row_id, target_id, ts in old:
            last_of_day[(target_id, ts.date())] = (ts, row_id)
        keep = {row_id for _, row_id in last_of_day.values()}
        drop = [row_id for row_id, _, _ in old if row_id not in keep]
        for i in range(0, len(drop), 500):
            await session.execute(delete(CapacityMetric).where(CapacityMetric.id.in_(drop[i : i + 500])))
        removed["capacity"] = len(drop)
        cutoff = now - timedelta(days=capacity_days)
        removed["capacity"] += (
            await session.execute(delete(CapacityMetric).where(CapacityMetric.timestamp < cutoff))
        ).rowcount or 0
        removed["alarms"] = (
            await session.execute(
                delete(AlarmRecord).where(
                    AlarmRecord.cleared_at.is_not(None), AlarmRecord.cleared_at < now - timedelta(days=90)
                )
            )
        ).rowcount or 0
        await session.commit()
    return removed
