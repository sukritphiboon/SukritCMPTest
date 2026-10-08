import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CtxDep, PageDep
from app.drivers import StorageDriverError
from app.models import (
    AuditOutcome,
    FileSystem,
    HealthStatus,
    ObjectBucket,
    StorageDevice,
    StorageVolume,
)
from app.schemas.api import DeviceCreate, DeviceOut, DevicePatch, DeviceTestResult
from app.schemas.storage import Alarm, CapacityMetrics, PerformanceMetrics, ReductionRatio

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("", response_model=DeviceOut, status_code=201)
async def register_device(body: DeviceCreate, ctx: CtxDep):
    device = StorageDevice(
        id=uuid.uuid4(),
        name=body.name,
        ip_address=body.ip_address,
        management_port=body.management_port,
        model=body.model,
    )
    device.set_credentials(ctx.cipher, body.username, body.password.get_secret_value())
    async with ctx.audited(
        "device.create",
        "device",
        resource_id=str(device.id),
        resource_name=body.name,
        details={"ip_address": body.ip_address, "model": body.model.value},
    ):
        ctx.session.add(device)
    await ctx.session.refresh(device)
    return device


@router.get("", response_model=list[DeviceOut])
async def list_devices(ctx: CtxDep, page: PageDep):
    stmt = select(StorageDevice).order_by(StorageDevice.name).limit(page.limit).offset(page.offset)
    return (await ctx.session.scalars(stmt)).all()


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(device_id: uuid.UUID, ctx: CtxDep):
    return await ctx.get(StorageDevice, device_id, "Storage device")


@router.patch("/{device_id}", response_model=DeviceOut)
async def update_device(device_id: uuid.UUID, body: DevicePatch, ctx: CtxDep):
    device = await ctx.get(StorageDevice, device_id, "Storage device")
    changes = body.model_dump(exclude_unset=True, exclude={"username", "password"})
    async with ctx.audited(
        "device.update",
        "device",
        resource_id=str(device.id),
        resource_name=device.name,
        details={
            "fields": sorted([*changes, *(k for k in ("username", "password") if k in body.model_fields_set)])
        },
    ):
        for key, value in changes.items():
            setattr(device, key, value)
        if body.username is not None or body.password is not None:
            username, password = device.get_credentials(ctx.cipher)
            device.set_credentials(
                ctx.cipher,
                body.username or username,
                body.password.get_secret_value() if body.password else password,
            )
    await ctx.session.refresh(device)
    return device


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: uuid.UUID, ctx: CtxDep):
    device = await ctx.get(StorageDevice, device_id, "Storage device")
    async with ctx.audited(
        "device.delete",
        "device",
        resource_id=str(device.id),
        resource_name=device.name,
    ):
        for model in (StorageVolume, FileSystem, ObjectBucket):
            count = await ctx.session.scalar(
                select(func.count()).select_from(model).where(model.storage_device_id == device.id)
            )
            if count:
                raise HTTPException(409, "The device still has volumes, file systems or buckets.")
        await ctx.session.delete(device)


@router.post("/{device_id}/test", response_model=DeviceTestResult)
async def test_device(device_id: uuid.UUID, ctx: CtxDep):
    """Log in to the array, read its alarms and update the stored health status."""
    device = await ctx.get(StorageDevice, device_id, "Storage device")
    result = DeviceTestResult(reachable=False, health_status=HealthStatus.FAULT)
    async with ctx.audited(
        "device.test",
        "device",
        storage_device_id=device.id,
        resource_id=str(device.id),
        resource_name=device.name,
    ) as audit:
        try:
            async with ctx.driver(device) as drv:
                alarms = await drv.get_active_alarms()
                array_id = getattr(getattr(drv, "client", None), "device_id", None)
        except StorageDriverError as exc:
            device.health_status = HealthStatus.FAULT
            result.error = str(exc)
            audit.outcome = AuditOutcome.FAILURE
            audit.details["error"] = str(exc)
        else:
            severe = any(a.severity in ("major", "critical") for a in alarms)
            device.health_status = HealthStatus.DEGRADED if severe else HealthStatus.HEALTHY
            device.last_seen_at = func.now()  # type: ignore[assignment]
            if array_id:
                device.device_id = array_id
            result = DeviceTestResult(
                reachable=True,
                device_id=array_id,
                health_status=device.health_status,
                active_alarms=len(alarms),
            )
    return result


# ---- telemetry ----------------------------------------------------------------------------------
@router.get("/{device_id}/capacity", response_model=CapacityMetrics, tags=["telemetry"])
async def capacity(device_id: uuid.UUID, ctx: CtxDep):
    device = await ctx.get(StorageDevice, device_id, "Storage device")
    async with ctx.driver(device) as drv:
        return await drv.get_capacity_metrics()


@router.get("/{device_id}/reduction-ratio", response_model=ReductionRatio, tags=["telemetry"])
async def reduction_ratio(device_id: uuid.UUID, ctx: CtxDep):
    device = await ctx.get(StorageDevice, device_id, "Storage device")
    async with ctx.driver(device) as drv:
        return await drv.get_reduction_ratio()


@router.get("/{device_id}/alarms", response_model=list[Alarm], tags=["telemetry"])
async def alarms(device_id: uuid.UUID, ctx: CtxDep):
    device = await ctx.get(StorageDevice, device_id, "Storage device")
    async with ctx.driver(device) as drv:
        return await drv.get_active_alarms()


@router.get("/{device_id}/performance", response_model=PerformanceMetrics, tags=["telemetry"])
async def performance(device_id: uuid.UUID, ctx: CtxDep):
    device = await ctx.get(StorageDevice, device_id, "Storage device")
    async with ctx.driver(device) as drv:
        return await drv.get_performance_metrics()
