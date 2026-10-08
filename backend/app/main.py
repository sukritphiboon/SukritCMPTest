from fastapi import FastAPI

from app.api import errors
from app.api.routers import audit, oceanprotect, targets


def create_app() -> FastAPI:
    app = FastAPI(title="OceanProtect CMP", version="0.3.0")
    errors.install(app)
    for module in (targets, oceanprotect, audit):
        app.include_router(module.router, prefix="/api/v1")

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
