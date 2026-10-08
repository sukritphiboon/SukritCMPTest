"""Response bodies of the /oceanprotect analytics API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AlarmSeverity, HardwareHealth, HealthStatus


class RunwayOut(BaseModel):
    status: Literal["ok", "insufficient_data", "not_growing", "full"]
    days_until_full: float | None = None
    avg_daily_growth_gb: float | None = None
    free_gb: float | None = None
    window_days: int = 0
    target_id: uuid.UUID | None = None  # for the overall figure: the appliance that fills first
    target_name: str | None = None


class IngestionOut(BaseModel):
    write_mb_s: float = 0.0
    read_mb_s: float = 0.0
    total_mb_s: float = 0.0
    write_gb_per_hour: float = 0.0
    iops: int = 0
    active_streams: int = 0


class ReductionOut(BaseModel):
    """Ratios are N:1 except ``dedup_ratio``, a 0-1 fraction ((ingested - post_dedup) / ingested)."""

    reduction_ratio: float | None = None
    dedup_ratio: float | None = None
    dedup_factor: float | None = None
    compression_ratio: float | None = None
    logical_written_gb: float = 0.0
    post_dedup_gb: float = 0.0
    used_physical_gb: float = 0.0
    raw_capacity_gb: float = 0.0
    free_gb: float = 0.0


class AlarmCounts(BaseModel):
    critical: int = 0
    major: int = 0
    warning: int = 0
    total: int = 0
    unacknowledged: int = 0


class TargetSummary(BaseModel):
    id: uuid.UUID
    name: str
    health_status: HealthStatus
    hardware_status: HardwareHealth | None = None
    hardware_summary: str | None = None
    last_seen_at: datetime | None = None
    last_error: str | None = None
    data_age_seconds: float | None = None
    stale: bool = True
    ingestion: IngestionOut | None = None
    reduction: ReductionOut | None = None
    runway: RunwayOut | None = None
    alarms: AlarmCounts = AlarmCounts()
    max_cpu_percent: float | None = None
    max_memory_percent: float | None = None


class Overview(BaseModel):
    as_of: datetime
    targets_total: int
    targets_reporting: int  # have fresh data; only these count towards ingestion
    ingestion: IngestionOut
    reduction: ReductionOut
    runway: RunwayOut
    alarms: AlarmCounts
    hardware_status: HardwareHealth | None
    targets: list[TargetSummary]


class ThroughputPoint(BaseModel):
    timestamp: datetime  # start of the bucket (UTC)
    write_mb_s_avg: float
    read_mb_s_avg: float
    total_mb_s_avg: float
    write_mb_s_peak: float  # with several appliances: the sum of each one's own peak in the bucket
    read_mb_s_peak: float
    samples: int


class ThroughputHistory(BaseModel):
    window: Literal["1h", "24h", "7d"]
    bucket_seconds: int
    start: datetime
    end: datetime
    points: list[ThroughputPoint]


class ReductionPoint(BaseModel):
    date: str
    logical_written_gb: float
    used_physical_gb: float
    post_dedup_gb: float
    reduction_ratio: float | None
    dedup_ratio: float | None
    compression_ratio: float | None
    logical_growth_gb: float | None  # change since the previous day
    physical_growth_gb: float | None


class ReductionStats(BaseModel):
    days: int
    points: list[ReductionPoint]


class AlarmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    backup_target_id: uuid.UUID
    target_name: str | None = None
    sequence: str
    event_id: str
    name: str
    severity: AlarmSeverity
    location: str | None
    raised_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    cleared_at: datetime | None
    active: bool = True
    acknowledged: bool
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    ack_note: str | None


class AlarmPage(BaseModel):
    total: int
    counts: AlarmCounts
    items: list[AlarmOut]


class AckBody(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
