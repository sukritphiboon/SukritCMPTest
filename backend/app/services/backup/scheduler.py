"""Run backup policies on their cron schedule (APScheduler, inside the ARQ worker process).

How it fits together
* Every enabled policy has one APScheduler job (id ``policy:<uuid>``) built from its ``cron_schedule``.
* ``reconcile`` makes the jobs match the database. A job runs it every ``scheduler_sync_seconds``, so a
  changed, disabled or deleted policy takes effect within that time (no message between processes needed).
* When a job fires, ``run_policy`` starts a backup (``trigger_job``) for every asset of the policy. An asset
  that already has a backup in progress is skipped, and one failing asset never stops the others.
* Several workers may run this code: each firing is *claimed* first (a Redis ``SET NX`` in production), so
  only one of them starts the backups.

Limits: the schedule lives in memory. A run that could not start within ``misfire_grace_time`` of its time
(worker down, overloaded) is skipped, not repeated later.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.cron import CronError, parse_cron, slot_time
from app.drivers import StorageDriverError
from app.models import BackupJob, BackupPolicy, ProtectedAsset
from app.schemas.orchestration import AssetRunResult, JobCreate, PolicyRunOut

from ..audit import audited
from ..context import Ctx, DriverFactory
from .jobs import ACTIVE, trigger_job

log = logging.getLogger(__name__)
JOB_PREFIX = "policy:"
Claim = Callable[[str], Awaitable[bool]]


def get_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"CMP_SCHEDULER_TIMEZONE '{name}' is not a known timezone (for example Asia/Bangkok)"
        ) from exc


class MemoryClaim:
    """Single-process stand-in for the Redis claim (tests and scripts)."""

    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def __call__(self, key: str) -> bool:
        if key in self.keys:
            return False
        self.keys.add(key)
        return True


def redis_claim(redis, ttl_seconds: int = 3600) -> Claim:
    async def claim(key: str) -> bool:
        return bool(await redis.set(key, "1", nx=True, ex=ttl_seconds))

    return claim


# ---- one policy run ---------------------------------------------------------------------------
@dataclass
class RunResult:
    policy_id: uuid.UUID
    policy_name: str | None
    status: str  # ran | duplicate | not_found | disabled
    results: list[AssetRunResult] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return sum(1 for r in self.results if r.outcome == outcome)

    def to_out(self) -> PolicyRunOut:
        return PolicyRunOut(
            policy_id=self.policy_id,
            policy_name=self.policy_name,
            status=self.status,  # type: ignore[arg-type]
            started=self.count("started"),
            skipped=self.count("skipped"),
            failed=self.count("failed"),
            results=self.results,
        )


async def run_policy(
    maker: async_sessionmaker[AsyncSession],
    factory: DriverFactory,
    policy_id: uuid.UUID,
    fire_time: datetime,
    *,
    actor: str = "scheduler",
    claim: Claim | None = None,
    parallel: int = 4,
    action: str = "schedule.run",
    source_ip: str | None = None,
) -> RunResult:
    """Start a backup for every asset of the policy. With ``claim`` only one caller runs a given firing."""
    if claim is not None and not await claim(f"cmp:sched:{policy_id}:{fire_time.isoformat()}"):
        log.info("Policy %s at %s was already started by another worker", policy_id, fire_time)
        return RunResult(policy_id, None, "duplicate")

    async with maker() as session:
        policy = await session.get(BackupPolicy, policy_id)
        if policy is None:
            return RunResult(policy_id, None, "not_found")
        name, enabled = policy.name, policy.enabled
        if not enabled:
            return RunResult(policy_id, name, "disabled")
        assets = (
            await session.execute(
                select(ProtectedAsset.id, ProtectedAsset.name)
                .where(ProtectedAsset.policy_id == policy_id)
                .order_by(ProtectedAsset.name)
            )
        ).all()
        busy = set(
            (
                await session.scalars(
                    select(BackupJob.asset_id).where(
                        BackupJob.asset_id.in_([a.id for a in assets]), BackupJob.status.in_(ACTIVE)
                    )
                )
            ).all()
        )

    gate = asyncio.Semaphore(max(parallel, 1))

    async def start(asset_id: uuid.UUID, asset_name: str) -> AssetRunResult:
        if asset_id in busy:
            return AssetRunResult(
                asset_id=asset_id,
                asset_name=asset_name,
                outcome="skipped",
                detail="a backup is already in progress",
            )
        async with gate, maker() as s:
            ctx = Ctx(s, actor, factory, source_ip)
            try:
                job = await trigger_job(ctx, JobCreate(asset_id=asset_id, policy_id=policy_id))
            except HTTPException as exc:
                # lost a race with another start: not an error, the asset is being backed up
                in_progress = exc.status_code == 409 and "in progress" in str(exc.detail)
                return AssetRunResult(
                    asset_id=asset_id,
                    asset_name=asset_name,
                    outcome="skipped" if in_progress else "failed",
                    detail=str(exc.detail),
                )
            except StorageDriverError as exc:
                return AssetRunResult(
                    asset_id=asset_id, asset_name=asset_name, outcome="failed", detail=str(exc)
                )
            except Exception as exc:  # one broken asset must not stop the others
                log.exception("Scheduled backup of %s failed unexpectedly", asset_name)
                return AssetRunResult(
                    asset_id=asset_id,
                    asset_name=asset_name,
                    outcome="failed",
                    detail=f"unexpected error: {exc}",
                )
            return AssetRunResult(asset_id=asset_id, asset_name=asset_name, outcome="started", job_id=job.id)

    result = RunResult(
        policy_id, name, "ran", list(await asyncio.gather(*(start(a.id, a.name) for a in assets)))
    )
    async with maker() as s:  # one audit entry for the run itself (each backup also has its own)
        async with audited(
            s,
            actor,
            action,
            "policy",
            resource_id=str(policy_id),
            resource_name=name,
            source_ip=source_ip,
            details={
                "fire_time": fire_time.isoformat(),
                "assets": len(assets),
                "started": result.count("started"),
                "skipped": result.count("skipped"),
                "failed": result.count("failed"),
            },
        ):
            pass
    return result


# ---- keeping the scheduler in line with the database ---------------------------------------------------
@dataclass(frozen=True)
class PolicyRow:
    id: uuid.UUID
    name: str
    cron_schedule: str
    enabled: bool


@dataclass
class ReconcileResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)  # policies whose stored cron cannot be used


def reconcile(
    scheduler: AsyncIOScheduler,
    policies: Iterable[PolicyRow],
    fire: Callable[..., Awaitable[None]],
    tz: tzinfo,
    *,
    misfire_grace_seconds: int = 300,
) -> ReconcileResult:
    """Add, change or remove scheduler jobs to match the enabled policies. Unchanged jobs are left alone."""
    result = ReconcileResult()
    wanted: dict[str, tuple[PolicyRow, CronTrigger]] = {}
    for policy in policies:
        if not policy.enabled:
            continue
        try:
            wanted[f"{JOB_PREFIX}{policy.id}"] = (policy, parse_cron(policy.cron_schedule, tz))
        except CronError as exc:
            result.invalid.append(policy.name)
            log.warning(
                "Policy '%s' has a schedule that cannot be used and will not run: %s", policy.name, exc
            )
    existing = {j.id: j for j in scheduler.get_jobs() if j.id.startswith(JOB_PREFIX)}
    for job_id in existing.keys() - wanted.keys():
        scheduler.remove_job(job_id)
        result.removed.append(job_id)
    for job_id, (policy, trigger) in wanted.items():
        job = existing.get(job_id)
        if job is None:
            scheduler.add_job(
                fire,
                trigger=trigger,
                id=job_id,
                name=policy.name,
                kwargs={"policy_id": str(policy.id)},
                coalesce=True,
                max_instances=1,
                misfire_grace_time=misfire_grace_seconds,
                replace_existing=True,
            )
            result.added.append(job_id)
        elif (str(job.trigger), str(job.trigger.timezone)) != (str(trigger), str(trigger.timezone)):
            scheduler.reschedule_job(job_id, trigger=trigger)
            result.updated.append(job_id)
        if job is not None and job.name != policy.name:
            scheduler.modify_job(job_id, name=policy.name)
    return result


def build_scheduler(
    maker: async_sessionmaker[AsyncSession],
    factory: DriverFactory,
    *,
    timezone: str = "Asia/Bangkok",
    sync_seconds: int = 30,
    misfire_grace_seconds: int = 300,
    max_parallel_starts: int = 4,
    claim: Claim | None = None,
) -> AsyncIOScheduler:
    """A scheduler with one job per enabled policy. Call ``scheduler.start()`` inside a running event loop."""
    tz = get_timezone(timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    claim = claim or MemoryClaim()

    async def fire(policy_id: str) -> None:
        job = scheduler.get_job(f"{JOB_PREFIX}{policy_id}")
        now = datetime.now(UTC)
        fire_time = slot_time(job.trigger, now) if job is not None else now.replace(second=0, microsecond=0)
        try:
            outcome = await run_policy(
                maker, factory, uuid.UUID(policy_id), fire_time, claim=claim, parallel=max_parallel_starts
            )
            log.info(
                "Policy %s (%s): %s, %d started, %d skipped, %d failed",
                outcome.policy_name,
                policy_id,
                outcome.status,
                outcome.count("started"),
                outcome.count("skipped"),
                outcome.count("failed"),
            )
        except Exception:  # never let a job error stop the scheduler
            log.exception("Scheduled run of policy %s failed", policy_id)

    async def sync() -> None:
        try:
            async with maker() as session:
                rows = [
                    PolicyRow(p.id, p.name, p.cron_schedule, p.enabled)
                    for p in (await session.scalars(select(BackupPolicy))).all()
                ]
            changes = reconcile(scheduler, rows, fire, tz, misfire_grace_seconds=misfire_grace_seconds)
            if changes.added or changes.updated or changes.removed:
                log.info(
                    "Backup schedule changed: %d added, %d updated, %d removed",
                    len(changes.added),
                    len(changes.updated),
                    len(changes.removed),
                )
        except Exception:
            log.exception("Could not refresh the backup schedule; the previous schedule stays in force")

    scheduler.add_job(
        sync,
        trigger="interval",
        seconds=sync_seconds,
        id="scheduler:sync",
        name="refresh backup schedule",
        coalesce=True,
        max_instances=1,
    )
    scheduler.sync = sync  # type: ignore[attr-defined]  # lets the worker load the schedule before the first interval
    scheduler.fire = fire  # type: ignore[attr-defined]
    return scheduler
