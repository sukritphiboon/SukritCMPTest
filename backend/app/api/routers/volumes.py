import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CtxDep, PageDep
from app.models import StorageVolume
from app.schemas.api import SnapshotCreate, VolumeCreate, VolumeExpand, VolumeMap, VolumeOut
from app.schemas.storage import SnapshotInfo
from app.services import provisioning

router = APIRouter(prefix="/volumes", tags=["volumes (LUN)"])


@router.post("", response_model=VolumeOut, status_code=201)
async def create_volume(body: VolumeCreate, ctx: CtxDep):
    volume = await provisioning.create_volume(ctx, body)
    await ctx.session.refresh(volume)
    return volume


@router.get("", response_model=list[VolumeOut])
async def list_volumes(
    ctx: CtxDep, page: PageDep, tenant_id: uuid.UUID | None = None, storage_device_id: uuid.UUID | None = None
):
    stmt = select(StorageVolume).order_by(StorageVolume.created_at, StorageVolume.name)
    if tenant_id:
        stmt = stmt.where(StorageVolume.tenant_id == tenant_id)
    if storage_device_id:
        stmt = stmt.where(StorageVolume.storage_device_id == storage_device_id)
    return (await ctx.session.scalars(stmt.limit(page.limit).offset(page.offset))).all()


@router.get("/{volume_id}", response_model=VolumeOut)
async def get_volume(volume_id: uuid.UUID, ctx: CtxDep):
    return await ctx.get(StorageVolume, volume_id, "Volume")


@router.delete("/{volume_id}", status_code=204)
async def delete_volume(volume_id: uuid.UUID, ctx: CtxDep):
    await provisioning.delete_volume(ctx, await ctx.get(StorageVolume, volume_id, "Volume"))


@router.post("/{volume_id}/expand", response_model=VolumeOut)
async def expand_volume(volume_id: uuid.UUID, body: VolumeExpand, ctx: CtxDep):
    volume = await provisioning.expand_volume(ctx, await ctx.get(StorageVolume, volume_id, "Volume"), body)
    await ctx.session.refresh(volume)
    return volume


@router.post("/{volume_id}/map", response_model=VolumeOut)
async def map_volume(volume_id: uuid.UUID, body: VolumeMap, ctx: CtxDep):
    volume = await provisioning.map_volume(ctx, await ctx.get(StorageVolume, volume_id, "Volume"), body)
    await ctx.session.refresh(volume)
    return volume


@router.post("/{volume_id}/snapshots", response_model=SnapshotInfo, status_code=201)
async def snapshot_volume(volume_id: uuid.UUID, body: SnapshotCreate, ctx: CtxDep):
    return await provisioning.snapshot_volume(ctx, await ctx.get(StorageVolume, volume_id, "Volume"), body)
