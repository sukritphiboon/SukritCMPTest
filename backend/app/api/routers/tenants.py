import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CtxDep, PageDep
from app.models import FileSystem, ObjectBucket, StorageVolume, Tenant
from app.schemas.api import TenantCreate, TenantOut, TenantPatch, TenantUsage
from app.services.quota import usage

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("", response_model=TenantOut, status_code=201)
async def create_tenant(body: TenantCreate, ctx: CtxDep):
    tenant = Tenant(id=uuid.uuid4(), **body.model_dump())
    async with ctx.audited(
        "tenant.create",
        "tenant",
        tenant_id=None,
        resource_id=str(tenant.id),
        resource_name=body.name,
        details=body.model_dump(exclude={"name", "description"}),
    ):
        ctx.session.add(tenant)
    await ctx.session.refresh(tenant)
    return tenant


@router.get("", response_model=list[TenantOut])
async def list_tenants(ctx: CtxDep, page: PageDep):
    stmt = select(Tenant).order_by(Tenant.name).limit(page.limit).offset(page.offset)
    return (await ctx.session.scalars(stmt)).all()


@router.get("/{tenant_id}", response_model=TenantOut)
async def get_tenant(tenant_id: uuid.UUID, ctx: CtxDep):
    return await ctx.get(Tenant, tenant_id)


@router.get("/{tenant_id}/usage", response_model=TenantUsage)
async def tenant_usage(tenant_id: uuid.UUID, ctx: CtxDep):
    return await usage(ctx.session, await ctx.get(Tenant, tenant_id))


@router.patch("/{tenant_id}", response_model=TenantOut)
async def update_tenant(tenant_id: uuid.UUID, body: TenantPatch, ctx: CtxDep):
    tenant = await ctx.get(Tenant, tenant_id)
    changes = body.model_dump(exclude_unset=True)
    async with ctx.audited(
        "tenant.update",
        "tenant",
        tenant_id=tenant.id,
        resource_id=str(tenant.id),
        resource_name=tenant.name,
        details={"changes": changes},
    ):
        for key, value in changes.items():
            setattr(tenant, key, value)
    await ctx.session.refresh(tenant)
    return tenant


@router.delete("/{tenant_id}", status_code=204)
async def delete_tenant(tenant_id: uuid.UUID, ctx: CtxDep):
    tenant = await ctx.get(Tenant, tenant_id)
    async with ctx.audited("tenant.delete", "tenant", resource_id=str(tenant.id), resource_name=tenant.name):
        for model in (StorageVolume, FileSystem, ObjectBucket):
            count = await ctx.session.scalar(
                select(func.count()).select_from(model).where(model.tenant_id == tenant.id)
            )
            if count:
                raise HTTPException(409, "The tenant still owns volumes, file systems or buckets.")
        await ctx.session.delete(tenant)
