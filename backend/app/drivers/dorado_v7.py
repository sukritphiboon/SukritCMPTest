"""Driver for Huawei OceanStor Dorado V7 all-flash arrays (block, file and object)."""

from __future__ import annotations

from collections.abc import Sequence

from app.schemas.storage import (
    BucketInfo,
    LunGroupInfo,
    LunInfo,
    MappingInfo,
    S3Credentials,
)

from .errors import ResourceExistsError
from .huawei_base import HuaweiDeviceManagerDriver, gb_to_sectors, sectors_to_gb

TYPE_LUN, TYPE_HOST, TYPE_HOSTGROUP, TYPE_LUNGROUP = 11, 21, 14, 256
GB = 1024**3


def _lun(d: dict) -> LunInfo:
    return LunInfo(
        array_id=d["ID"],
        name=d["NAME"],
        wwn=d["WWN"],
        size_gb=sectors_to_gb(d["CAPACITY"]),
        thin=d["ALLOCTYPE"] == "1",
    )


class DoradoV7Driver(HuaweiDeviceManagerDriver):
    # ---- block -----------------------------------------------------------------
    async def create_lun(
        self, name: str, size_gb: float, thin: bool = True, pool_id: str | None = None
    ) -> LunInfo:
        data = await self.client.request(
            "POST",
            "/lun",
            json={
                "NAME": name,
                "CAPACITY": str(gb_to_sectors(size_gb)),
                "ALLOCTYPE": "1" if thin else "0",
                "PARENTID": pool_id or await self._default_pool_id(),
            },
        )
        return _lun(data)

    async def delete_lun(self, lun_id: str) -> None:
        await self.client.request("DELETE", f"/lun/{lun_id}")

    async def expand_lun(self, lun_id: str, new_size_gb: float) -> LunInfo:
        data = await self.client.request(
            "PUT", "/lun/expand", json={"ID": lun_id, "CAPACITY": str(gb_to_sectors(new_size_gb))}
        )
        return _lun(data)

    async def create_lun_group(self, name: str, lun_ids: Sequence[str] = ()) -> LunGroupInfo:
        data = await self.client.request("POST", "/lungroup", json={"NAME": name})
        for lun_id in lun_ids:
            await self.client.request(
                "POST",
                "/lungroup/associate",
                json={"ID": data["ID"], "ASSOCIATEOBJTYPE": TYPE_LUN, "ASSOCIATEOBJID": lun_id},
            )
        return LunGroupInfo(array_id=data["ID"], name=data["NAME"])

    async def map_to_host(self, lun_group_id: str, host_name: str) -> MappingInfo:
        host = await self._first_by_name("/host", host_name) or await self.client.request(
            "POST", "/host", json={"NAME": host_name}
        )
        hostgroup_name, view_name = f"hg-{host_name}", f"mv-{host_name}-{lun_group_id}"
        hostgroup = await self._first_by_name("/hostgroup", hostgroup_name)
        if hostgroup is None:
            hostgroup = await self.client.request("POST", "/hostgroup", json={"NAME": hostgroup_name})
            await self.client.request(
                "POST",
                "/hostgroup/associate",
                json={"ID": hostgroup["ID"], "ASSOCIATEOBJTYPE": TYPE_HOST, "ASSOCIATEOBJID": host["ID"]},
            )
        if await self._first_by_name("/mappingview", view_name):
            raise ResourceExistsError(
                1077948993, f"LUN group {lun_group_id} is already mapped to {host_name}."
            )
        view = await self.client.request("POST", "/mappingview", json={"NAME": view_name})
        for obj_type, obj_id in ((TYPE_LUNGROUP, lun_group_id), (TYPE_HOSTGROUP, hostgroup["ID"])):
            await self.client.request(
                "PUT",
                "/mappingview/create_associate",
                json={"ID": view["ID"], "ASSOCIATEOBJTYPE": obj_type, "ASSOCIATEOBJID": obj_id},
            )
        return MappingInfo(mapping_view_id=view["ID"], host_id=host["ID"], lun_group_id=lun_group_id)

    # ---- object -------------------------------------------------------------------
    async def create_bucket(self, name: str, owner: str, quota_gb: float | None = None) -> BucketInfo:
        body: dict = {"name": name, "owner": owner}
        if quota_gb:
            body["quota_bytes"] = int(quota_gb * GB)
        data = await self.client.request("POST", "/s3/_admin/buckets", json=body, raw_path=True)
        return _bucket(data)

    async def set_bucket_quota(self, name: str, quota_gb: float) -> BucketInfo:
        data = await self.client.request(
            "PUT",
            f"/s3/_admin/buckets/{name}/quota",
            json={"quota_bytes": int(quota_gb * GB)},
            raw_path=True,
        )
        return _bucket(data)

    async def generate_s3_credentials(self, owner: str) -> S3Credentials:
        data = await self.client.request(
            "POST", "/s3/_admin/credentials", json={"owner": owner}, raw_path=True
        )
        return S3Credentials(**data)


def _bucket(d: dict) -> BucketInfo:
    quota = d.get("quota_bytes")
    return BucketInfo(
        name=d["name"],
        owner=d["owner"],
        endpoint=d["endpoint"],
        quota_gb=quota / GB if quota else None,
    )
