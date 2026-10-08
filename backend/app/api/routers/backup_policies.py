import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.api.deps import CtxDep, MakerDep, NowDep, PageDep
from app.core.config import get_settings
from app.core.cron import CronError, next_runs
from app.models import BackupPolicy, ProtectedAsset, WormMode
from app.schemas.orchestration import (
    PolicyCreate,
    PolicyOut,
    PolicyPatch,
    PolicyRunOut,
    PolicySchedule,
)
from app.services.backup.scheduler import get_timezone, run_policy

router = APIRouter(prefix="/backup-policies", tags=["backup policies"])


def to_out(policy: BackupPolicy, now) -> PolicyOut:
    """The policy plus its next run time (the schedule is read in the system timezone)."""
    out = PolicyOut.model_validate(policy)
    tz_name = get_settings().scheduler_timezone
    out.schedule_timezone = tz_name
    if policy.enabled:
        try:
            runs = next_runs(policy.cron_schedule, get_timezone(tz_name), now, 1)
            out.next_run_at = runs[0] if runs else None
        except (CronError, ValueError):
            out.next_run_at = (
                None  # an unreadable stored schedule: shown as "no next run" instead of an error
            )
    return out


@router.post("", response_model=PolicyOut, status_code=201)
async def create_policy(body: PolicyCreate, ctx: CtxDep, now: NowDep):
    policy = BackupPolicy(id=uuid.uuid4(), **body.model_dump())
    async with ctx.audited(
        "policy.create",
        "policy",
        resource_id=str(policy.id),
        resource_name=body.name,
        details={
            "backup_type": body.backup_type.value,
            "retention_days": body.retention_days,
            "worm_mode": body.worm_mode.value,
            "cron_schedule": body.cron_schedule,
        },
    ):
        ctx.session.add(policy)
    await ctx.session.refresh(policy)
    return to_out(policy, now)


@router.get("", response_model=list[PolicyOut])
async def list_policies(ctx: CtxDep, page: PageDep, now: NowDep, enabled: bool | None = None):
    stmt = select(BackupPolicy).order_by(BackupPolicy.name)
    if enabled is not None:
        stmt = stmt.where(BackupPolicy.enabled == enabled)
    return [
        to_out(p, now) for p in (await ctx.session.scalars(stmt.limit(page.limit).offset(page.offset))).all()
    ]


@router.get("/{policy_id}", response_model=PolicyOut)
async def get_policy(policy_id: uuid.UUID, ctx: CtxDep, now: NowDep):
    return to_out(await ctx.get(BackupPolicy, policy_id, "Policy"), now)


@router.get("/{policy_id}/schedule", response_model=PolicySchedule)
async def policy_schedule(
    policy_id: uuid.UUID, ctx: CtxDep, now: NowDep, count: Annotated[int, Query(ge=1, le=50)] = 5
):
    """The next run times of the policy, in the system timezone (``CMP_SCHEDULER_TIMEZONE``)."""
    policy = await ctx.get(BackupPolicy, policy_id, "Policy")
    tz_name = get_settings().scheduler_timezone
    runs = []
    if policy.enabled:
        try:
            runs = next_runs(policy.cron_schedule, get_timezone(tz_name), now, count)
        except CronError as exc:
            raise HTTPException(409, f"The stored schedule cannot be used: {exc}") from exc
    return PolicySchedule(
        cron_schedule=policy.cron_schedule, timezone=tz_name, enabled=policy.enabled, runs=runs
    )


@router.post("/{policy_id}/run", response_model=PolicyRunOut)
async def run_policy_now(policy_id: uuid.UUID, ctx: CtxDep, maker: MakerDep, now: NowDep):
    """Start a backup for every asset of the policy right now (the same code the schedule uses)."""
    policy = await ctx.get(BackupPolicy, policy_id, "Policy")
    if not policy.enabled:
        raise HTTPException(409, f"Policy '{policy.name}' is disabled.")
    result = await run_policy(
        maker,
        ctx.factory,
        policy_id,
        now,
        actor=ctx.actor,
        action="policy.run",
        source_ip=ctx.source_ip,
        parallel=get_settings().scheduler_max_parallel_starts,
    )
    if result.status == "not_found":
        raise HTTPException(404, "Policy not found.")
    return result.to_out()


@router.patch("/{policy_id}", response_model=PolicyOut)
async def update_policy(policy_id: uuid.UUID, body: PolicyPatch, ctx: CtxDep, now: NowDep):
    policy = await ctx.get(BackupPolicy, policy_id, "Policy")
    changes = body.model_dump(exclude_unset=True)
    async with ctx.audited(
        "policy.update",
        "policy",
        resource_id=str(policy.id),
        resource_name=policy.name,
        details={"changes": {k: getattr(v, "value", v) for k, v in changes.items()}},
    ):
        worm_enabled = changes.get("worm_enabled", policy.worm_enabled)
        worm_mode = changes.get("worm_mode", policy.worm_mode)
        if worm_enabled and worm_mode == WormMode.NONE:
            raise HTTPException(
                422, "worm_mode (enterprise or compliance) is required when worm_enabled is true"
            )
        if not worm_enabled:
            changes["worm_mode"] = WormMode.NONE
        for key, value in changes.items():
            setattr(policy, key, value)
    await ctx.session.refresh(policy)
    return to_out(policy, now)


@router.delete("/{policy_id}", status_code=204)
async def delete_policy(policy_id: uuid.UUID, ctx: CtxDep):
    policy = await ctx.get(BackupPolicy, policy_id, "Policy")
    async with ctx.audited("policy.delete", "policy", resource_id=str(policy.id), resource_name=policy.name):
        in_use = await ctx.session.scalar(
            select(func.count()).select_from(ProtectedAsset).where(ProtectedAsset.policy_id == policy.id)
        )
        if in_use:
            raise HTTPException(409, f"{in_use} asset(s) still use this policy; reassign them first.")
        await ctx.session.delete(policy)
