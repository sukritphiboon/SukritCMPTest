"""ARQ worker: polls every appliance on a fixed rhythm (default every 30 seconds)."""

from __future__ import annotations

import logging
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.core.database import get_sessionmaker
from app.services.backup.jobs import sync_active_jobs
from app.services.context import default_driver_factory
from app.services.telemetry.collector import housekeeping, poll_all

log = logging.getLogger(__name__)


def poll_seconds(interval: int) -> set[int]:
    """Seconds of a minute at which to poll, for example 30 -> {0, 30}. The interval must divide 60."""
    if interval < 1 or 60 % interval:
        raise ValueError(
            "The interval must be a divisor of 60 (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30 or 60 seconds)"
        )
    return set(range(0, 60, interval))


async def startup(ctx: dict[str, Any]) -> None:
    ctx.setdefault("maker", get_sessionmaker())
    ctx.setdefault("factory", default_driver_factory)
    ctx.setdefault("cipher", get_cipher())


async def collect_metrics(ctx: dict[str, Any]) -> dict[str, int]:
    """Poll all appliances once. Failures are stored as target health, never raised."""
    results = await poll_all(
        ctx["maker"], ctx["factory"], ctx["cipher"], concurrency=get_settings().telemetry_concurrency
    )
    failed = sum(1 for r in results if not r.ok)
    if failed:
        log.warning("%d of %d appliances could not be polled", failed, len(results))
    return {"polled": len(results), "failed": failed}


async def sync_backup_jobs(ctx: dict[str, Any]) -> dict[str, int]:
    """Follow running backups on their appliances and store progress and final results."""
    result = await sync_active_jobs(ctx["maker"], ctx["factory"], ctx["cipher"])
    return {
        "checked": result.checked,
        "updated": result.updated,
        "finished": result.finished,
        "lost": result.lost,
        "unreachable_targets": len(result.unreachable),
    }


async def purge_old_data(ctx: dict[str, Any]) -> dict[str, int]:
    return await housekeeping(ctx["maker"])


class WorkerSettings:
    on_startup = startup
    functions = [collect_metrics, sync_backup_jobs, purge_old_data]
    cron_jobs = [
        cron(
            collect_metrics,
            second=poll_seconds(get_settings().telemetry_interval_seconds),
            run_at_startup=True,
        ),
        cron(sync_backup_jobs, second=poll_seconds(get_settings().job_poll_interval_seconds)),
        cron(purge_old_data, hour=3, minute=10, second=0),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
