"""Backup appliance driver interface (adapter pattern).

Services talk only to ``BackupDriverBase``; a concrete driver translates the calls into the REST
dialect of one product. All sizes are GB and all methods are async.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from app.schemas.backup import (
    AlarmInfo,
    AssetInfo,
    BackupCopy,
    HardwareStatus,
    PerformanceSample,
    PolicyInfo,
    PoolMetrics,
    SystemInfo,
    TaskInfo,
    WormPolicyInfo,
)


class BackupDriverBase(ABC):
    # ---- lifecycle ------------------------------------------------------------
    @abstractmethod
    async def connect(self) -> None:
        """Log in. Called once before use."""

    @abstractmethod
    async def close(self) -> None:
        """Log out and release the connection."""

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ---- telemetry -----------------------------------------------------------------
    @abstractmethod
    async def get_system_info(self) -> SystemInfo: ...

    @abstractmethod
    async def get_pool_metrics(self) -> PoolMetrics: ...

    @abstractmethod
    async def get_performance(self) -> PerformanceSample: ...

    @abstractmethod
    async def get_hardware_status(self) -> HardwareStatus: ...

    @abstractmethod
    async def get_active_alarms(self) -> list[AlarmInfo]: ...

    # ---- backup orchestration ------------------------------------------------------------
    @abstractmethod
    async def list_policies(self) -> list[PolicyInfo]: ...

    @abstractmethod
    async def create_policy(
        self,
        name: str,
        cron_schedule: str,
        backup_type: Literal["full", "incremental"],
        retention_days: int,
        worm_enabled: bool = False,
    ) -> PolicyInfo: ...

    @abstractmethod
    async def delete_policy(self, policy_id: str) -> None: ...

    @abstractmethod
    async def create_asset(
        self, name: str, asset_type: str, source_ip: str = "", agent_version: str = ""
    ) -> AssetInfo: ...

    @abstractmethod
    async def trigger_backup(
        self,
        asset_id: str,
        policy_id: str | None = None,
        backup_type: Literal["full", "incremental"] | None = None,
    ) -> str:
        """Start an ad-hoc backup and return the appliance task id (the call itself is asynchronous)."""

    @abstractmethod
    async def get_task(self, task_id: str) -> TaskInfo: ...

    @abstractmethod
    async def cancel_task(self, task_id: str) -> TaskInfo: ...

    # ---- immutability -----------------------------------------------------------------------
    @abstractmethod
    async def create_filesystem(self, name: str, size_gb: float) -> str:
        """Create a backup file system and return its id (WORM policies attach to file systems)."""

    @abstractmethod
    async def create_worm_policy(
        self,
        filesystem_id: str,
        name: str,
        retention_days: int,
        mode: Literal["enterprise", "compliance"] = "compliance",
    ) -> WormPolicyInfo: ...

    @abstractmethod
    async def list_backup_copies(self) -> list[BackupCopy]: ...
