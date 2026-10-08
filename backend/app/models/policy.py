import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamps, UUIDPrimaryKey, str_enum
from .enums import WormMode


class ProtectionPolicy(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "protection_policies"
    __table_args__ = (CheckConstraint("retention_days > 0", name="retention_positive"),)

    name: Mapped[str] = mapped_column(String(128), unique=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), index=True, default=None
    )
    retention_days: Mapped[int] = mapped_column(Integer)
    worm_mode: Mapped[WormMode] = mapped_column(str_enum(WormMode, "worm_mode"), default=WormMode.NONE)
    # Cron expression, e.g. "0 */4 * * *" for a snapshot every four hours
    snapshot_schedule: Mapped[str | None] = mapped_column(String(64), default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
