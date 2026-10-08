"""Unified storage driver interface (adapter pattern).

The CMP services only talk to ``StorageDriverBase``. A concrete driver translates the calls
into the REST dialect of one product. All sizes are in GB; all methods are async.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Literal

from app.schemas.storage import (
    Alarm,
    BucketInfo,
    CapacityMetrics,
    FileSystemInfo,
    LunGroupInfo,
    LunInfo,
    MappingInfo,
    PerformanceMetrics,
    QuotaInfo,
    ReductionRatio,
    S3Credentials,
    ShareInfo,
    SnapshotInfo,
)


class StorageDriverBase(ABC):
    # ---- lifecycle ----------------------------------------------------------
    @abstractmethod
    async def connect(self) -> None:
        """Log in to the array. Called automatically on first use."""

    @abstractmethod
    async def close(self) -> None:
        """Log out and release the connection."""

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # ---- block ---------------------------------------------------------------
    @abstractmethod
    async def create_lun(
        self, name: str, size_gb: float, thin: bool = True, pool_id: str | None = None
    ) -> LunInfo: ...

    @abstractmethod
    async def delete_lun(self, lun_id: str) -> None: ...

    @abstractmethod
    async def expand_lun(self, lun_id: str, new_size_gb: float) -> LunInfo:
        """Grow a LUN to ``new_size_gb`` (the new total size, not the increment)."""

    @abstractmethod
    async def create_lun_group(self, name: str, lun_ids: Sequence[str] = ()) -> LunGroupInfo: ...

    @abstractmethod
    async def map_to_host(self, lun_group_id: str, host_name: str) -> MappingInfo:
        """Expose a LUN group to a host (the host is created when it does not exist)."""

    # ---- snapshots (block and file) ---------------------------------------------
    @abstractmethod
    async def create_snapshot(
        self, resource_id: str, name: str, resource_type: Literal["lun", "filesystem"] = "lun"
    ) -> SnapshotInfo: ...

    # ---- file ------------------------------------------------------------------
    @abstractmethod
    async def create_filesystem(
        self, name: str, size_gb: float, pool_id: str | None = None
    ) -> FileSystemInfo: ...

    @abstractmethod
    async def delete_filesystem(self, filesystem_id: str) -> None: ...

    @abstractmethod
    async def create_share(
        self, filesystem_id: str, protocol: Literal["nfs", "cifs"], name: str | None = None
    ) -> ShareInfo: ...

    @abstractmethod
    async def set_quota(
        self, filesystem_id: str, hard_gb: float, soft_gb: float | None = None
    ) -> QuotaInfo: ...

    # ---- object ----------------------------------------------------------------
    @abstractmethod
    async def create_bucket(self, name: str, owner: str, quota_gb: float | None = None) -> BucketInfo: ...

    @abstractmethod
    async def delete_bucket(self, name: str) -> None: ...

    @abstractmethod
    async def set_bucket_quota(self, name: str, quota_gb: float) -> BucketInfo: ...

    @abstractmethod
    async def generate_s3_credentials(self, owner: str) -> S3Credentials: ...

    # ---- telemetry ---------------------------------------------------------------
    @abstractmethod
    async def get_capacity_metrics(self) -> CapacityMetrics: ...

    @abstractmethod
    async def get_reduction_ratio(self) -> ReductionRatio: ...

    @abstractmethod
    async def get_active_alarms(self) -> list[Alarm]: ...

    @abstractmethod
    async def get_performance_metrics(self) -> PerformanceMetrics: ...
