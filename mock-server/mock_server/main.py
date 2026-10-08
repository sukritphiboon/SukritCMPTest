"""Huawei storage mock server entry point.

Run: ``MOCK_PROFILE=dorado uvicorn mock_server.main:app --port 8088``
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import envelope as E
from .routers import block, file, protect, s3, session, system
from .state import MockState


def create_app(
    profile: str | None = None,
    seed: int | None = None,
    username: str | None = None,
    password: str | None = None,
) -> FastAPI:
    profile = profile or os.getenv("MOCK_PROFILE", "dorado")
    if seed is None and os.getenv("MOCK_SEED"):
        seed = int(os.environ["MOCK_SEED"])
    app = FastAPI(title=f"Huawei Storage Mock ({profile})", version="0.1.0")
    app.state.mock = MockState(
        profile,
        seed,
        username or os.getenv("MOCK_USERNAME", "admin"),
        password or os.getenv("MOCK_PASSWORD", "Admin@storage1"),
    )

    @app.exception_handler(E.HuaweiError)
    async def huawei_error(_: Request, exc: E.HuaweiError):
        return JSONResponse(E.fail(exc.code, exc.description))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(E.fail(E.PARAM_ERROR, "The entered parameter is incorrect."))

    for r in (session, system, block, file, protect, s3):
        app.include_router(r.router)

    # ---- simulation control (not part of the Huawei API) ----
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
        alarm = app.state.mock.add_alarm(
            body.get("level", "Major"), body.get("name", "Injected alarm"), body.get("eventID", "0xF00CF9999")
        )
        return alarm

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "profile": app.state.mock.profile.key}

    return app


app = create_app()
