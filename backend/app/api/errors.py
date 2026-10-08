"""Translate driver / database errors into HTTP responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.core.crypto import CryptoError
from app.drivers import errors as e


def _status_for(exc: e.StorageDriverError) -> int:
    if isinstance(exc, e.ResourceNotFoundError):
        return 404
    if isinstance(
        exc, e.ResourceExistsError | e.ResourceBusyError | e.InsufficientSpaceError | e.ComplianceLockError
    ):
        return 409
    if isinstance(exc, e.NotSupportedError):
        return 422
    return 502  # unreachable appliance, bad appliance credentials, unknown appliance error


def install(app: FastAPI) -> None:
    @app.exception_handler(e.StorageDriverError)
    async def driver_error(_: Request, exc: e.StorageDriverError):
        body: dict = {"detail": str(exc)}
        if isinstance(exc, e.DeviceError):
            body["device_error_code"] = exc.code
        return JSONResponse(body, status_code=_status_for(exc))

    @app.exception_handler(IntegrityError)
    async def integrity_error(_: Request, exc: IntegrityError):
        return JSONResponse({"detail": "The request conflicts with existing data."}, status_code=409)

    @app.exception_handler(CryptoError)
    async def crypto_error(_: Request, exc: CryptoError):
        return JSONResponse({"detail": f"Credential encryption problem: {exc}"}, status_code=500)
