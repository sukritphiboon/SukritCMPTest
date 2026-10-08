"""Request and response bodies of the target and audit API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.models import ApplianceModel, AuditOutcome, HealthStatus

Name = Annotated[str, Field(min_length=1, max_length=128)]
Host = Annotated[str, Field(min_length=1, max_length=45)]
Port = Annotated[int, Field(ge=1, le=65535)]
Login = Annotated[str, Field(min_length=1, max_length=128)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TargetCreate(BaseModel):
    name: Name
    ip_address: Host
    management_port: Port = 8088
    username: Login
    password: SecretStr
    model: ApplianceModel


class TargetPatch(BaseModel):
    name: Name | None = None
    ip_address: Host | None = None
    management_port: Port | None = None
    username: Login | None = None
    password: SecretStr | None = None


class TargetOut(ORM):
    id: uuid.UUID
    name: str
    ip_address: str
    management_port: int
    model: ApplianceModel
    serial_number: str | None
    device_id: str | None
    health_status: HealthStatus
    firmware_version: str | None
    last_seen_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class TargetTestResult(BaseModel):
    reachable: bool
    health_status: HealthStatus
    serial_number: str | None = None
    firmware_version: str | None = None
    error: str | None = None


class AuditOut(ORM):
    id: uuid.UUID
    occurred_at: datetime
    actor: str
    action: str
    resource_type: str
    resource_id: str | None
    resource_name: str | None
    backup_target_id: uuid.UUID | None
    outcome: AuditOutcome
    details: dict[str, Any] | None
    source_ip: str | None


class AuditPage(BaseModel):
    total: int
    items: list[AuditOut]
