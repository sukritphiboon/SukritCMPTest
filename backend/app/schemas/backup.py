"""Vendor-neutral objects returned by backup drivers."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.models.enums import AlarmSeverity, BackupType, HardwareHealth, JobStatus


class SystemInfo(BaseModel):
    model: str
    serial_number: str
    firmware_version: str
    device_id: str


class PoolMetrics(BaseModel):
    """Sizes in GB. ``dedup_ratio`` is a 0-1 fraction; the other ratios are N:1."""

    raw_capacity_gb: float
    used_physical_gb: float
    free_gb: float
    logic_written_gb: float
    post_dedup_gb: float
    dedup_ratio: float
    dedup_factor: float
    compression_ratio: float
    reduction_ratio: float


class PerformanceSample(BaseModel):
    write_throughput_mb_s: float
    read_throughput_mb_s: float
    iops: int
    active_streams: int
    sampled_at: datetime


class Component(BaseModel):
    id: str
    name: str
    status: HardwareHealth


class ControllerStatus(Component):
    cpu_percent: float
    memory_percent: float


class NvramStatus(Component):
    controller: str
    dedup_cache_hit_percent: float
    cache_usage_percent: float


class DiskStatus(Component):
    location: str
    role: str
    media: str


class HardwareStatus(BaseModel):
    controllers: list[ControllerStatus]
    nvram: list[NvramStatus]
    power_modules: list[Component]
    disks: list[DiskStatus]

    def all_components(self) -> list[Component]:
        return [*self.controllers, *self.nvram, *self.power_modules, *self.disks]

    @property
    def overall(self) -> HardwareHealth:
        statuses = {c.status for c in self.all_components()}
        if HardwareHealth.FAULT in statuses:
            return HardwareHealth.FAULT
        return HardwareHealth.DEGRADED if HardwareHealth.DEGRADED in statuses else HardwareHealth.OK


class AlarmInfo(BaseModel):
    sequence: str
    event_id: str
    name: str
    severity: AlarmSeverity
    raised_at: datetime
    location: str = ""


class PolicyInfo(BaseModel):
    array_id: str
    name: str
    cron_schedule: str
    backup_type: BackupType
    retention_days: int
    worm_enabled: bool


class AssetInfo(BaseModel):
    array_id: str
    name: str
    asset_type: str
    source_ip: str = ""
    agent_version: str = ""


class TaskInfo(BaseModel):
    task_id: str
    job_id: str
    status: JobStatus
    progress_percent: int
    started_at: datetime | None
    ended_at: datetime | None
    data_transferred_gb: float
    throughput_mb_s: float
    logs: list[str]


class WormPolicyInfo(BaseModel):
    array_id: str
    name: str
    filesystem_id: str
    mode: str
    retention_days: int


class BackupCopy(BaseModel):
    array_id: str
    name: str
    source: str
    created_at: datetime
    expires_at: datetime
    retention_days: int
    worm_locked: bool
    state: str
    size_gb: float
