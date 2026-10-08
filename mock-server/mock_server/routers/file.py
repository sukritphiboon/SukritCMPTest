"""File system, NFS/CIFS share and quota endpoints (both products)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from .. import envelope as E
from ..deps import require_session, select

router = APIRouter(prefix="/deviceManager/rest/{device_id}")

TYPE_FS = 40


@router.post("/filesystem")
async def create_filesystem(request: Request, body: dict[str, Any] = Body(...)):
    state = require_session(request)
    E.require(body, "NAME", "CAPACITY")
    state.ensure_unique_name("filesystem", body["NAME"])
    capacity = int(body["CAPACITY"])
    if capacity <= 0:
        raise E.HuaweiError(E.PARAM_ERROR, "CAPACITY must be positive.")
    state.check_space(capacity * 512)
    fs = state.create(
        "filesystem",
        NAME=body["NAME"],
        CAPACITY=str(capacity),
        ALLOCTYPE=str(body.get("ALLOCTYPE", "1")),
        PARENTID=str(body.get("PARENTID", state.pool["ID"])),
        PARENTNAME=state.pool["NAME"],
        DESCRIPTION=body.get("DESCRIPTION", ""),
        WORMTYPE="0",
        HEALTHSTATUS="1",
        RUNNINGSTATUS="27",
    )
    return E.ok(fs)


@router.get("/filesystem")
async def list_filesystems(request: Request, filter: str | None = None, range: str | None = None):
    state = require_session(request)
    return E.ok(select(list(state.objects["filesystem"].values()), filter, range))


@router.get("/filesystem/{fs_id}")
async def get_filesystem(request: Request, fs_id: str):
    state = require_session(request)
    return E.ok(state.get("filesystem", fs_id))


@router.delete("/filesystem/{fs_id}")
async def delete_filesystem(request: Request, fs_id: str):
    state = require_session(request)
    fs = state.get("filesystem", fs_id)
    if fs["WORMTYPE"] != "0":
        raise E.HuaweiError(E.WORM_LOCKED, "A WORM file system cannot be deleted.")
    for kind, key in (
        ("nfshare", "FSID"),
        ("cifshare", "FSID"),
        ("fsquota", "PARENTID"),
        ("snapshot", "PARENTID"),
    ):
        for oid in [o["ID"] for o in state.objects[kind].values() if o.get(key) == fs_id]:
            state.delete(kind, oid)
    state.delete("filesystem", fs_id)
    return E.ok()


@router.post("/NFSHARE")
async def create_nfs_share(request: Request, body: dict[str, Any] = Body(...)):
    state = require_session(request)
    E.require(body, "FSID")
    fs = state.get("filesystem", body["FSID"])
    path = body.get("SHAREPATH") or f"/{fs['NAME']}/"
    if any(s["SHAREPATH"] == path for s in state.objects["nfshare"].values()):
        raise E.HuaweiError(E.OBJECT_EXISTS, "The share path already exists.")
    share = state.create(
        "nfshare", FSID=fs["ID"], SHAREPATH=path, DESCRIPTION=body.get("DESCRIPTION", ""), TYPE=16401
    )
    return E.ok(share)


@router.get("/NFSHARE")
async def list_nfs_shares(request: Request, filter: str | None = None, range: str | None = None):
    state = require_session(request)
    return E.ok(select(list(state.objects["nfshare"].values()), filter, range))


@router.post("/CIFSHARE")
async def create_cifs_share(request: Request, body: dict[str, Any] = Body(...)):
    state = require_session(request)
    E.require(body, "FSID", "NAME")
    fs = state.get("filesystem", body["FSID"])
    state.ensure_unique_name("cifshare", body["NAME"])
    share = state.create(
        "cifshare",
        FSID=fs["ID"],
        NAME=body["NAME"],
        SHAREPATH=body.get("SHAREPATH") or f"/{fs['NAME']}/",
        DESCRIPTION=body.get("DESCRIPTION", ""),
        TYPE=16402,
    )
    return E.ok(share)


@router.get("/CIFSHARE")
async def list_cifs_shares(request: Request, filter: str | None = None, range: str | None = None):
    state = require_session(request)
    return E.ok(select(list(state.objects["cifshare"].values()), filter, range))


@router.post("/FS_QUOTA")
async def create_quota(request: Request, body: dict[str, Any] = Body(...)):
    """Quota values are in bytes (simulation choice)."""
    state = require_session(request)
    E.require(body, "PARENTID", "SPACEHARDQUOTA")
    fs = state.get("filesystem", body["PARENTID"])
    hard = int(body["SPACEHARDQUOTA"])
    soft = int(body.get("SPACESOFTQUOTA", int(hard * 0.8)))
    if hard <= 0 or soft > hard:
        raise E.HuaweiError(E.PARAM_ERROR, "Soft quota must not exceed the hard quota.")
    quota = state.create(
        "fsquota",
        PARENTID=fs["ID"],
        PARENTTYPE=TYPE_FS,
        QUOTATYPE=str(body.get("QUOTATYPE", "1")),
        SPACEHARDQUOTA=str(hard),
        SPACESOFTQUOTA=str(soft),
        SPACEUSED="0",
    )
    return E.ok(quota)


@router.get("/FS_QUOTA")
async def list_quotas(request: Request, filter: str | None = None, range: str | None = None):
    state = require_session(request)
    return E.ok(select(list(state.objects["fsquota"].values()), filter, range))
