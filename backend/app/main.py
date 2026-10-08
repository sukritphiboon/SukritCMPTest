from fastapi import FastAPI

from app.api import errors
from app.api.routers import audit, buckets, devices, filesystems, policies, tenants, volumes


def create_app() -> FastAPI:
    app = FastAPI(title="CMP Backend", version="0.2.0")
    errors.install(app)
    for module in (devices, tenants, volumes, filesystems, buckets, policies, audit):
        app.include_router(module.router, prefix="/api/v1")

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
