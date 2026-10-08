"""Driver for Huawei OceanProtect backup appliances (DeviceManager REST API, port 8088)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from app.models.enums import AlarmSeverity, BackupType, HardwareHealth, JobStatus
from app.schemas.backup import (
    AlarmInfo,
    AssetInfo,
    BackupCopy,
    Component,
    ControllerStatus,
    DiskStatus,
    HardwareStatus,
    NvramStatus,
    PerformanceSample,
    PolicyInfo,
    PoolMetrics,
    SystemInfo,
    TaskInfo,
    WormPolicyInfo,
)
from app.services.analytics import ratios

from .base import BackupDriverBase
from .errors import ResourceNotFoundError
from .huawei_client import DeviceConnection, HuaweiClient

SECTOR_BYTES = 512
GB = 1024**3
# level of /alarm/currentalarm in the reference: 3 = warning, 5 = major, 6 = critical
_SEVERITY = {"3": AlarmSeverity.WARNING, "5": AlarmSeverity.MAJOR, "6": AlarmSeverity.CRITICAL}
_WORM_MODE = {"enterprise": 2, "compliance": 1}
_ASSET_TYPES = {"vmware": "VMware", "database": "Database", "file_share": "FileShare", "lun": "LUN"}
_ASSET_TYPES_BACK = {v: k for k, v in _ASSET_TYPES.items()}


def _gb(sectors: str | int) -> float:
    return int(sectors) * SECTOR_BYTES / GB


def _ts(epoch: str | int) -> datetime:
    return datetime.fromtimestamp(int(epoch), UTC)


def _health(code: str) -> HardwareHealth:
    """1 = normal, 2 = fault, anything else (pre-fail, degraded ...) = degraded."""
    return {"1": HardwareHealth.OK, "2": HardwareHealth.FAULT}.get(str(code), HardwareHealth.DEGRADED)


def _policy(d: dict[str, Any]) -> PolicyInfo:
    return PolicyInfo(
        array_id=d["ID"],
        name=d["NAME"],
        cron_schedule=d["CRON"],
        backup_type=BackupType(d["BACKUPTYPENAME"]),
        retention_days=int(d["RETENTIONDAYS"]),
        worm_enabled=bool(d["WORMENABLED"]),
    )


class OceanProtectDriver(BackupDriverBase):
    def __init__(self, connection: DeviceConnection, transport: httpx.AsyncBaseTransport | None = None):
        self.client = HuaweiClient(connection, transport=transport)

    async def connect(self) -> None:
        await self.client.login()

    async def close(self) -> None:
        await self.client.close()

    # ---- telemetry -----------------------------------------------------------------
    async def get_system_info(self) -> SystemInfo:
        d = await self.client.request("GET", "/system/")
        return SystemInfo(
            model=d["PRODUCTMODE"],
            serial_number=d["SN"],
            firmware_version=d["PRODUCTVERSION"],
            device_id=d["ID"],
        )

    async def get_pool_metrics(self) -> PoolMetrics:
        pools = await self.client.get_list("/storagepool")
        if not pools:
            raise ResourceNotFoundError(1077948996, "No storage pool found.")
        p = pools[0]
        # Documented fields: DEDUPINVOLVEDCAPACITY went into deduplication (the ingested data),
        # DEDUPEDCAPACITY is what deduplication saved, USERCONSUMEDCAPACITY is what is on disk.
        # All of them count 512-byte sectors.
        physical = int(p["USERCONSUMEDCAPACITY"])
        ingested = int(p["DEDUPINVOLVEDCAPACITY"])
        post = ingested - int(p["DEDUPEDCAPACITY"])
        return PoolMetrics(
            raw_capacity_gb=_gb(p["USERTOTALCAPACITY"]),
            used_physical_gb=_gb(physical),
            free_gb=_gb(p["USERFREECAPACITY"]),
            logic_written_gb=_gb(ingested),
            post_dedup_gb=_gb(post),
            dedup_ratio=ratios.dedup_ratio(ingested, post),
            dedup_factor=ratios.dedup_factor(ingested, post),
            compression_ratio=ratios.compression_ratio(post, physical),
            reduction_ratio=ratios.reduction_ratio(ingested, physical),
        )

    async def get_performance(self) -> PerformanceSample:
        d = await self.client.request("GET", "/performancedata")
        return PerformanceSample(
            write_throughput_mb_s=d["WRITE_THROUGHPUT_MBPS"],
            read_throughput_mb_s=d["READ_THROUGHPUT_MBPS"],
            iops=d["IOPS"],
            active_streams=d["ACTIVE_STREAMS"],
            sampled_at=_ts(d["TIMESTAMP"]),
        )

    async def get_hardware_status(self) -> HardwareStatus:
        controllers = await self.client.get_list("/controller")
        nvram = await self.client.get_list("/nvram")
        power = await self.client.get_list("/power")
        disks = await self.client.get_list("/disk")
        return HardwareStatus(
            controllers=[
                ControllerStatus(
                    id=c["ID"],
                    name=c["NAME"],
                    status=_health(c["HEALTHSTATUS"]),
                    cpu_percent=c["CPUUSAGE"],
                    memory_percent=c["MEMORYUSAGE"],
                )
                for c in controllers
            ],
            nvram=[
                NvramStatus(
                    id=n["ID"],
                    name=f"NVRAM {n['ID']}",
                    status=_health(n["HEALTHSTATUS"]),
                    controller=n["CONTROLLER"],
                    dedup_cache_hit_percent=n["DEDUPCACHEHITRATIO"],
                    cache_usage_percent=n["CACHEUSAGE"],
                )
                for n in nvram
            ],
            power_modules=[
                Component(id=p["ID"], name=p["NAME"], status=_health(p["HEALTHSTATUS"])) for p in power
            ],
            disks=[
                DiskStatus(
                    id=d["ID"],
                    name=f"Disk {d['LOCATION']}",
                    status=_health(d["HEALTHSTATUS"]),
                    location=d["LOCATION"],
                    role=d["ROLE"],
                    media=d["TYPE"],
                )
                for d in disks
            ],
        )

    async def get_active_alarms(self) -> list[AlarmInfo]:
        return [
            AlarmInfo(
                sequence=str(a["sequence"]),
                event_id=a["strEventID"],  # hexadecimal; eventID is the same number in decimal
                name=a["name"],
                severity=_SEVERITY.get(str(a["level"]), AlarmSeverity.WARNING),
                raised_at=_ts(a["startTime"]),
                location=a.get("location", ""),
            )
            for a in await self.client.get_list("/alarm/currentalarm")
        ]

    # ---- backup orchestration ---------------------------------------------------------------
    async def list_policies(self) -> list[PolicyInfo]:
        return [_policy(p) for p in await self.client.get_list("/backup_policy")]

    async def create_policy(
        self,
        name: str,
        cron_schedule: str,
        backup_type: Literal["full", "incremental"],
        retention_days: int,
        worm_enabled: bool = False,
    ) -> PolicyInfo:
        data = await self.client.request(
            "POST",
            "/backup_policy",
            json={
                "NAME": name,
                "CRON": cron_schedule,
                "BACKUPTYPE": 1 if backup_type == "full" else 2,
                "RETENTIONDAYS": retention_days,
                "WORMENABLED": worm_enabled,
            },
        )
        return _policy(data)

    async def delete_policy(self, policy_id: str) -> None:
        await self.client.request("DELETE", f"/backup_policy/{policy_id}")

    async def create_asset(
        self, name: str, asset_type: str, source_ip: str = "", agent_version: str = ""
    ) -> AssetInfo:
        body = {"NAME": name, "TYPE": _ASSET_TYPES[asset_type], "SOURCEIP": source_ip}
        if agent_version:
            body["AGENTVERSION"] = agent_version
        d = await self.client.request("POST", "/asset", json=body)
        return AssetInfo(
            array_id=d["ID"],
            name=d["NAME"],
            asset_type=_ASSET_TYPES_BACK[d["TYPE"]],
            source_ip=d["SOURCEIP"],
            agent_version=d["AGENTVERSION"],
        )

    async def delete_asset(self, asset_id: str) -> None:
        await self.client.request("DELETE", f"/asset/{asset_id}")

    async def trigger_backup(
        self,
        asset_id: str,
        policy_id: str | None = None,
        backup_type: Literal["full", "incremental"] | None = None,
    ) -> str:
        body: dict[str, Any] = {"ASSETID": asset_id}
        if policy_id:
            body["POLICYID"] = policy_id
        if backup_type:
            body["BACKUPTYPE"] = 1 if backup_type == "full" else 2
        data = await self.client.request("POST", "/backup_job", json=body)
        return data["taskId"]

    async def get_task(self, task_id: str) -> TaskInfo:
        return self._task(await self.client.request("GET", f"/task_list/{task_id}"))

    async def cancel_task(self, task_id: str) -> TaskInfo:
        return self._task(await self.client.request("PUT", f"/task_list/{task_id}/cancel"))

    @staticmethod
    def _task(d: dict[str, Any]) -> TaskInfo:
        return TaskInfo(
            task_id=d["taskId"],
            job_id=d["jobId"],
            status=JobStatus(d["STATUS"]),
            progress_percent=d["PROGRESS"],
            started_at=_ts(d["STARTTIME"]) if d["STARTTIME"] else None,
            ended_at=_ts(d["ENDTIME"]) if d["ENDTIME"] else None,
            data_transferred_gb=d["DATATRANSFERREDBYTES"] / GB,
            throughput_mb_s=d["THROUGHPUTMBPS"],
            logs=list(d["LOGS"]),
        )

    # ---- immutability -------------------------------------------------------------------------
    async def create_filesystem(self, name: str, size_gb: float) -> str:
        data = await self.client.request(
            "POST", "/filesystem", json={"NAME": name, "CAPACITY": str(int(size_gb * GB // SECTOR_BYTES))}
        )
        return data["ID"]

    async def create_worm_policy(
        self,
        filesystem_id: str,
        name: str,
        retention_days: int,
        mode: Literal["enterprise", "compliance"] = "compliance",
    ) -> WormPolicyInfo:
        data = await self.client.request(
            "POST",
            "/worm_policy",
            json={
                "NAME": name,
                "FSID": filesystem_id,
                "MODE": _WORM_MODE[mode],
                "PROTECTPERIOD": retention_days,
            },
        )
        return WormPolicyInfo(
            array_id=data["ID"],
            name=data["NAME"],
            filesystem_id=filesystem_id,
            mode=data["MODENAME"],
            retention_days=int(data["PROTECTPERIOD"]),
        )

    async def list_backup_copies(self) -> list[BackupCopy]:
        return [
            BackupCopy(
                array_id=c["ID"],
                name=c["NAME"],
                source=c["SOURCE"],
                created_at=_ts(c["CREATETIME"]),
                expires_at=_ts(c["EXPIRETIME"]),
                retention_days=c["RETENTIONDAYS"],
                worm_locked=c["WORM"],
                state=c["STATE"],
                size_gb=int(c["SIZE"]) / GB,
            )
            for c in await self.client.get_list("/backup_retention")
        ]
