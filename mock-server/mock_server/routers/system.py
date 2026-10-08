"""System info, storage pool, alarms, performance and hardware status."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import envelope as E
from ..deps import require_session, select
from ..profiles import SECTOR

router = APIRouter(prefix="/deviceManager/rest/{device_id}")


def _sectors(n: int) -> str:
    return str(n // SECTOR)


def pool_view(state) -> dict:
    n = state.pool_numbers()
    dedup_fraction = (n["ingested"] - n["post_dedup"]) / n["ingested"] if n["ingested"] else 0.0
    return {
        "ID": "0",
        "NAME": "StoragePool001",
        "USAGETYPE": "1",
        "HEALTHSTATUS": "1",
        "RUNNINGSTATUS": "27",
        # capacities are counted in 512-byte sectors, like the real API
        "USERTOTALCAPACITY": _sectors(n["raw"]),
        "USERCONSUMEDCAPACITY": _sectors(n["physical"]),
        "USERFREECAPACITY": _sectors(n["raw"] - n["physical"]),
        "LOGICALWRITTENCAPACITY": _sectors(n["ingested"]),
        "POSTDEDUPCAPACITY": _sectors(n["post_dedup"]),
        "DEDUPRATIO": f"{dedup_fraction:.4f}",  # (ingested - post-dedup) / ingested
        "DEDUPFACTOR": str(state.dedupe_x),  # ingested : post-dedup
        "COMPRESSIONRATIO": str(state.compression_x),  # post-dedup : physical
        "DATAREDUCTION_RATIO": f"{n['ingested'] / n['physical']:.2f}" if n["physical"] else "0",
    }


@router.get("/system/")
async def system_info(request: Request):
    state = require_session(request)
    return E.ok(
        {
            "ID": state.device_id,
            "NAME": f"{state.model.split()[-1].lower()}-mock",
            "PRODUCTMODE": state.model,
            "PRODUCTVERSION": "1.6.0",
            "HEALTHSTATUS": "1",
            "RUNNINGSTATUS": "1",
            "SN": state.serial_number,
        }
    )


@router.get("/storagepool")
async def list_pools(request: Request, filter: str | None = None, range: str | None = None):
    state = require_session(request)
    return E.ok(select([pool_view(state)], filter, range))


@router.get("/alarm/currentalarm")
async def current_alarms(request: Request, range: str | None = None):
    state = require_session(request)
    return E.ok(select(state.alarms, None, range))


@router.get("/performancedata")
async def performance(request: Request):
    state = require_session(request)
    return E.ok(state.sample_performance())


def _hw(kind: str):
    async def handler(request: Request, filter: str | None = None):
        state = require_session(request)
        return E.ok(select(list(state.hardware[kind].values()), filter, None))

    return handler


for _kind in ("controller", "nvram", "power", "disk"):
    router.add_api_route(f"/{_kind}", _hw(_kind), methods=["GET"])
