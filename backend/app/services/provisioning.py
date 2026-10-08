"""Create / change / delete storage resources: array first, then the CMP database, always audited.

If the database write fails after the array was changed, the new array resource is removed again
(best effort). Deleting a resource that is already gone on the array only cleans the database.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.drivers.errors import ResourceNotFoundError
from app.models import (
    DeviceModel,
    FileSystem,
    MappingStatus,
    ObjectBucket,
    ProtectionPolicy,
    Provisioning,
    QuotaStatus,
    StorageDevice,
    StorageVolume,
    Tenant,
    WormMode,
)
from app.schemas.api import (
    BucketCreate,
    BucketQuota,
    CredentialsCreate,
    FileSystemCreate,
    QuotaSet,
    SnapshotCreate,
    VolumeCreate,
    VolumeExpand,
    VolumeMap,
)

from .context import Ctx, undo
from .quota import ensure_within_quota


# ---- block ------------------------------------------------------------------------------------
async def create_volume(ctx: Ctx, body: VolumeCreate) -> StorageVolume:
    device = await ctx.get(StorageDevice, body.storage_device_id, "Storage device")
    tenant = await ctx.get(Tenant, body.tenant_id)
    await ctx.get_optional(ProtectionPolicy, body.protection_policy_id)
    async with ctx.audited(
        "volume.create",
        "volume",
        storage_device_id=device.id,
        tenant_id=tenant.id,
        resource_name=body.name,
        details={"size_gb": body.size_gb, "thin": body.thin},
    ) as audit:
        await ensure_within_quota(ctx.session, tenant, "block", body.size_gb)
        async with ctx.driver(device) as drv:
            lun = await drv.create_lun(body.name, body.size_gb, body.thin)
            volume = StorageVolume(
                name=body.name,
                wwn=lun.wwn,
                size_gb=body.size_gb,
                provisioning=Provisioning.THIN if body.thin else Provisioning.THICK,
                array_lun_id=lun.array_id,
                storage_device_id=device.id,
                tenant_id=tenant.id,
                protection_policy_id=body.protection_policy_id,
            )
            ctx.session.add(volume)
            try:
                await ctx.session.commit()
            except Exception:
                await ctx.session.rollback()
                await undo(drv.delete_lun, lun.array_id)
                raise
        audit.resource_id = str(volume.id)
        audit.details["array_lun_id"] = lun.array_id
    return volume


async def delete_volume(ctx: Ctx, volume: StorageVolume) -> None:
    device = await ctx.get(StorageDevice, volume.storage_device_id)
    async with ctx.audited(
        "volume.delete",
        "volume",
        storage_device_id=device.id,
        tenant_id=volume.tenant_id,
        resource_id=str(volume.id),
        resource_name=volume.name,
    ):
        if volume.array_lun_id:
            async with ctx.driver(device) as drv:
                try:
                    await drv.delete_lun(volume.array_lun_id)
                except ResourceNotFoundError:
                    pass
        await ctx.session.delete(volume)


async def expand_volume(ctx: Ctx, volume: StorageVolume, body: VolumeExpand) -> StorageVolume:
    device = await ctx.get(StorageDevice, volume.storage_device_id)
    tenant = await ctx.get(Tenant, volume.tenant_id)
    async with ctx.audited(
        "volume.expand",
        "volume",
        storage_device_id=device.id,
        tenant_id=tenant.id,
        resource_id=str(volume.id),
        resource_name=volume.name,
        details={"from_gb": volume.size_gb, "to_gb": body.new_size_gb},
    ):
        if body.new_size_gb <= volume.size_gb:
            raise HTTPException(422, "The new size must be larger than the current size.")
        await ensure_within_quota(ctx.session, tenant, "block", body.new_size_gb - volume.size_gb)
        async with ctx.driver(device) as drv:
            await drv.expand_lun(volume.array_lun_id or "", body.new_size_gb)
        volume.size_gb = body.new_size_gb
    return volume


async def map_volume(ctx: Ctx, volume: StorageVolume, body: VolumeMap) -> StorageVolume:
    device = await ctx.get(StorageDevice, volume.storage_device_id)
    async with ctx.audited(
        "volume.map",
        "volume",
        storage_device_id=device.id,
        tenant_id=volume.tenant_id,
        resource_id=str(volume.id),
        resource_name=volume.name,
        details={"host": body.host_name},
    ):
        if volume.mapping_status == MappingStatus.MAPPED:
            raise HTTPException(409, "The volume is already mapped.")
        async with ctx.driver(device) as drv:
            group = await drv.create_lun_group(
                f"lg-{volume.name}-{volume.id.hex[:8]}", [volume.array_lun_id or ""]
            )
            await drv.map_to_host(group.array_id, body.host_name)
        volume.mapping_status = MappingStatus.MAPPED
    return volume


async def snapshot_volume(ctx: Ctx, volume: StorageVolume, body: SnapshotCreate):
    device = await ctx.get(StorageDevice, volume.storage_device_id)
    async with ctx.audited(
        "volume.snapshot",
        "volume",
        storage_device_id=device.id,
        tenant_id=volume.tenant_id,
        resource_id=str(volume.id),
        resource_name=volume.name,
        details={"snapshot": body.name},
    ):
        async with ctx.driver(device) as drv:
            return await drv.create_snapshot(volume.array_lun_id or "", body.name, "lun")


# ---- file ----------------------------------------------------------------------------------------
async def create_filesystem(ctx: Ctx, body: FileSystemCreate) -> FileSystem:
    device = await ctx.get(StorageDevice, body.storage_device_id, "Storage device")
    tenant = await ctx.get(Tenant, body.tenant_id)
    policy = await ctx.get_optional(ProtectionPolicy, body.protection_policy_id)
    async with ctx.audited(
        "filesystem.create",
        "filesystem",
        storage_device_id=device.id,
        tenant_id=tenant.id,
        resource_name=body.name,
        details={"size_gb": body.size_gb, "protocol": body.protocol.value},
    ) as audit:
        if body.quota_soft_gb and body.quota_hard_gb and body.quota_soft_gb > body.quota_hard_gb:
            raise HTTPException(422, "The soft quota must not exceed the hard quota.")
        wants_worm = policy is not None and policy.worm_mode != WormMode.NONE
        if wants_worm and device.model != DeviceModel.OCEANPROTECT:
            raise HTTPException(422, "WORM protection policies need an OceanProtect device.")
        await ensure_within_quota(ctx.session, tenant, "file", body.size_gb)
        async with ctx.driver(device) as drv:
            fs = await drv.create_filesystem(body.name, body.size_gb)
            try:
                share = await drv.create_share(fs.array_id, body.protocol.value, body.share_name)  # type: ignore[arg-type]
                if body.quota_hard_gb:
                    await drv.set_quota(fs.array_id, body.quota_hard_gb, body.quota_soft_gb)
                if wants_worm and policy is not None:
                    await drv.create_worm_policy(  # type: ignore[attr-defined]
                        fs.array_id, f"worm-{body.name}", policy.retention_days, policy.worm_mode.value
                    )
                row = FileSystem(
                    name=body.name,
                    size_gb=body.size_gb,
                    protocol=body.protocol,
                    share_path=share.path,
                    quota_hard_gb=body.quota_hard_gb,
                    quota_soft_gb=(
                        body.quota_soft_gb or int(body.quota_hard_gb * 0.8) if body.quota_hard_gb else None
                    ),
                    quota_status=QuotaStatus.WITHIN_LIMIT if body.quota_hard_gb else QuotaStatus.NONE,
                    array_fs_id=fs.array_id,
                    storage_device_id=device.id,
                    tenant_id=tenant.id,
                    protection_policy_id=body.protection_policy_id,
                )
                ctx.session.add(row)
                await ctx.session.commit()
            except Exception:
                await ctx.session.rollback()
                await undo(drv.delete_filesystem, fs.array_id)
                raise
        audit.resource_id = str(row.id)
        audit.details["array_fs_id"] = fs.array_id
    return row


async def delete_filesystem(ctx: Ctx, fs: FileSystem) -> None:
    device = await ctx.get(StorageDevice, fs.storage_device_id)
    async with ctx.audited(
        "filesystem.delete",
        "filesystem",
        storage_device_id=device.id,
        tenant_id=fs.tenant_id,
        resource_id=str(fs.id),
        resource_name=fs.name,
    ):
        if fs.array_fs_id:
            async with ctx.driver(device) as drv:
                try:
                    await drv.delete_filesystem(fs.array_fs_id)
                except ResourceNotFoundError:
                    pass
        await ctx.session.delete(fs)


async def set_filesystem_quota(ctx: Ctx, fs: FileSystem, body: QuotaSet) -> FileSystem:
    device = await ctx.get(StorageDevice, fs.storage_device_id)
    async with ctx.audited(
        "filesystem.quota",
        "filesystem",
        storage_device_id=device.id,
        tenant_id=fs.tenant_id,
        resource_id=str(fs.id),
        resource_name=fs.name,
        details={"hard_gb": body.hard_gb, "soft_gb": body.soft_gb},
    ):
        if body.soft_gb and body.soft_gb > body.hard_gb:
            raise HTTPException(422, "The soft quota must not exceed the hard quota.")
        async with ctx.driver(device) as drv:
            info = await drv.set_quota(fs.array_fs_id or "", body.hard_gb, body.soft_gb)
        fs.quota_hard_gb = body.hard_gb
        fs.quota_soft_gb = int(info.soft_gb)
        fs.quota_status = QuotaStatus.WITHIN_LIMIT
    return fs


async def snapshot_filesystem(ctx: Ctx, fs: FileSystem, body: SnapshotCreate):
    device = await ctx.get(StorageDevice, fs.storage_device_id)
    async with ctx.audited(
        "filesystem.snapshot",
        "filesystem",
        storage_device_id=device.id,
        tenant_id=fs.tenant_id,
        resource_id=str(fs.id),
        resource_name=fs.name,
        details={"snapshot": body.name},
    ):
        async with ctx.driver(device) as drv:
            return await drv.create_snapshot(fs.array_fs_id or "", body.name, "filesystem")


# ---- object ---------------------------------------------------------------------------------------
async def create_bucket(ctx: Ctx, body: BucketCreate) -> ObjectBucket:
    device = await ctx.get(StorageDevice, body.storage_device_id, "Storage device")
    tenant = await ctx.get(Tenant, body.tenant_id)
    owner = body.owner or tenant.name
    async with ctx.audited(
        "bucket.create",
        "bucket",
        storage_device_id=device.id,
        tenant_id=tenant.id,
        resource_name=body.bucket_name,
        details={"quota_gb": body.quota_gb, "owner": owner},
    ) as audit:
        await ensure_within_quota(ctx.session, tenant, "object", body.quota_gb)
        async with ctx.driver(device) as drv:
            info = await drv.create_bucket(body.bucket_name, owner, body.quota_gb)
            row = ObjectBucket(
                bucket_name=body.bucket_name,
                owner=owner,
                quota_gb=body.quota_gb,
                s3_endpoint=info.endpoint,
                storage_device_id=device.id,
                tenant_id=tenant.id,
            )
            ctx.session.add(row)
            try:
                await ctx.session.commit()
            except Exception:
                await ctx.session.rollback()
                await undo(drv.delete_bucket, body.bucket_name)
                raise
        audit.resource_id = str(row.id)
    return row


async def set_bucket_quota(ctx: Ctx, bucket: ObjectBucket, body: BucketQuota) -> ObjectBucket:
    device = await ctx.get(StorageDevice, bucket.storage_device_id)
    tenant = await ctx.get(Tenant, bucket.tenant_id)
    async with ctx.audited(
        "bucket.quota",
        "bucket",
        storage_device_id=device.id,
        tenant_id=tenant.id,
        resource_id=str(bucket.id),
        resource_name=bucket.bucket_name,
        details={"from_gb": bucket.quota_gb, "to_gb": body.quota_gb},
    ):
        delta = body.quota_gb - (bucket.quota_gb or 0)
        if delta > 0:
            await ensure_within_quota(ctx.session, tenant, "object", delta)
        async with ctx.driver(device) as drv:
            await drv.set_bucket_quota(bucket.bucket_name, body.quota_gb)
        bucket.quota_gb = body.quota_gb
    return bucket


async def delete_bucket(ctx: Ctx, bucket: ObjectBucket) -> None:
    device = await ctx.get(StorageDevice, bucket.storage_device_id)
    async with ctx.audited(
        "bucket.delete",
        "bucket",
        storage_device_id=device.id,
        tenant_id=bucket.tenant_id,
        resource_id=str(bucket.id),
        resource_name=bucket.bucket_name,
    ):
        async with ctx.driver(device) as drv:
            try:
                await drv.delete_bucket(bucket.bucket_name)
            except ResourceNotFoundError:
                pass
        await ctx.session.delete(bucket)


async def issue_credentials(ctx: Ctx, body: CredentialsCreate):
    device = await ctx.get(StorageDevice, body.storage_device_id, "Storage device")
    tenant = await ctx.get(Tenant, body.tenant_id)
    owner = body.owner or tenant.name
    async with ctx.audited(
        "s3.credentials",
        "bucket",
        storage_device_id=device.id,
        tenant_id=tenant.id,
        resource_name=owner,
        details={"owner": owner},  # the secret key is never written to the audit log
    ):
        async with ctx.driver(device) as drv:
            return await drv.generate_s3_credentials(owner)
