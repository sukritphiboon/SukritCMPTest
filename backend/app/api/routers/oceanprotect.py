"""Monitoring and analytics for OceanProtect appliances. Without ``target_id`` every target is combined."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CtxDep, NowDep, PageDep
from app.core.config import get_settings
from app.models import AlarmRecord, AlarmSeverity, BackupTarget
from app.schemas.telemetry import (
    AckBody,
    AlarmOut,
    AlarmPage,
    Overview,
    ReductionStats,
    ThroughputHistory,
)
from app.services.context import Ctx
from app.services.telemetry import queries

router = APIRouter(prefix="/oceanprotect", tags=["oceanprotect"])
TargetId = Annotated[uuid.UUID | None, Query(description="Limit the answer to one appliance")]


async def _targets(ctx: Ctx, target_id: uuid.UUID | None) -> list[BackupTarget]:
    if target_id is not None:
        return [await ctx.get(BackupTarget, target_id, "Backup target")]
    return list((await ctx.session.scalars(select(BackupTarget).order_by(BackupTarget.name))).all())


@router.get("/overview", response_model=Overview)
async def overview(ctx: CtxDep, now: NowDep, target_id: TargetId = None):
    settings = get_settings()
    return await queries.build_overview(
        ctx.session,
        await _targets(ctx, target_id),
        now,
        interval_seconds=settings.telemetry_interval_seconds,
        stale_intervals=settings.telemetry_stale_intervals,
        window_days=settings.runway_window_days,
    )


@router.get("/throughput/history", response_model=ThroughputHistory)
async def throughput_history(
    ctx: CtxDep, now: NowDep, window: Literal["1h", "24h", "7d"] = "1h", target_id: TargetId = None
):
    targets = await _targets(ctx, target_id)
    return await queries.throughput_history(ctx.session, [t.id for t in targets], window, now)


@router.get("/reduction-stats", response_model=ReductionStats)
async def reduction_stats(
    ctx: CtxDep, now: NowDep, days: Annotated[int, Query(ge=1, le=400)] = 30, target_id: TargetId = None
):
    targets = await _targets(ctx, target_id)
    return await queries.reduction_stats(ctx.session, [t.id for t in targets], days, now)


def _alarm_out(record: AlarmRecord, names: dict[uuid.UUID, str]) -> AlarmOut:
    out = AlarmOut.model_validate(record)
    out.target_name = names.get(record.backup_target_id)
    out.active = record.cleared_at is None
    for field in ("raised_at", "first_seen_at", "last_seen_at", "cleared_at", "acknowledged_at"):
        value = getattr(out, field)
        if value is not None:
            setattr(out, field, queries.utc(value))
    return out


@router.get("/alarms", response_model=AlarmPage)
async def list_alarms(
    ctx: CtxDep,
    page: PageDep,
    target_id: TargetId = None,
    severity: AlarmSeverity | None = None,
    acknowledged: bool | None = None,
    state: Literal["active", "cleared", "all"] = "active",
):
    targets = await _targets(ctx, target_id)
    names = {t.id: t.name for t in targets}
    filters = [AlarmRecord.backup_target_id.in_(list(names))]
    if state == "active":
        filters.append(AlarmRecord.cleared_at.is_(None))
    elif state == "cleared":
        filters.append(AlarmRecord.cleared_at.is_not(None))
    if severity is not None:
        filters.append(AlarmRecord.severity == severity)
    if acknowledged is not None:
        filters.append(AlarmRecord.acknowledged == acknowledged)
    total = await ctx.session.scalar(select(func.count()).select_from(AlarmRecord).where(*filters))
    # severity counts describe the whole filtered set, not just the current page
    counts_rows = (
        await ctx.session.execute(
            select(AlarmRecord.severity, AlarmRecord.acknowledged, func.count())
            .where(*filters)
            .group_by(AlarmRecord.severity, AlarmRecord.acknowledged)
        )
    ).all()
    counts = queries.AlarmCounts()
    for sev, ack, n in counts_rows:
        setattr(counts, sev.value, getattr(counts, sev.value) + n)
        counts.total += n
        counts.unacknowledged += 0 if ack else n
    stmt = (
        select(AlarmRecord)
        .where(*filters)
        .order_by(queries.severity_order(), AlarmRecord.raised_at.desc(), AlarmRecord.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    items = [_alarm_out(a, names) for a in (await ctx.session.scalars(stmt)).all()]
    return AlarmPage(total=total or 0, counts=counts, items=items)


@router.post("/alarms/{alarm_id}/acknowledge", response_model=AlarmOut)
async def acknowledge_alarm(alarm_id: uuid.UUID, ctx: CtxDep, now: NowDep, body: AckBody | None = None):
    """Mark an alarm as seen. Stored in the CMP only; nothing is sent to the appliance.

    Acknowledging again is harmless and keeps the first acknowledgement.
    """
    alarm = await ctx.get(AlarmRecord, alarm_id, "Alarm")
    target = await ctx.session.get(BackupTarget, alarm.backup_target_id)
    names = {alarm.backup_target_id: target.name if target else ""}
    if alarm.acknowledged:
        return _alarm_out(alarm, names)
    async with ctx.audited(
        "alarm.acknowledge",
        "alarm",
        backup_target_id=alarm.backup_target_id,
        resource_id=str(alarm.id),
        resource_name=alarm.name,
        details={
            "severity": alarm.severity.value,
            "sequence": alarm.sequence,
            "note": body.note if body else None,
        },
    ):
        alarm.acknowledged, alarm.acknowledged_by, alarm.acknowledged_at = True, ctx.actor, now
        alarm.ack_note = body.note if body else None
    await ctx.session.refresh(alarm)
    return _alarm_out(alarm, names)
