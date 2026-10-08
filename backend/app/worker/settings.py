"""ARQ worker: periodic telemetry polling of registered storage devices."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.core.database import get_sessionmaker
from app.drivers import DeviceConnection, StorageDriverError, create_driver
from app.models import HealthStatus, StorageDevice


async def poll_device(ctx: dict, device_id: str) -> dict:
    """Log in to one array, read alarms and capacity, and update its health status."""
    async with get_sessionmaker()() as session:
        device = await session.get(StorageDevice, uuid.UUID(device_id))
        if device is None:
            return {"device": device_id, "status": "missing"}
        username, password = device.get_credentials(get_cipher())
        connection = DeviceConnection(device.ip_address, device.management_port, username, password)
        try:
            async with create_driver(device.model, connection) as driver:
                alarms = await driver.get_active_alarms()
                capacity = await driver.get_capacity_metrics()
        except StorageDriverError:
            device.health_status = HealthStatus.FAULT
            result = {"device": device_id, "status": "unreachable"}
        else:
            severe = any(a.severity in ("major", "critical") for a in alarms)
            device.health_status = HealthStatus.DEGRADED if severe else HealthStatus.HEALTHY
            device.last_seen_at = datetime.now(UTC)
            result = {
                "device": device_id,
                "status": device.health_status.value,
                "used_percent": capacity.used_percent,
            }
        await session.commit()
        return result


async def poll_all_devices(ctx: dict) -> int:
    async with get_sessionmaker()() as session:
        ids = (await session.scalars(select(StorageDevice.id))).all()
    for device_id in ids:
        await poll_device(ctx, str(device_id))
    return len(ids)


class WorkerSettings:
    functions = [poll_device]
    cron_jobs = [cron(poll_all_devices, minute=set(range(0, 60, 5)))]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
