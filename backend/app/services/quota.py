"""Tenant quota accounting. Quota kinds: block (LUNs), file (file systems), object (bucket quotas)."""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FileSystem, ObjectBucket, StorageVolume, Tenant

Kind = Literal["block", "file", "object"]
_COLUMNS = {
    "block": StorageVolume.size_gb,
    "file": FileSystem.size_gb,
    "object": ObjectBucket.quota_gb,
}
_OWNER = {"block": StorageVolume.tenant_id, "file": FileSystem.tenant_id, "object": ObjectBucket.tenant_id}


class QuotaExceeded(HTTPException):
    def __init__(self, kind: str, allowed: int, used: int, requested: int):
        super().__init__(
            409,
            f"{kind} quota exceeded: allocated {allowed} GB, used {used} GB, requested {requested} GB.",
        )


async def used_gb(session: AsyncSession, tenant_id, kind: Kind) -> int:
    stmt = select(func.coalesce(func.sum(_COLUMNS[kind]), 0)).where(_OWNER[kind] == tenant_id)
    return int((await session.execute(stmt)).scalar_one())


async def usage(session: AsyncSession, tenant: Tenant) -> dict[str, dict[str, int]]:
    result = {}
    for kind in ("block", "file", "object"):
        allowed = getattr(tenant, f"quota_{kind}_gb")
        used = await used_gb(session, tenant.id, kind)  # type: ignore[arg-type]
        result[kind] = {"allocated_gb": allowed, "used_gb": used, "free_gb": max(allowed - used, 0)}
    return result


async def ensure_within_quota(session: AsyncSession, tenant: Tenant, kind: Kind, requested_gb: int) -> None:
    if not tenant.is_active:
        raise HTTPException(409, f"Tenant '{tenant.name}' is inactive.")
    allowed = getattr(tenant, f"quota_{kind}_gb")
    used = await used_gb(session, tenant.id, kind)
    if used + requested_gb > allowed:
        raise QuotaExceeded(kind, allowed, used, requested_gb)
