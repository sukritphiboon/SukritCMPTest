"""System info, storage pool, alarms, performance and hardware status."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import envelope as E
from ..deps import require_session, select
from ..profiles import SECTOR

router = APIRouter(prefix="/deviceManager/rest/{device_id}")


def _sectors(n: int) -> str:
    return str(n // SECTOR)


def _rate(numerator: float, denominator: float) -> str:
    """Ratios are JSON strings in the real API: numerator / denominator is the N in 'N:1'."""
    if denominator <= 0:
        return '{"numerator":"10", "denominator":"10","logic":"="}'
    return f'{{"numerator":"{round(numerator / denominator * 100)}", "denominator":"100","logic":"="}}'


def pool_view(state) -> dict:
    """Pool fields use the names of the reference: capacities in sectors, ratios as JSON strings.

    Reduction: DEDUPINVOLVEDCAPACITY is the data that went into deduplication (ingested), DEDUPEDCAPACITY what
    deduplication saved, COMPRESSINVOLVEDCAPACITY the data that went into compression and
    COMPRESSEDCAPACITY what compression saved; USERCONSUMEDCAPACITY is what is left on disk.
    """
    n = state.pool_numbers()
    saved_by_dedup = n["ingested"] - n["post_dedup"]
    saved_by_compression = n["post_dedup"] - n["physical"]
    return {
        "ID": "0",
        "NAME": "StoragePool001",
        "TYPE": 216,
        "USAGETYPE": "1",
        "NEWUSAGETYPE": 0,
        "HEALTHSTATUS": "1",
        "RUNNINGSTATUS": "27",
        "PARENTID": "0",
        "PARENTTYPE": 266,
        "PARENTNAME": "StoragePool001",
        "USERTOTALCAPACITY": _sectors(n["raw"]),
        "USERCONSUMEDCAPACITY": _sectors(n["physical"]),
        "USERFREECAPACITY": _sectors(n["raw"] - n["physical"]),
        "USERCONSUMEDCAPACITYPERCENTAGE": str(int(n["physical"] * 100 / n["raw"])),
        "DATASPACE": _sectors(n["raw"] - n["physical"]),
        "DEDUPINVOLVEDCAPACITY": _sectors(n["ingested"]),
        "DEDUPEDCAPACITY": _sectors(saved_by_dedup),
        "COMPRESSINVOLVEDCAPACITY": _sectors(n["post_dedup"]),
        "COMPRESSEDCAPACITY": _sectors(saved_by_compression),
        "REDUCTIONINVOLVEDCAPACITY": _sectors(n["ingested"]),
        "DEDUPLICATIONRATE": _rate(n["ingested"], n["post_dedup"]),
        "COMPRESSIONRATE": _rate(n["post_dedup"], n["physical"]),
        "SPACEREDUCTIONRATE": _rate(n["ingested"], n["physical"]),
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
async def current_alarms(request: Request, filter: str | None = None, range: str | None = None):
    """Newest first (the reference sorts by ``startTime,d``); filter on level, startTime, sequence ..."""
    state = require_session(request)
    newest_first = sorted(state.alarms, key=lambda a: (a["startTime"], a["sequence"]), reverse=True)
    return E.ok(select(newest_first, filter, range))


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
