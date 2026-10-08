import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CtxDep, PageDep
from app.drivers.errors import ResourceNotFoundError
from app.models import BackupJob, BackupPolicy, BackupTarget, JobStatus, ProtectedAsset
from app.schemas.orchestration import AssetCreate, AssetOut, AssetPatch
from app.services.context import undo

router = APIRouter(prefix="/assets", tags=["protected assets"])


@router.post("", response_model=AssetOut, status_code=201)
async def register_asset(body: AssetCreate, ctx: CtxDep):
    """Register an asset on the appliance and in the CMP."""
    target = await ctx.get(BackupTarget, body.backup_target_id, "Backup target")
    await ctx.get_optional(BackupPolicy, body.policy_id)
    async with ctx.audited(
        "asset.create",
        "asset",
        backup_target_id=target.id,
        resource_name=body.name,
        details={
            "asset_type": body.asset_type.value,
            "policy_id": str(body.policy_id) if body.policy_id else None,
        },
    ) as audit:
        async with ctx.driver(target) as drv:
            info = await drv.create_asset(
                body.name, body.asset_type.value, body.source_ip or "", body.agent_version or ""
            )
            asset = ProtectedAsset(
                name=body.name,
                asset_type=body.asset_type,
                source_ip=body.source_ip,
                agent_version=body.agent_version or info.agent_version or None,
                array_asset_id=info.array_id,
                backup_target_id=target.id,
                policy_id=body.policy_id,
            )
            ctx.session.add(asset)
            try:
                await ctx.session.commit()
            except Exception:
                await ctx.session.rollback()
                await undo(drv.delete_asset, info.array_id)
                raise
        audit.resource_id = str(asset.id)
    await ctx.session.refresh(asset)
    return asset


@router.get("", response_model=list[AssetOut])
async def list_assets(
    ctx: CtxDep, page: PageDep, backup_target_id: uuid.UUID | None = None, policy_id: uuid.UUID | None = None
):
    stmt = select(ProtectedAsset).order_by(ProtectedAsset.name)
    if backup_target_id:
        stmt = stmt.where(ProtectedAsset.backup_target_id == backup_target_id)
    if policy_id:
        stmt = stmt.where(ProtectedAsset.policy_id == policy_id)
    return (await ctx.session.scalars(stmt.limit(page.limit).offset(page.offset))).all()


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: uuid.UUID, ctx: CtxDep):
    return await ctx.get(ProtectedAsset, asset_id, "Asset")


@router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(asset_id: uuid.UUID, body: AssetPatch, ctx: CtxDep):
    asset = await ctx.get(ProtectedAsset, asset_id, "Asset")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("policy_id"):
        await ctx.get(BackupPolicy, changes["policy_id"])
    async with ctx.audited(
        "asset.update",
        "asset",
        backup_target_id=asset.backup_target_id,
        resource_id=str(asset.id),
        resource_name=asset.name,
        details={"changes": {k: str(v) if v else v for k, v in changes.items()}},
    ):
        for key, value in changes.items():
            setattr(asset, key, value)
    await ctx.session.refresh(asset)
    return asset


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(asset_id: uuid.UUID, ctx: CtxDep):
    asset = await ctx.get(ProtectedAsset, asset_id, "Asset")
    target = await ctx.get(BackupTarget, asset.backup_target_id)
    async with ctx.audited(
        "asset.delete",
        "asset",
        backup_target_id=target.id,
        resource_id=str(asset.id),
        resource_name=asset.name,
    ):
        running = await ctx.session.scalar(
            select(func.count())
            .select_from(BackupJob)
            .where(
                BackupJob.asset_id == asset.id, BackupJob.status.in_((JobStatus.PENDING, JobStatus.RUNNING))
            )
        )
        if running:
            raise HTTPException(409, "The asset has a backup in progress.")
        if asset.array_asset_id:
            async with ctx.driver(target) as drv:
                try:
                    await drv.delete_asset(asset.array_asset_id)
                except ResourceNotFoundError:
                    pass  # already gone on the appliance
        await ctx.session.delete(asset)
