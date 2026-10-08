import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CtxDep, PageDep
from app.models import ProtectionPolicy, Tenant
from app.schemas.api import PolicyCreate, PolicyOut, PolicyPatch

router = APIRouter(prefix="/protection-policies", tags=["protection policies"])


@router.post("", response_model=PolicyOut, status_code=201)
async def create_policy(body: PolicyCreate, ctx: CtxDep):
    await ctx.get_optional(Tenant, body.tenant_id)
    policy = ProtectionPolicy(id=uuid.uuid4(), **body.model_dump())
    async with ctx.audited(
        "policy.create",
        "policy",
        tenant_id=body.tenant_id,
        resource_id=str(policy.id),
        resource_name=body.name,
        details={"retention_days": body.retention_days, "worm_mode": body.worm_mode.value},
    ):
        ctx.session.add(policy)
    await ctx.session.refresh(policy)
    return policy


@router.get("", response_model=list[PolicyOut])
async def list_policies(ctx: CtxDep, page: PageDep, tenant_id: uuid.UUID | None = None):
    stmt = select(ProtectionPolicy).order_by(ProtectionPolicy.name)
    if tenant_id:
        stmt = stmt.where(ProtectionPolicy.tenant_id == tenant_id)
    return (await ctx.session.scalars(stmt.limit(page.limit).offset(page.offset))).all()


@router.get("/{policy_id}", response_model=PolicyOut)
async def get_policy(policy_id: uuid.UUID, ctx: CtxDep):
    return await ctx.get(ProtectionPolicy, policy_id, "Policy")


@router.patch("/{policy_id}", response_model=PolicyOut)
async def update_policy(policy_id: uuid.UUID, body: PolicyPatch, ctx: CtxDep):
    policy = await ctx.get(ProtectionPolicy, policy_id, "Policy")
    changes = body.model_dump(exclude_unset=True)
    async with ctx.audited(
        "policy.update",
        "policy",
        tenant_id=policy.tenant_id,
        resource_id=str(policy.id),
        resource_name=policy.name,
        details={"changes": {k: getattr(v, "value", v) for k, v in changes.items()}},
    ):
        for key, value in changes.items():
            setattr(policy, key, value)
    await ctx.session.refresh(policy)
    return policy


@router.delete("/{policy_id}", status_code=204)
async def delete_policy(policy_id: uuid.UUID, ctx: CtxDep):
    policy = await ctx.get(ProtectionPolicy, policy_id, "Policy")
    async with ctx.audited(
        "policy.delete",
        "policy",
        tenant_id=policy.tenant_id,
        resource_id=str(policy.id),
        resource_name=policy.name,
    ):
        await ctx.session.delete(policy)
