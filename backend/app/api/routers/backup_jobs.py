import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CtxDep, NowDep, PageDep
from app.models import BackupJob, JobStatus
from app.schemas.orchestration import JobCreate, JobOut, JobPage, JobSummary
from app.services.backup import jobs

router = APIRouter(prefix="/backup-jobs", tags=["backup jobs"])


@router.post("", response_model=JobOut, status_code=202)
async def start_backup(body: JobCreate, ctx: CtxDep):
    """Start an ad-hoc backup. The backup runs on the appliance; follow it with ``GET /backup-jobs/{id}``."""
    job = await jobs.trigger_job(ctx, body)
    return jobs.job_out(job)


@router.get("", response_model=JobPage)
async def list_jobs(
    ctx: CtxDep,
    page: PageDep,
    status: Annotated[list[JobStatus] | None, Query()] = None,
    active: bool | None = None,
    backup_target_id: uuid.UUID | None = None,
    asset_id: uuid.UUID | None = None,
    policy_id: uuid.UUID | None = None,
):
    """Newest first. ``status`` may be repeated; ``active=true`` means PENDING or RUNNING."""
    filters = []
    if status:
        filters.append(BackupJob.status.in_(status))
    if active is not None:
        in_progress = BackupJob.status.in_(jobs.ACTIVE)
        filters.append(in_progress if active else ~in_progress)
    for column, value in (
        (BackupJob.backup_target_id, backup_target_id),
        (BackupJob.asset_id, asset_id),
        (BackupJob.policy_id, policy_id),
    ):
        if value is not None:
            filters.append(column == value)
    total = await ctx.session.scalar(select(func.count()).select_from(BackupJob).where(*filters))
    stmt = (
        select(BackupJob)
        .where(*filters)
        .order_by(BackupJob.created_at.desc(), BackupJob.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return JobPage(total=total or 0, items=[jobs.job_out(j) for j in (await ctx.session.scalars(stmt)).all()])


@router.get("/summary", response_model=JobSummary)
async def job_summary(
    ctx: CtxDep,
    now: NowDep,
    window: Literal["24h", "7d", "30d"] = "24h",
    backup_target_id: uuid.UUID | None = None,
):
    return await jobs.summarize(ctx.session, window, now, backup_target_id)


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: uuid.UUID,
    ctx: CtxDep,
    refresh: Annotated[
        bool, Query(description="Ask the appliance now instead of using the last stored state")
    ] = False,
):
    job = await ctx.get(BackupJob, job_id, "Job")
    progress = None
    if refresh and not job.status.is_final:
        job, progress = await jobs.refresh_job(ctx, job)
    return jobs.job_out(job, progress)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: uuid.UUID, ctx: CtxDep):
    job, cancelled = await jobs.cancel_job(ctx, await ctx.get(BackupJob, job_id, "Job"))
    if not cancelled:
        raise HTTPException(409, f"The job had already finished ({job.status.value}).")
    return jobs.job_out(job)
