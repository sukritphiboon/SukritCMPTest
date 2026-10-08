"""Behaviour shared by every Huawei DeviceManager based driver (file services and telemetry)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from app.schemas.storage import (
    Alarm,
    CapacityMetrics,
    FileSystemInfo,
    PerformanceMetrics,
    QuotaInfo,
    ReductionRatio,
    ShareInfo,
    SnapshotInfo,
)

from .base import StorageDriverBase
from .errors import ResourceNotFoundError
from .huawei_client import DeviceConnection, HuaweiClient

SECTOR_BYTES = 512
GB = 1024**3
TYPE_LUN, TYPE_FS = 11, 40
_SEVERITY = {"1": "low", "2": "medium", "3": "major", "4": "critical"}


def gb_to_sectors(gb: float) -> int:
    return int(gb * GB // SECTOR_BYTES)


def sectors_to_gb(sectors: str | int) -> float:
    return round(int(sectors) * SECTOR_BYTES / GB, 2)


def _ts(epoch: str | int) -> datetime:
    return datetime.fromtimestamp(int(epoch), UTC)


class HuaweiDeviceManagerDriver(StorageDriverBase):
    def __init__(self, connection: DeviceConnection, transport: httpx.AsyncBaseTransport | None = None):
        self.client = HuaweiClient(connection, transport=transport)

    async def connect(self) -> None:
        await self.client.login()

    async def close(self) -> None:
        await self.client.close()

    async def _default_pool_id(self) -> str:
        pools = await self.client.get_list("/storagepool")
        if not pools:
            raise ResourceNotFoundError(1077948996, "No storage pool found.")
        return pools[0]["ID"]

    async def _first_by_name(self, path: str, name: str) -> dict[str, Any] | None:
        found = await self.client.get_list(path, filter=f"NAME::{name}")
        return found[0] if found else None

    # ---- snapshots ----------------------------------------------------------------
    async def create_snapshot(
        self, resource_id: str, name: str, resource_type: Literal["lun", "filesystem"] = "lun"
    ) -> SnapshotInfo:
        data = await self.client.request(
            "POST",
            "/snapshot",
            json={
                "NAME": name,
                "PARENTID": resource_id,
                "PARENTTYPE": TYPE_LUN if resource_type == "lun" else TYPE_FS,
            },
        )
        return SnapshotInfo(
            array_id=data["ID"], name=data["NAME"], resource_id=resource_id, resource_type=resource_type
        )

    # ---- file -----------------------------------------------------------------------
    async def create_filesystem(
        self, name: str, size_gb: float, pool_id: str | None = None
    ) -> FileSystemInfo:
        data = await self.client.request(
            "POST",
            "/filesystem",
            json={
                "NAME": name,
                "CAPACITY": str(gb_to_sectors(size_gb)),
                "PARENTID": pool_id or await self._default_pool_id(),
            },
        )
        return FileSystemInfo(array_id=data["ID"], name=data["NAME"], size_gb=sectors_to_gb(data["CAPACITY"]))

    async def create_share(
        self, filesystem_id: str, protocol: Literal["nfs", "cifs"], name: str | None = None
    ) -> ShareInfo:
        if protocol == "nfs":
            data = await self.client.request("POST", "/NFSHARE", json={"FSID": filesystem_id})
            return ShareInfo(array_id=data["ID"], protocol="nfs", path=data["SHAREPATH"])
        if name is None:
            fs = await self.client.request("GET", f"/filesystem/{filesystem_id}")
            name = fs["NAME"]
        data = await self.client.request("POST", "/CIFSHARE", json={"FSID": filesystem_id, "NAME": name})
        return ShareInfo(array_id=data["ID"], protocol="cifs", path=data["SHAREPATH"], name=data["NAME"])

    async def set_quota(self, filesystem_id: str, hard_gb: float, soft_gb: float | None = None) -> QuotaInfo:
        soft_gb = hard_gb * 0.8 if soft_gb is None else soft_gb
        data = await self.client.request(
            "POST",
            "/FS_QUOTA",
            json={
                "PARENTID": filesystem_id,
                "PARENTTYPE": TYPE_FS,
                "QUOTATYPE": "1",
                "SPACEHARDQUOTA": int(hard_gb * GB),
                "SPACESOFTQUOTA": int(soft_gb * GB),
            },
        )
        return QuotaInfo(
            array_id=data["ID"],
            hard_gb=int(data["SPACEHARDQUOTA"]) / GB,
            soft_gb=int(data["SPACESOFTQUOTA"]) / GB,
        )

    # ---- telemetry ---------------------------------------------------------------------
    async def _pool(self) -> dict[str, Any]:
        pools = await self.client.get_list("/storagepool")
        if not pools:
            raise ResourceNotFoundError(1077948996, "No storage pool found.")
        return pools[0]

    async def get_capacity_metrics(self) -> CapacityMetrics:
        pool = await self._pool()
        total = sectors_to_gb(pool["USERTOTALCAPACITY"])
        used = sectors_to_gb(pool["USERCONSUMEDCAPACITY"])
        return CapacityMetrics(
            total_gb=total,
            used_gb=used,
            free_gb=sectors_to_gb(pool["USERFREECAPACITY"]),
            provisioned_gb=sectors_to_gb(pool["PROVISIONEDCAPACITY"]),
            used_percent=round(used / total * 100, 2) if total else 0.0,
        )

    async def get_reduction_ratio(self) -> ReductionRatio:
        pool = await self._pool()
        return ReductionRatio(
            overall=float(pool["DATAREDUCTION_RATIO"]),
            thin=float(pool["SMARTTHIN_RATIO"]),
            dedupe=float(pool["SMARTDEDUPE_RATIO"]),
            compression=float(pool["SMARTCOMPRESSION_RATIO"]),
        )

    async def get_active_alarms(self) -> list[Alarm]:
        return [
            Alarm(
                alarm_id=a["eventID"],
                name=a["name"],
                severity=_SEVERITY.get(a["level"], "medium"),
                started_at=_ts(a["startTime"]),
                location=a.get("location", ""),
            )
            for a in await self.client.get_list("/alarm/currentalarm")
        ]

    async def get_performance_metrics(self) -> PerformanceMetrics:
        d = await self.client.request("GET", "/performance_statistic/cur_statistic_data")
        return PerformanceMetrics(
            iops=d["IOPS"],
            read_iops=d["READ_IOPS"],
            write_iops=d["WRITE_IOPS"],
            latency_ms=round(d["LATENCY_US"] / 1000, 3),
            throughput_mbps=d["BANDWIDTH_MBPS"],
            read_throughput_mbps=d["READ_BANDWIDTH_MBPS"],
            write_throughput_mbps=d["WRITE_BANDWIDTH_MBPS"],
            sampled_at=_ts(d["TIMESTAMP"]),
        )
