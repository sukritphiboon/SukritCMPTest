"""Request and response bodies of the public REST API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.models import (
    AuditOutcome,
    DeviceModel,
    FileProtocol,
    HealthStatus,
    MappingStatus,
    Provisioning,
    QuotaStatus,
    WormMode,
)

Name = Annotated[str, Field(min_length=1, max_length=128)]
GB = Annotated[int, Field(ge=1, le=100_000_000)]
Quota = Annotated[int, Field(ge=0, le=100_000_000)]
BucketName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- devices ----------------------------------------------------------------
class DeviceCreate(BaseModel):
    name: Name
    ip_address: Annotated[str, Field(min_length=1, max_length=45)]
    management_port: Annotated[int, Field(ge=1, le=65535)] = 8088
    username: Annotated[str, Field(min_length=1, max_length=128)]
    password: SecretStr
    model: DeviceModel


class DevicePatch(BaseModel):
    name: Name | None = None
    ip_address: Annotated[str, Field(min_length=1, max_length=45)] | None = None
    management_port: Annotated[int, Field(ge=1, le=65535)] | None = None
    username: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    password: SecretStr | None = None


class DeviceOut(ORM):
    id: uuid.UUID
    name: str
    ip_address: str
    management_port: int
    model: DeviceModel
    device_id: str | None
    health_status: HealthStatus
    firmware_version: str | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DeviceTestResult(BaseModel):
    reachable: bool
    device_id: str | None = None
    health_status: HealthStatus
    active_alarms: int = 0
    error: str | None = None


# ---- tenants ----------------------------------------------------------------
class TenantCreate(BaseModel):
    name: Name
    description: str | None = None
    quota_block_gb: Quota = 0
    quota_file_gb: Quota = 0
    quota_object_gb: Quota = 0


class TenantPatch(BaseModel):
    name: Name | None = None
    description: str | None = None
    is_active: bool | None = None
    quota_block_gb: Quota | None = None
    quota_file_gb: Quota | None = None
    quota_object_gb: Quota | None = None


class TenantOut(ORM):
    id: uuid.UUID
    name: str
    description: str | None
    is_active: bool
    quota_block_gb: int
    quota_file_gb: int
    quota_object_gb: int
    created_at: datetime
    updated_at: datetime


class QuotaUsage(BaseModel):
    allocated_gb: int
    used_gb: int
    free_gb: int


class TenantUsage(BaseModel):
    block: QuotaUsage
    file: QuotaUsage
    object: QuotaUsage


# ---- volumes ------------------------------------------------------------------
class VolumeCreate(BaseModel):
    name: Name
    size_gb: GB
    thin: bool = True
    storage_device_id: uuid.UUID
    tenant_id: uuid.UUID
    protection_policy_id: uuid.UUID | None = None


class VolumeExpand(BaseModel):
    new_size_gb: GB


class VolumeMap(BaseModel):
    host_name: Name


class SnapshotCreate(BaseModel):
    name: Name


class VolumeOut(ORM):
    id: uuid.UUID
    name: str
    wwn: str | None
    size_gb: int
    provisioning: Provisioning
    mapping_status: MappingStatus
    array_lun_id: str | None
    storage_device_id: uuid.UUID
    tenant_id: uuid.UUID
    protection_policy_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


# ---- file systems -----------------------------------------------------------------
class FileSystemCreate(BaseModel):
    name: Name
    size_gb: GB
    protocol: FileProtocol
    share_name: Name | None = None  # CIFS share name; defaults to the file system name
    quota_hard_gb: GB | None = None
    quota_soft_gb: GB | None = None
    storage_device_id: uuid.UUID
    tenant_id: uuid.UUID
    protection_policy_id: uuid.UUID | None = None


class QuotaSet(BaseModel):
    hard_gb: GB
    soft_gb: GB | None = None


class FileSystemOut(ORM):
    id: uuid.UUID
    name: str
    size_gb: int
    protocol: FileProtocol
    share_path: str | None
    quota_hard_gb: int | None
    quota_soft_gb: int | None
    quota_status: QuotaStatus
    array_fs_id: str | None
    storage_device_id: uuid.UUID
    tenant_id: uuid.UUID
    protection_policy_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


# ---- buckets --------------------------------------------------------------------------
class BucketCreate(BaseModel):
    bucket_name: BucketName
    owner: Name | None = None  # defaults to the tenant name
    quota_gb: GB
    storage_device_id: uuid.UUID
    tenant_id: uuid.UUID


class BucketQuota(BaseModel):
    quota_gb: GB


class CredentialsCreate(BaseModel):
    storage_device_id: uuid.UUID
    tenant_id: uuid.UUID
    owner: Name | None = None


class CredentialsOut(BaseModel):
    """The secret key is shown only in this response and is not stored."""

    owner: str
    access_key: str
    secret_key: str
    endpoint: str


class BucketOut(ORM):
    id: uuid.UUID
    bucket_name: str
    owner: str
    quota_gb: int | None
    s3_endpoint: str
    storage_device_id: uuid.UUID
    tenant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# ---- protection policies -----------------------------------------------------------------
class PolicyCreate(BaseModel):
    name: Name
    tenant_id: uuid.UUID | None = None
    retention_days: Annotated[int, Field(ge=1, le=36500)]
    worm_mode: WormMode = WormMode.NONE
    snapshot_schedule: Annotated[str, Field(max_length=64)] | None = None
    enabled: bool = True


class PolicyPatch(BaseModel):
    name: Name | None = None
    retention_days: Annotated[int, Field(ge=1, le=36500)] | None = None
    worm_mode: WormMode | None = None
    snapshot_schedule: Annotated[str, Field(max_length=64)] | None = None
    enabled: bool | None = None


class PolicyOut(ORM):
    id: uuid.UUID
    name: str
    tenant_id: uuid.UUID | None
    retention_days: int
    worm_mode: WormMode
    snapshot_schedule: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


# ---- audit ----------------------------------------------------------------------------------
class AuditOut(ORM):
    id: uuid.UUID
    occurred_at: datetime
    actor: str
    action: str
    resource_type: str
    resource_id: str | None
    resource_name: str | None
    storage_device_id: uuid.UUID | None
    tenant_id: uuid.UUID | None
    outcome: AuditOutcome
    details: dict[str, Any] | None
    source_ip: str | None


class AuditPage(BaseModel):
    total: int
    items: list[AuditOut]
