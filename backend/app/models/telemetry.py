import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKey, str_enum
from .enums import HardwareHealth


def _target_fk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("backup_targets.id", ondelete="CASCADE"))


class CapacityMetric(UUIDPrimaryKey, Base):
    """One pool reading. Sizes in GB; ``dedup_ratio`` is a 0-1 fraction, the others are N:1."""

    __tablename__ = "capacity_metrics"
    __table_args__ = (Index("ix_capacity_target_time", "backup_target_id", "timestamp"),)

    backup_target_id: Mapped[uuid.UUID] = _target_fk()
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_capacity_gb: Mapped[float] = mapped_column(Float)
    used_physical_gb: Mapped[float] = mapped_column(Float)
    logic_written_gb: Mapped[float] = mapped_column(Float)
    post_dedup_gb: Mapped[float] = mapped_column(Float)
    dedup_ratio: Mapped[float] = mapped_column(Float)  # (ingested - post_dedup) / ingested
    compression_ratio: Mapped[float] = mapped_column(Float)  # post_dedup : physical
    reduction_ratio: Mapped[float] = mapped_column(Float)  # ingested : physical, e.g. 25.0 means 25:1


class ThroughputSample(UUIDPrimaryKey, Base):
    __tablename__ = "throughput_samples"
    __table_args__ = (Index("ix_throughput_target_time", "backup_target_id", "timestamp"),)

    backup_target_id: Mapped[uuid.UUID] = _target_fk()
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    write_throughput_mb_s: Mapped[float] = mapped_column(Float)
    read_throughput_mb_s: Mapped[float] = mapped_column(Float)
    iops: Mapped[int] = mapped_column(Integer, default=0)
    active_streams: Mapped[int] = mapped_column(Integer, default=0)


class HardwareSnapshot(UUIDPrimaryKey, Base):
    __tablename__ = "hardware_snapshots"
    __table_args__ = (Index("ix_hardware_target_time", "backup_target_id", "timestamp"),)

    backup_target_id: Mapped[uuid.UUID] = _target_fk()
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    overall_status: Mapped[HardwareHealth] = mapped_column(str_enum(HardwareHealth, "hardware_health"))
    max_cpu_percent: Mapped[float] = mapped_column(Float, default=0.0)
    max_memory_percent: Mapped[float] = mapped_column(Float, default=0.0)
    # {"controllers": [...], "nvram": [...], "power_modules": [...], "disks": [...]}
    components: Mapped[dict[str, Any]] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(String(255), default=None)
