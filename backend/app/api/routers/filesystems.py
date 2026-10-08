import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CtxDep, PageDep
from app.models import FileSystem
from app.schemas.api import FileSystemCreate, FileSystemOut, QuotaSet, SnapshotCreate
from app.schemas.storage import SnapshotInfo
from app.services import provisioning

router = APIRouter(prefix="/filesystems", tags=["file systems"])


@router.post("", response_model=FileSystemOut, status_code=201)
async def create_filesystem(body: FileSystemCreate, ctx: CtxDep):
    fs = await provisioning.create_filesystem(ctx, body)
    await ctx.session.refresh(fs)
    return fs


@router.get("", response_model=list[FileSystemOut])
async def list_filesystems(
    ctx: CtxDep, page: PageDep, tenant_id: uuid.UUID | None = None, storage_device_id: uuid.UUID | None = None
):
    stmt = select(FileSystem).order_by(FileSystem.created_at, FileSystem.name)
    if tenant_id:
        stmt = stmt.where(FileSystem.tenant_id == tenant_id)
    if storage_device_id:
        stmt = stmt.where(FileSystem.storage_device_id == storage_device_id)
    return (await ctx.session.scalars(stmt.limit(page.limit).offset(page.offset))).all()


@router.get("/{fs_id}", response_model=FileSystemOut)
async def get_filesystem(fs_id: uuid.UUID, ctx: CtxDep):
    return await ctx.get(FileSystem, fs_id, "File system")


@router.delete("/{fs_id}", status_code=204)
async def delete_filesystem(fs_id: uuid.UUID, ctx: CtxDep):
    await provisioning.delete_filesystem(ctx, await ctx.get(FileSystem, fs_id, "File system"))


@router.post("/{fs_id}/quota", response_model=FileSystemOut)
async def set_quota(fs_id: uuid.UUID, body: QuotaSet, ctx: CtxDep):
    fs = await provisioning.set_filesystem_quota(ctx, await ctx.get(FileSystem, fs_id, "File system"), body)
    await ctx.session.refresh(fs)
    return fs


@router.post("/{fs_id}/snapshots", response_model=SnapshotInfo, status_code=201)
async def snapshot_filesystem(fs_id: uuid.UUID, body: SnapshotCreate, ctx: CtxDep):
    return await provisioning.snapshot_filesystem(ctx, await ctx.get(FileSystem, fs_id, "File system"), body)
