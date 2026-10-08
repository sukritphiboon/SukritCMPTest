"""Huawei OceanProtect mock server entry point.

Run: ``uvicorn mock_server.main:app --port 8088``
Environment: MOCK_SEED, MOCK_USERNAME, MOCK_PASSWORD, MOCK_MODEL, MOCK_RAW_TB,
MOCK_USED_PERCENT, MOCK_DAILY_GROWTH_GB.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import envelope as E
from .routers import backup, file, protect, session, system
from .state import MockState


def create_app(
    seed: int | None = None,
    username: str | None = None,
    password: str | None = None,
    **state_options: Any,
) -> FastAPI:
    if seed is None and os.getenv("MOCK_SEED"):
        seed = int(os.environ["MOCK_SEED"])

    def from_env(name: str, cast):
        raw = os.getenv(name)
        return cast(raw) if raw else None

    env_options = {
        "model": from_env("MOCK_MODEL", str),
        "raw_tb": from_env("MOCK_RAW_TB", int),
        "initial_used_percent": from_env("MOCK_USED_PERCENT", float),
        "daily_growth_gb": from_env("MOCK_DAILY_GROWTH_GB", float),
    }
    # 0 is a valid value (for example "no growth"), so only None means "not given"
    options = {k: v for k, v in {**env_options, **state_options}.items() if v is not None}
    app = FastAPI(title="Huawei OceanProtect Mock", version="0.2.0")
    app.state.mock = MockState(
        seed,
        username or os.getenv("MOCK_USERNAME", "admin"),
        password or os.getenv("MOCK_PASSWORD", "Admin@storage1"),
        **options,
    )

    @app.exception_handler(E.HuaweiError)
    async def huawei_error(_: Request, exc: E.HuaweiError):
        return JSONResponse(E.fail(exc.code, exc.description))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(E.fail(E.PARAM_ERROR, "The entered parameter is incorrect."))

    for r in (session, system, backup, file, protect):
        app.include_router(r.router)

    # ---- simulation controls (not part of the Huawei API; no authentication) ----
    @app.post("/_mock/reset")
    async def reset():
        app.state.mock.reset()
        return {"ok": True}

    @app.post("/_mock/advance_time")
    async def advance_time(body: dict[str, Any]):
        app.state.mock.advance_time(float(body.get("seconds", 0)))
        return {"ok": True}

    @app.post("/_mock/alarms")
    async def inject_alarm(body: dict[str, Any]):
        return app.state.mock.add_alarm(
            body.get("level", "Major"), body.get("name", "Injected alarm"), body.get("eventID", "0xF00CF9999")
        )

    @app.delete("/_mock/alarms/{sequence}")
    async def clear_alarm(sequence: str):
        app.state.mock.clear_alarm(sequence)
        return {"ok": True}

    @app.post("/_mock/ingest")
    async def ingest(body: dict[str, Any]):
        app.state.mock.ingest(float(body.get("logical_gb", 0)))
        return {"ok": True}

    @app.post("/_mock/performance")
    async def pin_performance(body: dict[str, Any]):
        """Pin write_mbps / read_mbps / iops / streams_per_controller; ``{}`` unpins."""
        app.state.mock.perf_override = {k: v for k, v in body.items() if v is not None}
        return {"ok": True}

    @app.post("/_mock/next_backup")
    async def next_backup(body: dict[str, Any]):
        """Decide the next backup task: outcome (SUCCESS|FAILED|PARTIALLY_SUCCESSFUL), size_gb, duration_s."""
        app.state.mock.next_backup = {
            k: v for k, v in body.items() if k in ("outcome", "size_gb", "duration_s")
        }
        return {"ok": True}

    @app.post("/_mock/lose_task")
    async def lose_task(body: dict[str, Any]):
        """Make the appliance forget a task id (it then answers 'not found')."""
        app.state.mock.task_lost.add(str(body["task_id"]))
        return {"ok": True}

    @app.post("/_mock/hardware_fault")
    async def hardware_fault(body: dict[str, Any]):
        item = app.state.mock.set_health(body["component"], str(body["id"]), body.get("health", "fault"))
        return item

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "model": app.state.mock.model}

    return app


app = create_app()
