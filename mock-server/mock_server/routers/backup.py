"""Backup policies, protected assets, backup jobs and the asynchronous task list.

Starting a backup returns a ``taskId``; poll ``GET /task_list/{taskId}`` until the status is final.
Paths and field names are simulation choices that follow the style of the real API.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from .. import envelope as E
from ..deps import require_session, select
from ..profiles import BACKUP_TYPES

router = APIRouter(prefix="/deviceManager/rest/{device_id}")


# ---- policies --------------------------------------------------------------
def _policy_fields(body: dict[str, Any]) -> dict[str, Any]:
    btype = int(body.get("BACKUPTYPE", 1))
    days = int(body.get("RETENTIONDAYS", 0))
    if btype not in BACKUP_TYPES or days < 1:
        raise E.HuaweiError(
            E.PARAM_ERROR, "BACKUPTYPE must be 1 (full) or 2 (incremental); RETENTIONDAYS >= 1."
        )
    return {
        "CRON": str(body["CRON"]),
        "BACKUPTYPE": btype,
        "BACKUPTYPENAME": BACKUP_TYPES[btype],
        "RETENTIONDAYS": days,
        "WORMENABLED": bool(body.get("WORMENABLED", False)),
    }


@router.post("/backup_policy")
async def create_policy(request: Request, body: dict[str, Any] = Body(...)):
    state = require_session(request)
    E.require(body, "NAME", "CRON", "RETENTIONDAYS")
    state.ensure_unique_name("policy", body["NAME"])
    return E.ok(state.create("policy", NAME=body["NAME"], **_policy_fields(body)))


@router.get("/backup_policy")
async def list_policies(request: Request, filter: str | None = None, range: str | None = None):
    state = require_session(request)
    return E.ok(select(list(state.objects["policy"].values()), filter, range))


@router.put("/backup_policy/{policy_id}")
async def update_policy(request: Request, policy_id: str, body: dict[str, Any] = Body(...)):
    state = require_session(request)
    policy = state.get("policy", policy_id)
    merged = {**policy, **body}
    policy.update(_policy_fields(merged))
    return E.ok(policy)


@router.delete("/backup_policy/{policy_id}")
async def delete_policy(request: Request, policy_id: str):
    state = require_session(request)
    if any(j["POLICYID"] == policy_id for j in state.objects["job"].values()):
        raise E.HuaweiError(E.OBJECT_IN_USE, "The policy is used by a backup job.")
    state.delete("policy", policy_id)
    return E.ok()


# ---- assets ------------------------------------------------------------------
ASSET_TYPES = ("VMware", "Database", "FileShare", "LUN")


@router.post("/asset")
async def create_asset(request: Request, body: dict[str, Any] = Body(...)):
    state = require_session(request)
    E.require(body, "NAME", "TYPE")
    if body["TYPE"] not in ASSET_TYPES:
        raise E.HuaweiError(E.PARAM_ERROR, f"TYPE must be one of {', '.join(ASSET_TYPES)}.")
    state.ensure_unique_name("asset", body["NAME"])
    return E.ok(
        state.create(
            "asset",
            NAME=body["NAME"],
            TYPE=body["TYPE"],
            SOURCEIP=body.get("SOURCEIP", ""),
            AGENTVERSION=body.get("AGENTVERSION", "1.6.0"),
        )
    )


@router.get("/asset")
async def list_assets(request: Request, filter: str | None = None, range: str | None = None):
    state = require_session(request)
    return E.ok(select(list(state.objects["asset"].values()), filter, range))


@router.delete("/asset/{asset_id}")
async def delete_asset(request: Request, asset_id: str):
    state = require_session(request)
    asset = state.get("asset", asset_id)
    if any(
        j["ASSETID"] == asset_id and state.task_view(j["taskId"])["STATUS"] in ("PENDING", "RUNNING")
        for j in state.objects["job"].values()
    ):
        raise E.HuaweiError(E.OBJECT_IN_USE, "The asset has a running backup.")
    state.delete("asset", asset["ID"])
    return E.ok()


# ---- jobs and tasks --------------------------------------------------------------
@router.post("/backup_job")
async def start_backup(request: Request, body: dict[str, Any] = Body(...)):
    """Trigger a backup. ``SIMULATE_OUTCOME`` (simulation only) pins the final status."""
    state = require_session(request)
    E.require(body, "ASSETID")
    asset = state.get("asset", body["ASSETID"])
    policy = state.get("policy", body["POLICYID"]) if body.get("POLICYID") else None
    btype = int(body.get("BACKUPTYPE", policy["BACKUPTYPE"] if policy else 1))
    if btype not in BACKUP_TYPES:
        raise E.HuaweiError(E.PARAM_ERROR, "BACKUPTYPE must be 1 (full) or 2 (incremental).")
    outcome = body.get("SIMULATE_OUTCOME")
    if outcome not in (None, "SUCCESS", "FAILED", "PARTIALLY_SUCCESSFUL"):
        raise E.HuaweiError(E.PARAM_ERROR, "Unknown SIMULATE_OUTCOME.")
    size_gb = float(
        body.get("SIZEGB") or (state.rng.randint(200, 900) if btype == 1 else state.rng.randint(10, 80))
    )
    state.check_space(int(size_gb * 1024**3))
    job = state.create(
        "job",
        ASSETID=asset["ID"],
        ASSETNAME=asset["NAME"],
        POLICYID=policy["ID"] if policy else "",
        BACKUPTYPE=btype,
        BACKUPTYPENAME=BACKUP_TYPES[btype],
    )
    task = state.start_task(job, size_gb, outcome)
    job["taskId"] = task["taskId"]
    return E.ok({"ID": job["ID"], "taskId": task["taskId"]})


@router.get("/backup_job")
async def list_jobs(request: Request, filter: str | None = None, range: str | None = None):
    state = require_session(request)
    return E.ok(select(list(state.objects["job"].values()), filter, range))


@router.get("/task_list")
async def list_tasks(request: Request):
    state = require_session(request)
    return E.ok([state.task_view(t) for t in state.tasks])


@router.get("/task_list/{task_id}")
async def get_task(request: Request, task_id: str):
    state = require_session(request)
    return E.ok(state.task_view(task_id))


@router.put("/task_list/{task_id}/cancel")
async def cancel_task(request: Request, task_id: str):
    state = require_session(request)
    return E.ok(state.cancel_task(task_id))
