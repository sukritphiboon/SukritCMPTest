"""Audit trail helper.

``audited`` wraps one operation. On success the audit row is stored together with the changes the
operation made; on any error the transaction is rolled back and a *failure* row is stored instead.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, AuditOutcome


@dataclass
class AuditCtx:
    """Mutable bits the operation can fill in while it runs."""

    resource_id: str | None = None
    resource_name: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    outcome: AuditOutcome = AuditOutcome.SUCCESS


def _describe(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc) or type(exc).__name__


@asynccontextmanager
async def audited(
    session: AsyncSession,
    actor: str,
    action: str,
    resource_type: str,
    *,
    backup_target_id: uuid.UUID | None = None,
    resource_id: str | None = None,
    resource_name: str | None = None,
    details: dict[str, Any] | None = None,
    source_ip: str | None = None,
) -> AsyncIterator[AuditCtx]:
    ctx = AuditCtx(resource_id, resource_name, dict(details or {}))

    def make(outcome: AuditOutcome) -> AuditLog:
        return AuditLog(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=ctx.resource_id,
            resource_name=ctx.resource_name,
            backup_target_id=backup_target_id,
            outcome=outcome,
            details=ctx.details or None,
            source_ip=source_ip,
        )

    try:
        yield ctx
        session.add(make(ctx.outcome))
        await session.commit()
    except Exception as exc:
        await session.rollback()
        ctx.details["error"] = _describe(exc)
        session.add(make(AuditOutcome.FAILURE))
        await session.commit()
        raise
