"""System info, storage pools, alarms and performance statistics."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import envelope as E
from ..deps import require_session, select
from ..profiles import SECTORS_PER_GB  # noqa: F401  (documented unit)

router = APIRouter(prefix="/deviceManager/rest/{device_id}")


def _pool_view(state) -> dict:
    pool = state.pool
    total = pool["total_bytes"]
    consumed = state.consumed_bytes()
    reduction = state.total_reduction_ratio()
    return {
        "ID": pool["ID"],
        "NAME": pool["NAME"],
        "USAGETYPE": pool["USAGETYPE"],
        "HEALTHSTATUS": pool["HEALTHSTATUS"],
        "RUNNINGSTATUS": pool["RUNNINGSTATUS"],
        "USERTOTALCAPACITY": str(total // 512),
        "USERCONSUMEDCAPACITY": str(consumed // 512),
        "USERFREECAPACITY": str(total // 512 - consumed // 512),
        "PROVISIONEDCAPACITY": str(state.provisioned_bytes() // 512),
        "SMARTTHIN_RATIO": str(pool["thin"]),
        "SMARTDEDUPE_RATIO": str(pool["dedupe"]),
        "SMARTCOMPRESSION_RATIO": str(pool["compression"]),
        "DATAREDUCTION_RATIO": str(reduction),
    }


@router.get("/system/")
async def system_info(request: Request):
    state = require_session(request)
    p = state.profile
    return E.ok(
        {
            "ID": state.device_id,
            "NAME": f"{p.key}-mock",
            "PRODUCTMODE": p.product_mode,
            "PRODUCTVERSION": p.product_version,
            "HEALTHSTATUS": "1",
            "RUNNINGSTATUS": "1",
            "SN": f"2102{state.device_id}",
        }
    )


@router.get("/storagepool")
async def list_pools(request: Request, filter: str | None = None, range: str | None = None):
    state = require_session(request)
    return E.ok(select([_pool_view(state)], filter, range))


@router.get("/alarm/currentalarm")
async def current_alarms(request: Request, range: str | None = None):
    state = require_session(request)
    return E.ok(select(state.alarms, None, range))


@router.get("/performance_statistic/cur_statistic_data")
async def performance(request: Request):
    state = require_session(request)
    return E.ok(state.sample_performance())
