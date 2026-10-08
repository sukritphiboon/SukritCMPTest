import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CtxDep, PageDep
from app.drivers import StorageDriverError
from app.models import AuditOutcome, BackupTarget, HealthStatus
from app.schemas.api import TargetCreate, TargetOut, TargetPatch, TargetTestResult

router = APIRouter(prefix="/targets", tags=["backup targets"])


@router.post("", response_model=TargetOut, status_code=201)
async def register_target(body: TargetCreate, ctx: CtxDep):
    target = BackupTarget(
        id=uuid.uuid4(),
        name=body.name,
        ip_address=body.ip_address,
        management_port=body.management_port,
        model=body.model,
    )
    target.set_credentials(ctx.cipher, body.username, body.password.get_secret_value())
    async with ctx.audited(
        "target.create",
        "target",
        resource_id=str(target.id),
        resource_name=body.name,
        details={"ip_address": body.ip_address, "model": body.model.value},
    ):
        ctx.session.add(target)
    await ctx.session.refresh(target)
    return target


@router.get("", response_model=list[TargetOut])
async def list_targets(ctx: CtxDep, page: PageDep):
    stmt = select(BackupTarget).order_by(BackupTarget.name).limit(page.limit).offset(page.offset)
    return (await ctx.session.scalars(stmt)).all()


@router.get("/{target_id}", response_model=TargetOut)
async def get_target(target_id: uuid.UUID, ctx: CtxDep):
    return await ctx.get(BackupTarget, target_id, "Backup target")


@router.patch("/{target_id}", response_model=TargetOut)
async def update_target(target_id: uuid.UUID, body: TargetPatch, ctx: CtxDep):
    target = await ctx.get(BackupTarget, target_id, "Backup target")
    changes = body.model_dump(exclude_unset=True, exclude={"username", "password"})
    secret_fields = [k for k in ("username", "password") if k in body.model_fields_set]
    async with ctx.audited(
        "target.update",
        "target",
        backup_target_id=target.id,
        resource_id=str(target.id),
        resource_name=target.name,
        details={"fields": sorted([*changes, *secret_fields])},
    ):
        for key, value in changes.items():
            setattr(target, key, value)
        if secret_fields:
            username, password = target.get_credentials(ctx.cipher)
            target.set_credentials(
                ctx.cipher,
                body.username or username,
                body.password.get_secret_value() if body.password else password,
            )
    await ctx.session.refresh(target)
    return target


@router.delete("/{target_id}", status_code=204)
async def delete_target(target_id: uuid.UUID, ctx: CtxDep):
    target = await ctx.get(BackupTarget, target_id, "Backup target")
    async with ctx.audited(
        "target.delete",
        "target",
        resource_id=str(target.id),
        resource_name=target.name,
    ):
        await ctx.session.delete(target)


@router.post("/{target_id}/test", response_model=TargetTestResult)
async def test_target(target_id: uuid.UUID, ctx: CtxDep):
    """Log in to the appliance and read its identity. Always answers 200; check ``reachable``."""
    target = await ctx.get(BackupTarget, target_id, "Backup target")
    result = TargetTestResult(reachable=False, health_status=HealthStatus.FAULT)
    async with ctx.audited(
        "target.test",
        "target",
        backup_target_id=target.id,
        resource_id=str(target.id),
        resource_name=target.name,
    ) as audit:
        try:
            async with ctx.driver(target) as drv:
                info = await drv.get_system_info()
        except StorageDriverError as exc:
            target.health_status, target.last_error = HealthStatus.FAULT, str(exc)[:500]
            result.error = str(exc)
            audit.outcome = AuditOutcome.FAILURE
            audit.details["error"] = str(exc)
        else:
            if target.health_status in (HealthStatus.UNKNOWN, HealthStatus.FAULT):
                target.health_status = HealthStatus.HEALTHY
            target.last_error, target.last_seen_at = None, datetime.now(UTC)
            target.serial_number = target.serial_number or info.serial_number
            target.device_id = target.device_id or info.device_id
            target.firmware_version = info.firmware_version
            result = TargetTestResult(
                reachable=True,
                health_status=target.health_status,
                serial_number=info.serial_number,
                firmware_version=info.firmware_version,
            )
    return result
