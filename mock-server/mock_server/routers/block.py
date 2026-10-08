"""LUN, LUN group, host, host group, mapping view and snapshot endpoints (Dorado only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from .. import envelope as E
from ..deps import require_block, require_session, select

router = APIRouter(prefix="/deviceManager/rest/{device_id}")

TYPE_LUN, TYPE_FS, TYPE_HOST, TYPE_HOSTGROUP = 11, 40, 21, 14
TYPE_LUNGROUP, TYPE_MAPPINGVIEW = 256, 245


def _wwn(state) -> str:
    return "6" + "".join(state.rng.choice("0123456789abcdef") for _ in range(31))


def _refresh_exposure(state) -> None:
    """A LUN is exposed when its LUN group sits in a mapping view that has a host group."""
    exposed: set[str] = set()
    for view in state.objects["mappingview"].values():
        if view.get("HOSTGROUPID") and view.get("LUNGROUPID"):
            group = state.objects["lungroup"].get(view["LUNGROUPID"])
            if group:
                exposed.update(group["LUNS"])
    for lun in state.objects["lun"].values():
        lun["EXPOSEDTOINITIATOR"] = "true" if lun["ID"] in exposed else "false"


def _public(obj: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in obj.items() if k not in ("LUNS", "HOSTS")}


# ---- LUN -----------------------------------------------------------------
@router.post("/lun")
async def create_lun(request: Request, body: dict[str, Any] = Body(...)):
    state = require_block(request)
    E.require(body, "NAME", "CAPACITY")
    state.ensure_unique_name("lun", body["NAME"])
    capacity = int(body["CAPACITY"])
    if capacity <= 0:
        raise E.HuaweiError(E.PARAM_ERROR, "CAPACITY must be positive.")
    state.check_space(capacity * 512)
    lun = state.create(
        "lun",
        NAME=body["NAME"],
        CAPACITY=str(capacity),
        ALLOCTYPE=str(body.get("ALLOCTYPE", "1")),
        PARENTID=str(body.get("PARENTID", state.pool["ID"])),
        PARENTNAME=state.pool["NAME"],
        WWN=_wwn(state),
        DESCRIPTION=body.get("DESCRIPTION", ""),
        ENABLECOMPRESSION=str(body.get("ENABLECOMPRESSION", "true")).lower(),
        ENABLEDEDUP=str(body.get("ENABLEDEDUP", "true")).lower(),
        HEALTHSTATUS="1",
        RUNNINGSTATUS="27",
        EXPOSEDTOINITIATOR="false",
        ISADD2LUNGROUP="false",
    )
    return E.ok(lun)


@router.get("/lun")
async def list_luns(request: Request, filter: str | None = None, range: str | None = None):
    state = require_block(request)
    return E.ok(select(list(state.objects["lun"].values()), filter, range))


@router.get("/lun/{lun_id}")
async def get_lun(request: Request, lun_id: str):
    state = require_block(request)
    return E.ok(state.get("lun", lun_id))


@router.put("/lun/expand")
async def expand_lun(request: Request, body: dict[str, Any] = Body(...)):
    state = require_block(request)
    E.require(body, "ID", "CAPACITY")
    lun = state.get("lun", body["ID"])
    new = int(body["CAPACITY"])
    old = int(lun["CAPACITY"])
    if new <= old:
        raise E.HuaweiError(E.PARAM_ERROR, "The new capacity must be larger than the current one.")
    state.check_space((new - old) * 512)
    lun["CAPACITY"] = str(new)
    return E.ok(lun)


@router.delete("/lun/{lun_id}")
async def delete_lun(request: Request, lun_id: str):
    state = require_block(request)
    lun = state.get("lun", lun_id)
    if lun["EXPOSEDTOINITIATOR"] == "true":
        raise E.HuaweiError(E.OBJECT_IN_USE, "The LUN is mapped to a host and cannot be deleted.")
    for group in state.objects["lungroup"].values():
        if lun_id in group["LUNS"]:
            group["LUNS"].remove(lun_id)
    for snap_id in [s["ID"] for s in state.objects["snapshot"].values() if s["PARENTID"] == lun_id]:
        state.delete("snapshot", snap_id)
    state.delete("lun", lun_id)
    return E.ok()


# ---- LUN group -------------------------------------------------------------
@router.post("/lungroup")
async def create_lungroup(request: Request, body: dict[str, Any] = Body(...)):
    state = require_block(request)
    E.require(body, "NAME")
    state.ensure_unique_name("lungroup", body["NAME"])
    group = state.create("lungroup", NAME=body["NAME"], TYPE=TYPE_LUNGROUP, LUNS=[], DESCRIPTION="")
    return E.ok(_public(group))


@router.get("/lungroup")
async def list_lungroups(request: Request, filter: str | None = None, range: str | None = None):
    state = require_block(request)
    items = [_public(g) for g in state.objects["lungroup"].values()]
    return E.ok(select(items, filter, range))


@router.post("/lungroup/associate")
async def lungroup_associate(request: Request, body: dict[str, Any] = Body(...)):
    state = require_block(request)
    E.require(body, "ID", "ASSOCIATEOBJTYPE", "ASSOCIATEOBJID")
    if int(body["ASSOCIATEOBJTYPE"]) != TYPE_LUN:
        raise E.HuaweiError(E.PARAM_ERROR, "Only LUNs can be added to a LUN group.")
    group = state.get("lungroup", body["ID"])
    lun = state.get("lun", body["ASSOCIATEOBJID"])
    if lun["ID"] not in group["LUNS"]:
        group["LUNS"].append(lun["ID"])
    lun["ISADD2LUNGROUP"] = "true"
    _refresh_exposure(state)
    return E.ok()


@router.delete("/lungroup/{group_id}")
async def delete_lungroup(request: Request, group_id: str):
    state = require_block(request)
    group = state.get("lungroup", group_id)
    if any(v.get("LUNGROUPID") == group_id for v in state.objects["mappingview"].values()):
        raise E.HuaweiError(E.OBJECT_IN_USE, "The LUN group belongs to a mapping view.")
    for lun_id in group["LUNS"]:
        lun = state.objects["lun"].get(lun_id)
        if lun:
            lun["ISADD2LUNGROUP"] = "false"
    state.delete("lungroup", group_id)
    return E.ok()


# ---- host / host group ------------------------------------------------------
@router.post("/host")
async def create_host(request: Request, body: dict[str, Any] = Body(...)):
    state = require_block(request)
    E.require(body, "NAME")
    state.ensure_unique_name("host", body["NAME"])
    host = state.create(
        "host",
        NAME=body["NAME"],
        TYPE=TYPE_HOST,
        OPERATIONSYSTEM=str(body.get("OPERATIONSYSTEM", "0")),
        IP=body.get("IP", ""),
    )
    return E.ok(host)


@router.get("/host")
async def list_hosts(request: Request, filter: str | None = None, range: str | None = None):
    state = require_block(request)
    return E.ok(select(list(state.objects["host"].values()), filter, range))


@router.post("/hostgroup")
async def create_hostgroup(request: Request, body: dict[str, Any] = Body(...)):
    state = require_block(request)
    E.require(body, "NAME")
    state.ensure_unique_name("hostgroup", body["NAME"])
    group = state.create("hostgroup", NAME=body["NAME"], TYPE=TYPE_HOSTGROUP, HOSTS=[])
    return E.ok(_public(group))


@router.get("/hostgroup")
async def list_hostgroups(request: Request, filter: str | None = None, range: str | None = None):
    state = require_block(request)
    items = [_public(g) for g in state.objects["hostgroup"].values()]
    return E.ok(select(items, filter, range))


@router.post("/hostgroup/associate")
async def hostgroup_associate(request: Request, body: dict[str, Any] = Body(...)):
    state = require_block(request)
    E.require(body, "ID", "ASSOCIATEOBJTYPE", "ASSOCIATEOBJID")
    if int(body["ASSOCIATEOBJTYPE"]) != TYPE_HOST:
        raise E.HuaweiError(E.PARAM_ERROR, "Only hosts can be added to a host group.")
    group = state.get("hostgroup", body["ID"])
    host = state.get("host", body["ASSOCIATEOBJID"])
    if host["ID"] not in group["HOSTS"]:
        group["HOSTS"].append(host["ID"])
    return E.ok()


# ---- mapping view -------------------------------------------------------------
@router.post("/mappingview")
async def create_mappingview(request: Request, body: dict[str, Any] = Body(...)):
    state = require_block(request)
    E.require(body, "NAME")
    state.ensure_unique_name("mappingview", body["NAME"])
    view = state.create(
        "mappingview", NAME=body["NAME"], TYPE=TYPE_MAPPINGVIEW, HOSTGROUPID="", LUNGROUPID=""
    )
    return E.ok(view)


@router.get("/mappingview")
async def list_mappingviews(request: Request, filter: str | None = None, range: str | None = None):
    state = require_block(request)
    return E.ok(select(list(state.objects["mappingview"].values()), filter, range))


@router.put("/mappingview/create_associate")
async def mappingview_associate(request: Request, body: dict[str, Any] = Body(...)):
    state = require_block(request)
    E.require(body, "ID", "ASSOCIATEOBJTYPE", "ASSOCIATEOBJID")
    view = state.get("mappingview", body["ID"])
    obj_type, obj_id = int(body["ASSOCIATEOBJTYPE"]), str(body["ASSOCIATEOBJID"])
    if obj_type == TYPE_LUNGROUP:
        state.get("lungroup", obj_id)
        view["LUNGROUPID"] = obj_id
    elif obj_type == TYPE_HOSTGROUP:
        state.get("hostgroup", obj_id)
        view["HOSTGROUPID"] = obj_id
    else:
        raise E.HuaweiError(E.PARAM_ERROR, "Unsupported associated object type.")
    _refresh_exposure(state)
    return E.ok(view)


# ---- snapshot (LUN on Dorado, file system on both) ---------------------------------
@router.post("/snapshot")
async def create_snapshot(request: Request, body: dict[str, Any] = Body(...)):
    state = require_session(request)
    E.require(body, "NAME", "PARENTID")
    parent_type = int(body.get("PARENTTYPE", TYPE_LUN))
    if parent_type == TYPE_LUN:
        if not state.profile.supports_block:
            raise E.HuaweiError(E.NOT_SUPPORTED, "LUN snapshots are not supported on this device.")
        parent = state.get("lun", body["PARENTID"])
    elif parent_type == TYPE_FS:
        parent = state.get("filesystem", body["PARENTID"])
    else:
        raise E.HuaweiError(E.PARAM_ERROR, "Unsupported PARENTTYPE.")
    state.ensure_unique_name("snapshot", body["NAME"])
    snap = state.create(
        "snapshot",
        NAME=body["NAME"],
        PARENTID=parent["ID"],
        PARENTTYPE=parent_type,
        PARENTNAME=parent["NAME"],
        TIMESTAMP=str(int(state.now())),
        USERCAPACITY=parent["CAPACITY"],
        HEALTHSTATUS="1",
        RUNNINGSTATUS="43",
    )
    return E.ok(snap)


@router.get("/snapshot")
async def list_snapshots(request: Request, filter: str | None = None, range: str | None = None):
    state = require_session(request)
    return E.ok(select(list(state.objects["snapshot"].values()), filter, range))


@router.delete("/snapshot/{snapshot_id}")
async def delete_snapshot(request: Request, snapshot_id: str):
    state = require_session(request)
    state.delete("snapshot", snapshot_id)
    return E.ok()
