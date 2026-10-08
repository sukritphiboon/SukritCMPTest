"""Minimal file system endpoints: a WORM policy is attached to a file system."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from .. import envelope as E
from ..deps import require_session, select

router = APIRouter(prefix="/deviceManager/rest/{device_id}")


@router.post("/filesystem")
async def create_filesystem(request: Request, body: dict[str, Any] = Body(...)):
    state = require_session(request)
    E.require(body, "NAME", "CAPACITY")
    state.ensure_unique_name("filesystem", body["NAME"])
    capacity = int(body["CAPACITY"])  # 512-byte sectors
    if capacity <= 0:
        raise E.HuaweiError(E.PARAM_ERROR, "CAPACITY must be positive.")
    state.check_space(capacity * 512)
    fs = state.create(
        "filesystem",
        NAME=body["NAME"],
        CAPACITY=str(capacity),
        PARENTID="0",
        PARENTNAME="StoragePool001",
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
    return E.ok(require_session(request).get("filesystem", fs_id))


@router.delete("/filesystem/{fs_id}")
async def delete_filesystem(request: Request, fs_id: str):
    state = require_session(request)
    fs = state.get("filesystem", fs_id)
    if fs["WORMTYPE"] != "0":
        raise E.HuaweiError(E.WORM_LOCKED, "A WORM file system cannot be deleted.")
    state.delete("filesystem", fs_id)
    return E.ok()
