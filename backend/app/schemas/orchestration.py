"""Request and response bodies of the backup orchestration API (policies, assets, jobs)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import AssetType, BackupType, JobStatus, WormMode

Name = Annotated[str, Field(min_length=1, max_length=128)]
_CRON_FIELD = re.compile(r"^[0-9A-Za-z*/,\-?]+$")


def check_cron(value: str) -> str:
    """Five space separated fields (minute hour day month weekday). Structure only, not the value ranges."""
    fields = value.split()
    if len(fields) != 5 or not all(_CRON_FIELD.match(f) for f in fields):
        raise ValueError("cron_schedule needs five fields: minute hour day-of-month month day-of-week")
    return " ".join(fields)


Cron = Annotated[str, Field(max_length=64)]
Retention = Annotated[int, Field(ge=1, le=36500)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- policies -------------------------------------------------------------------------
class PolicyCreate(BaseModel):
    name: Name
    cron_schedule: Cron
    backup_type: BackupType
    retention_days: Retention
    worm_enabled: bool = False
    worm_mode: WormMode = WormMode.NONE
    enabled: bool = True

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        self.cron_schedule = check_cron(self.cron_schedule)
        if self.worm_enabled and self.worm_mode == WormMode.NONE:
            raise ValueError("worm_mode (enterprise or compliance) is required when worm_enabled is true")
        if not self.worm_enabled:
            self.worm_mode = WormMode.NONE
        return self


class PolicyPatch(BaseModel):
    name: Name | None = None
    cron_schedule: Cron | None = None
    backup_type: BackupType | None = None
    retention_days: Retention | None = None
    worm_enabled: bool | None = None
    worm_mode: WormMode | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _cron(self) -> Self:
        if self.cron_schedule is not None:
            self.cron_schedule = check_cron(self.cron_schedule)
        return self


class PolicyOut(ORM):
    id: uuid.UUID
    name: str
    cron_schedule: str
    backup_type: BackupType
    retention_days: int
    worm_enabled: bool
    worm_mode: WormMode
    enabled: bool
    created_at: datetime
    updated_at: datetime


# ---- assets ---------------------------------------------------------------------------------
class AssetCreate(BaseModel):
    backup_target_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=255)]
    asset_type: AssetType
    source_ip: Annotated[str, Field(max_length=45)] | None = None
    agent_version: Annotated[str, Field(max_length=32)] | None = None
    policy_id: uuid.UUID | None = None


class AssetPatch(BaseModel):
    """``policy_id: null`` removes the policy; leaving it out keeps it."""

    policy_id: uuid.UUID | None = None
    source_ip: Annotated[str, Field(max_length=45)] | None = None
    agent_version: Annotated[str, Field(max_length=32)] | None = None


class AssetOut(ORM):
    id: uuid.UUID
    name: str
    asset_type: AssetType
    source_ip: str | None
    agent_version: str | None
    array_asset_id: str | None
    backup_target_id: uuid.UUID
    policy_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


# ---- jobs ------------------------------------------------------------------------------------
class JobCreate(BaseModel):
    asset_id: uuid.UUID
    policy_id: uuid.UUID | None = None  # default: the policy assigned to the asset
    backup_type: BackupType | None = None  # default: the policy's type, or full without a policy


class JobOut(ORM):
    id: uuid.UUID
    task_id: str
    backup_target_id: uuid.UUID
    asset_id: uuid.UUID | None
    asset_name: str | None
    policy_id: uuid.UUID | None
    backup_type: BackupType
    status: JobStatus
    active: bool = False  # PENDING or RUNNING: still worth polling
    progress_percent: int | None = None  # filled when the appliance was asked just now
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None = None
    data_transferred_gb: float
    throughput_mb_s: float
    log_messages: list[str]
    created_at: datetime
    updated_at: datetime


class JobPage(BaseModel):
    total: int
    items: list[JobOut]


class JobSummary(BaseModel):
    """Jobs created inside the window.

    ``success_rate_percent`` is SUCCESS / (SUCCESS + FAILED + PARTIALLY_SUCCESSFUL).
    Cancelled jobs are left out.
    """

    window: Literal["24h", "7d", "30d"]
    total: int
    by_status: dict[str, int]
    active: int
    success_rate_percent: float | None
    total_transferred_gb: float
    avg_throughput_mb_s: float | None
