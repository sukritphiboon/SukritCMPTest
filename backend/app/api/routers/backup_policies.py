import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CtxDep, PageDep
from app.models import BackupPolicy, ProtectedAsset, WormMode
from app.schemas.orchestration import PolicyCreate, PolicyOut, PolicyPatch

router = APIRouter(prefix="/backup-policies", tags=["backup policies"])


@router.post("", response_model=PolicyOut, status_code=201)
async def create_policy(body: PolicyCreate, ctx: CtxDep):
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
    return policy


@router.get("", response_model=list[PolicyOut])
async def list_policies(ctx: CtxDep, page: PageDep, enabled: bool | None = None):
    stmt = select(BackupPolicy).order_by(BackupPolicy.name)
    if enabled is not None:
        stmt = stmt.where(BackupPolicy.enabled == enabled)
    return (await ctx.session.scalars(stmt.limit(page.limit).offset(page.offset))).all()


@router.get("/{policy_id}", response_model=PolicyOut)
async def get_policy(policy_id: uuid.UUID, ctx: CtxDep):
    return await ctx.get(BackupPolicy, policy_id, "Policy")


@router.patch("/{policy_id}", response_model=PolicyOut)
async def update_policy(policy_id: uuid.UUID, body: PolicyPatch, ctx: CtxDep):
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
    return policy


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
