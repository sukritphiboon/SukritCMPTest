import uuid
from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CtxDep, PageDep
from app.models import AuditLog, AuditOutcome
from app.schemas.api import AuditPage

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=AuditPage)
async def list_audit_logs(
    ctx: CtxDep,
    page: PageDep,
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    outcome: AuditOutcome | None = None,
    storage_device_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
):
    filters = []
    for column, value in (
        (AuditLog.actor, actor),
        (AuditLog.action, action),
        (AuditLog.resource_type, resource_type),
        (AuditLog.outcome, outcome),
        (AuditLog.storage_device_id, storage_device_id),
        (AuditLog.tenant_id, tenant_id),
    ):
        if value is not None:
            filters.append(column == value)
    if since:
        filters.append(AuditLog.occurred_at >= since)
    if until:
        filters.append(AuditLog.occurred_at <= until)
    total = await ctx.session.scalar(select(func.count()).select_from(AuditLog).where(*filters))
    stmt = (
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id)
        .limit(page.limit)
        .offset(page.offset)
    )
    return AuditPage(total=total or 0, items=(await ctx.session.scalars(stmt)).all())
