"""Backup orchestration: trigger backups, follow their tasks on the appliance, cancel them.

A backup is asynchronous on the appliance. ``trigger_job`` starts it and stores a ``BackupJob`` with the
appliance task id; ``apply_task`` copies what the appliance reports into the job. The worker calls
``sync_active_jobs`` regularly, and the API can refresh one job on demand.
A job in a final state never changes again.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.crypto import SecretCipher
from app.drivers import BackupDriverBase, StorageDriverError
from app.drivers.errors import ResourceBusyError, ResourceNotFoundError
from app.models import (
    AuditOutcome,
    BackupJob,
    BackupPolicy,
    BackupTarget,
    BackupType,
    JobStatus,
    ProtectedAsset,
)
from app.schemas.backup import TaskInfo
from app.schemas.orchestration import JobCreate, JobOut, JobSummary

from ..context import Ctx, undo

log = logging.getLogger(__name__)
ACTIVE = (JobStatus.PENDING, JobStatus.RUNNING)
WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}


def apply_task(job: BackupJob, info: TaskInfo) -> bool:
    """Copy the appliance's view of the task into the job. Returns True when something changed."""
    if job.status.is_final:  # history is never rewritten
        return False
    before = (
        job.status,
        job.started_at,
        job.ended_at,
        job.data_transferred_gb,
        job.throughput_mb_s,
        job.log_messages,
    )
    job.status = info.status
    job.started_at = info.started_at or job.started_at
    job.ended_at = info.ended_at
    job.data_transferred_gb = round(info.data_transferred_gb, 3)
    job.throughput_mb_s = info.throughput_mb_s
    job.log_messages = list(info.logs)
    return before != (
        job.status,
        job.started_at,
        job.ended_at,
        job.data_transferred_gb,
        job.throughput_mb_s,
        job.log_messages,
    )


def mark_lost(job: BackupJob, now: datetime) -> None:
    """The appliance no longer knows the task, so its result can never be learned."""
    job.status = JobStatus.FAILED
    job.ended_at = now
    job.log_messages = [*job.log_messages, "The appliance no longer reports this task; result unknown."]


def job_out(job: BackupJob, progress: int | None = None) -> JobOut:
    out = JobOut.model_validate(job)
    out.active = job.status in ACTIVE
    out.progress_percent = progress
    for field_name in ("started_at", "ended_at", "created_at", "updated_at"):
        value = getattr(out, field_name)
        if value is not None and value.tzinfo is None:  # SQLite returns naive UTC
            setattr(out, field_name, value.replace(tzinfo=UTC))
    if out.started_at and out.ended_at:
        out.duration_seconds = max((out.ended_at - out.started_at).total_seconds(), 0.0)
    return out


async def trigger_job(ctx: Ctx, body: JobCreate) -> BackupJob:
    asset = await ctx.get(ProtectedAsset, body.asset_id, "Asset")
    target = await ctx.get(BackupTarget, asset.backup_target_id)
    policy = await ctx.get_optional(BackupPolicy, body.policy_id or asset.policy_id)
    backup_type = body.backup_type or (policy.backup_type if policy else BackupType.FULL)
    async with ctx.audited(
        "backup.trigger",
        "backup_job",
        backup_target_id=target.id,
        resource_name=asset.name,
        details={
            "asset_id": str(asset.id),
            "policy_id": str(policy.id) if policy else None,
            "backup_type": backup_type.value,
        },
    ) as audit:
        if policy is not None and not policy.enabled:
            raise HTTPException(409, f"Policy '{policy.name}' is disabled.")
        if asset.array_asset_id is None:
            raise HTTPException(409, "The asset is not registered on the appliance.")
        running = await ctx.session.scalar(
            select(func.count())
            .select_from(BackupJob)
            .where(BackupJob.asset_id == asset.id, BackupJob.status.in_(ACTIVE))
        )
        if running:
            raise HTTPException(409, f"Asset '{asset.name}' already has a backup in progress.")
        async with ctx.driver(target) as drv:
            task_id = await drv.trigger_backup(asset.array_asset_id, backup_type=backup_type.value)
            job = BackupJob(
                task_id=task_id,
                backup_target_id=target.id,
                asset_id=asset.id,
                asset_name=asset.name,
                policy_id=policy.id if policy else None,
                backup_type=backup_type,
                status=JobStatus.PENDING,
                log_messages=[],
            )
            try:
                apply_task(
                    job, await drv.get_task(task_id)
                )  # first look: normally PENDING, with its first log line
            except StorageDriverError:
                log.warning(
                    "Could not read task %s right after starting it; the worker will catch up.", task_id
                )
            ctx.session.add(job)
            try:
                await ctx.session.commit()
            except Exception:
                await ctx.session.rollback()
                await undo(drv.cancel_task, task_id)
                raise
        audit.resource_id = str(job.id)
        audit.details["task_id"] = task_id
    return job


