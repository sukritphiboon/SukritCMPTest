"""Vendor-neutral data objects returned by storage drivers."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LunInfo(BaseModel):
    array_id: str
    name: str
    wwn: str
    size_gb: float
    thin: bool


class LunGroupInfo(BaseModel):
    array_id: str
    name: str


class MappingInfo(BaseModel):
    mapping_view_id: str
    host_id: str
    lun_group_id: str


class SnapshotInfo(BaseModel):
    array_id: str
    name: str
    resource_id: str
    resource_type: Literal["lun", "filesystem"]


class FileSystemInfo(BaseModel):
    array_id: str
    name: str
    size_gb: float


class ShareInfo(BaseModel):
    array_id: str
    protocol: Literal["nfs", "cifs"]
    path: str
    name: str | None = None


class QuotaInfo(BaseModel):
    array_id: str
    hard_gb: float
    soft_gb: float


class BucketInfo(BaseModel):
    name: str
    owner: str
    endpoint: str
    quota_gb: float | None = None


class S3Credentials(BaseModel):
    owner: str
    access_key: str
    secret_key: str = Field(repr=False)
    endpoint: str


class CapacityMetrics(BaseModel):
    total_gb: float
    used_gb: float
    free_gb: float
    provisioned_gb: float
    used_percent: float


class ReductionRatio(BaseModel):
    """Ratios are expressed as N in 'N:1'."""

    overall: float
    thin: float
    dedupe: float
    compression: float


class Alarm(BaseModel):
    alarm_id: str
    name: str
    severity: Literal["low", "medium", "major", "critical"]
    started_at: datetime
    location: str = ""


class PerformanceMetrics(BaseModel):
    iops: int
    read_iops: int
    write_iops: int
    latency_ms: float
    throughput_mbps: float
    read_throughput_mbps: float
    write_throughput_mbps: float
    sampled_at: datetime


class WormPolicyInfo(BaseModel):
    array_id: str
    name: str
    filesystem_id: str
    mode: Literal["enterprise", "compliance"]
    retention_days: int


class BackupCopy(BaseModel):
    array_id: str
    name: str
    source: str
    created_at: datetime
    expires_at: datetime
    retention_days: int
    worm_locked: bool
    state: Literal["locked", "retained", "expired"]
    size_gb: float
