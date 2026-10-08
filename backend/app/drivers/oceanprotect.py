"""Driver for Huawei OceanProtect backup appliances.

OceanProtect stores backups on file systems: it has no block LUNs and no object service, so
those operations raise ``NotSupportedError``. WORM and retention features are added on top.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, NoReturn

from app.schemas.storage import (
    BackupCopy,
    BucketInfo,
    LunGroupInfo,
    LunInfo,
    MappingInfo,
    S3Credentials,
    WormPolicyInfo,
)

from .errors import NotSupportedError
from .huawei_base import GB, HuaweiDeviceManagerDriver, _ts

_WORM_MODE = {"enterprise": 2, "compliance": 1}


def _unsupported(feature: str) -> NoReturn:
    raise NotSupportedError(f"OceanProtect does not support {feature}.")


class OceanProtectDriver(HuaweiDeviceManagerDriver):
    # ---- block: not offered ---------------------------------------------------------
    async def create_lun(
        self, name: str, size_gb: float, thin: bool = True, pool_id: str | None = None
    ) -> LunInfo:
        _unsupported("block LUNs")

    async def delete_lun(self, lun_id: str) -> None:
        _unsupported("block LUNs")

    async def expand_lun(self, lun_id: str, new_size_gb: float) -> LunInfo:
        _unsupported("block LUNs")

    async def create_lun_group(self, name: str, lun_ids: Sequence[str] = ()) -> LunGroupInfo:
        _unsupported("LUN groups")

    async def map_to_host(self, lun_group_id: str, host_name: str) -> MappingInfo:
        _unsupported("host mapping")

    # ---- object: not offered ----------------------------------------------------------
    async def create_bucket(self, name: str, owner: str, quota_gb: float | None = None) -> BucketInfo:
        _unsupported("object storage")

    async def delete_bucket(self, name: str) -> None:
        _unsupported("object storage")

    async def set_bucket_quota(self, name: str, quota_gb: float) -> BucketInfo:
        _unsupported("object storage")

    async def generate_s3_credentials(self, owner: str) -> S3Credentials:
        _unsupported("object storage")

    # ---- protection --------------------------------------------------------------------
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
