import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKey, str_enum
from .enums import AlarmSeverity


class AlarmRecord(UUIDPrimaryKey, Base):
    """An appliance alarm as seen by the CMP. ``cleared_at`` is set once the appliance stops reporting it."""

    __tablename__ = "alarm_records"
    __table_args__ = (
        UniqueConstraint("backup_target_id", "sequence", "event_id", name="uq_alarm_identity"),
        Index("ix_alarm_active", "backup_target_id", "cleared_at"),
    )

    backup_target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("backup_targets.id", ondelete="CASCADE")
    )
    sequence: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    severity: Mapped[AlarmSeverity] = mapped_column(str_enum(AlarmSeverity, "alarm_severity"))
    location: Mapped[str | None] = mapped_column(String(255), default=None)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # time reported by the appliance
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Acknowledgement is stored in the CMP only; it is not sent to the appliance.
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by: Mapped[str | None] = mapped_column(String(128), default=None)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ack_note: Mapped[str | None] = mapped_column(Text, default=None)
