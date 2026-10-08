import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKey, str_enum
from .enums import AuditOutcome


class AuditLog(UUIDPrimaryKey, Base):
    """Append-only record of who did what to which resource. Rows survive resource deletion."""

    __tablename__ = "audit_logs"

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),  # microsecond precision keeps same-second events ordered
        server_default=func.now(),
        index=True,
    )
    actor: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)  # e.g. "lun.create"
    resource_type: Mapped[str] = mapped_column(String(32))  # lun | filesystem | bucket | device ...
    resource_id: Mapped[str | None] = mapped_column(String(64), default=None)
    resource_name: Mapped[str | None] = mapped_column(String(128), default=None)
    storage_device_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("storage_devices.id", ondelete="SET NULL"), index=True, default=None
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="SET NULL"), index=True, default=None
    )
    outcome: Mapped[AuditOutcome] = mapped_column(
        str_enum(AuditOutcome, "audit_outcome"), default=AuditOutcome.SUCCESS
    )
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    source_ip: Mapped[str | None] = mapped_column(String(45), default=None)