async def refresh_job(ctx: Ctx, job: BackupJob) -> tuple[BackupJob, int | None]:
    """Ask the appliance about one job now. Returns the job and the progress percentage."""
    if job.status.is_final:
        return job, None
    target = await ctx.get(BackupTarget, job.backup_target_id)
    progress: int | None = None
    async with ctx.driver(target) as drv:
        try:
            info = await drv.get_task(job.task_id)
        except ResourceNotFoundError:
            mark_lost(job, datetime.now(UTC))
        else:
            progress = info.progress_percent
            apply_task(job, info)
    await ctx.session.commit()
    await ctx.session.refresh(job)  # updated_at is set by the database, so it must be read again
    return job, progress


async def cancel_job(ctx: Ctx, job: BackupJob) -> tuple[BackupJob, bool]:
    """Cancel on the appliance. Returns (job, cancelled); False means it had already finished."""
    target = await ctx.get(BackupTarget, job.backup_target_id)
    cancelled = True
    async with ctx.audited(
        "backup.cancel",
        "backup_job",
        backup_target_id=target.id,
        resource_id=str(job.id),
        resource_name=job.asset_name,
        details={"task_id": job.task_id},
    ) as audit:
        if job.status.is_final:
            raise HTTPException(409, f"The job already finished ({job.status.value}).")
        async with ctx.driver(target) as drv:
            try:
                apply_task(job, await drv.cancel_task(job.task_id))
            except ResourceBusyError:
                # it ended just before the request arrived: record how it ended instead of losing that
                apply_task(job, await drv.get_task(job.task_id))
                cancelled = False
                audit.outcome = AuditOutcome.FAILURE
                audit.details["error"] = f"already finished ({job.status.value})"
            except ResourceNotFoundError:
                mark_lost(job, datetime.now(UTC))
                cancelled = False
                audit.outcome = AuditOutcome.FAILURE
                audit.details["error"] = "task unknown to the appliance"
    await ctx.session.refresh(job)
    return job, cancelled


# ---- background tracking ---------------------------------------------------------------------
@dataclass
class SyncResult:
    checked: int = 0
    updated: int = 0
    finished: int = 0
    lost: int = 0
    unreachable: list[str] = field(default_factory=list)


async def sync_active_jobs(
    maker: async_sessionmaker[AsyncSession],
    factory: Callable[[BackupTarget, SecretCipher], BackupDriverBase],
    cipher: SecretCipher,
) -> SyncResult:
    """Update every PENDING/RUNNING job from its appliance; an unreachable one affects only its own jobs."""
    result = SyncResult()
    async with maker() as session:
        rows = (await session.scalars(select(BackupJob).where(BackupJob.status.in_(ACTIVE)))).all()
        by_target: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        for job in rows:
            by_target[job.backup_target_id].append(job.id)
    for target_id, job_ids in by_target.items():
        async with maker() as session:
            target = await session.get(BackupTarget, target_id)
            if target is None:
                continue
            target_name = target.name  # read now: a rollback expires the object
            try:
                async with factory(target, cipher) as drv:
                    for job_id in job_ids:
                        job = await session.get(BackupJob, job_id)
                        if job is None or job.status.is_final:
                            continue
                        result.checked += 1
                        try:
                            info = await drv.get_task(job.task_id)
                        except ResourceNotFoundError:
                            mark_lost(job, datetime.now(UTC))
                            result.lost += 1
                            result.updated += 1
                            continue
                        if apply_task(job, info):
                            result.updated += 1
                            result.finished += job.status.is_final
                await session.commit()
            except StorageDriverError as exc:
                await session.rollback()
                result.unreachable.append(target_name)
                log.warning("Could not check backup jobs on %s: %s", target_name, exc)
    return result


# ---- reporting ---------------------------------------------------------------------------------
async def summarize(
    session: AsyncSession, window: str, now: datetime, target_id: uuid.UUID | None = None
) -> JobSummary:
    since = now - WINDOWS[window]
    stmt = select(BackupJob.status, BackupJob.data_transferred_gb, BackupJob.throughput_mb_s).where(
        BackupJob.created_at
        >= since.replace(tzinfo=None)  # created_at is stored as naive UTC by the database
    )
    if target_id:
        stmt = stmt.where(BackupJob.backup_target_id == target_id)
    by_status = {s.value: 0 for s in JobStatus}
    transferred, speeds = 0.0, []
    for status, gb, mbps in (await session.execute(stmt)).all():
        by_status[status.value] += 1
        transferred += gb
        if mbps > 0:
            speeds.append(mbps)
    ok, partial, failed = (by_status[k] for k in ("SUCCESS", "PARTIALLY_SUCCESSFUL", "FAILED"))
    finished = ok + partial + failed
    return JobSummary(
        window=window,  # type: ignore[arg-type]
        total=sum(by_status.values()),
        by_status=by_status,
        active=by_status["PENDING"] + by_status["RUNNING"],
        success_rate_percent=round(ok / finished * 100, 1) if finished else None,
        total_transferred_gb=round(transferred, 2),
        avg_throughput_mb_s=round(sum(speeds) / len(speeds), 1) if speeds else None,
    )
