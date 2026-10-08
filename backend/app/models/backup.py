import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamps, UUIDPrimaryKey, str_enum
from .enums import AssetType, BackupType, JobStatus, WormMode


class BackupPolicy(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "backup_policies"
    __table_args__ = (
        CheckConstraint("retention_days > 0", name="retention_positive"),
        CheckConstraint("NOT worm_enabled OR worm_mode <> 'none'", name="worm_mode_set"),
    )

    name: Mapped[str] = mapped_column(String(128), unique=True)
    cron_schedule: Mapped[str] = mapped_column(String(64))  # e.g. "0 1 * * *"
    backup_type: Mapped[BackupType] = mapped_column(str_enum(BackupType, "backup_type"))
    retention_days: Mapped[int] = mapped_column(Integer)
    worm_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    worm_mode: Mapped[WormMode] = mapped_column(str_enum(WormMode, "worm_mode"), default=WormMode.NONE)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ProtectedAsset(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "protected_assets"
    __table_args__ = (UniqueConstraint("backup_target_id", "name", name="uq_asset_target_name"),)

    name: Mapped[str] = mapped_column(String(255))
    asset_type: Mapped[AssetType] = mapped_column(str_enum(AssetType, "asset_type"))
    source_ip: Mapped[str | None] = mapped_column(String(45), default=None)
    agent_version: Mapped[str | None] = mapped_column(String(32), default=None)
    backup_target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("backup_targets.id", ondelete="CASCADE"), index=True
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("backup_policies.id", ondelete="SET NULL"), default=None
    )


class BackupJob(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "backup_jobs"
    __table_args__ = (
        UniqueConstraint("backup_target_id", "task_id", name="uq_job_target_task"),
        CheckConstraint("data_transferred_gb >= 0", name="transferred_non_negative"),
    )

    task_id: Mapped[str] = mapped_column(String(64))  # task id on the appliance, polled via /task_list
    backup_target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("backup_targets.id", ondelete="CASCADE"), index=True
    )
    # History must outlive assets and policies, so these are SET NULL; the name stays readable.
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("protected_assets.id", ondelete="SET NULL"), index=True, default=None
    )
    asset_name: Mapped[str | None] = mapped_column(String(255), default=None)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("backup_policies.id", ondelete="SET NULL"), default=None
    )
    backup_type: Mapped[BackupType] = mapped_column(str_enum(BackupType, "backup_type"))
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "job_status"), default=JobStatus.PENDING, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    data_transferred_gb: Mapped[float] = mapped_column(Float, default=0.0)
    throughput_mb_s: Mapped[float] = mapped_column(Float, default=0.0)
    log_messages: Mapped[list[Any]] = mapped_column(JSON, default=list)
