"""OceanProtect data protection: WORM policies and backup copy retention.

The paths ``/worm_policy`` and ``/backup_retention`` are simulation choices.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from .. import envelope as E
from ..deps import require_session as require_protection
from ..deps import select

router = APIRouter(prefix="/deviceManager/rest/{device_id}")

DAY = 86400
MODE_COMPLIANCE, MODE_ENTERPRISE = 1, 2


def _copy_view(state, copy: dict[str, Any]) -> dict[str, Any]:
    expire = copy["CREATETIME"] + copy["RETENTIONDAYS"] * DAY
    expired = state.now() >= expire
    if expired:
        status = "expired"
    elif copy["WORM"]:
        status = "locked"
    else:
        status = "retained"
    return {
        "ID": copy["ID"],
        "NAME": copy["NAME"],
        "SOURCE": copy["SOURCE"],
        "CREATETIME": copy["CREATETIME"],
        "RETENTIONDAYS": copy["RETENTIONDAYS"],
        "EXPIRETIME": expire,
        "REMAININGDAYS": 0 if expired else int((expire - state.now()) // DAY) + 1,
        "WORM": copy["WORM"],
        "STATE": status,
        "SIZE": str(copy["size_bytes"]),
    }


@router.post("/worm_policy")
async def create_worm_policy(request: Request, body: dict[str, Any] = Body(...)):
    state = require_protection(request)
    E.require(body, "NAME", "FSID", "MODE", "PROTECTPERIOD")
    mode, period = int(body["MODE"]), int(body["PROTECTPERIOD"])
    if mode not in (MODE_COMPLIANCE, MODE_ENTERPRISE) or period < 1:
        raise E.HuaweiError(E.PARAM_ERROR, "MODE must be 1 or 2 and PROTECTPERIOD at least 1 day.")
    fs = state.get("filesystem", body["FSID"])
    if fs["WORMTYPE"] != "0":
        raise E.HuaweiError(E.OBJECT_EXISTS, "The file system already has a WORM policy.")
    state.ensure_unique_name("wormpolicy", body["NAME"])
    fs["WORMTYPE"] = str(mode)
    policy = state.create(
        "wormpolicy",
        NAME=body["NAME"],
        FSID=fs["ID"],
        MODE=str(mode),
        MODENAME="compliance" if mode == MODE_COMPLIANCE else "enterprise",
        PROTECTPERIOD=str(period),
        AUTOLOCK=bool(body.get("AUTOLOCK", True)),
    )
    return E.ok(policy)


@router.get("/worm_policy")
async def list_worm_policies(request: Request, filter: str | None = None, range: str | None = None):
    state = require_protection(request)
    return E.ok(select(list(state.objects["wormpolicy"].values()), filter, range))


@router.delete("/worm_policy/{policy_id}")
async def delete_worm_policy(request: Request, policy_id: str):
    state = require_protection(request)
    policy = state.get("wormpolicy", policy_id)
    if policy["MODE"] == str(MODE_COMPLIANCE):
        raise E.HuaweiError(E.WORM_LOCKED, "A compliance mode WORM policy cannot be removed.")
    state.get("filesystem", policy["FSID"])["WORMTYPE"] = "0"
    state.delete("wormpolicy", policy_id)
    return E.ok()


@router.get("/backup_retention")
async def list_backup_copies(request: Request, filter: str | None = None, range: str | None = None):
    state = require_protection(request)
    views = [_copy_view(state, c) for c in state.objects["backup"].values()]
    return E.ok(select(views, filter, range))


@router.post("/backup_retention")
async def create_backup_copy(request: Request, body: dict[str, Any] = Body(...)):
    state = require_protection(request)
    E.require(body, "NAME", "SOURCE", "RETENTIONDAYS")
    days = int(body["RETENTIONDAYS"])
    if days < 1:
        raise E.HuaweiError(E.PARAM_ERROR, "RETENTIONDAYS must be at least 1.")
    size = int(body.get("SIZE", 100 * 1024**3))
    state.check_space(size)
    copy = state.create(
        "backup",
        NAME=body["NAME"],
        SOURCE=body["SOURCE"],
        CREATETIME=int(state.now()),
        RETENTIONDAYS=days,
        WORM=bool(body.get("WORM", False)),
        size_bytes=size,
        CAPACITY=str(size // 512),
    )
    return E.ok(_copy_view(state, copy))


@router.delete("/backup_retention/{copy_id}")
async def delete_backup_copy(request: Request, copy_id: str):
    state = require_protection(request)
    view = _copy_view(state, state.get("backup", copy_id))
    if view["STATE"] in ("locked", "retained"):
        raise E.HuaweiError(
            E.WORM_LOCKED,
            "The backup copy is still within its retention period and cannot be deleted.",
        )
    state.delete("backup", copy_id)
    return E.ok()
