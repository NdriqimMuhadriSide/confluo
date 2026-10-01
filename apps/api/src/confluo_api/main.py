"""FastAPI app. Thin by design: it wires core services and the enabled modules."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from confluo_core.db import create_pool, ping
from confluo_core.logging import configure_logging
from confluo_core.modules import ConfluoModule, discover_modules
from confluo_core.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    modules = discover_modules()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = create_pool(settings)
        await pool.open(wait=False)
        app.state.pool = pool
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="Confluo API", version="0.1.0", lifespan=lifespan)
    app.state.modules = modules
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["system"])
    async def health(request: Request) -> dict[str, Any]:
        db_ok = await ping(request.app.state.pool)
        return {"status": "ok" if db_ok else "degraded", "database": db_ok}

    @app.get("/api/me/manifest", tags=["system"])
    async def manifest(request: Request) -> dict[str, Any]:
        # Per-tenant enablement arrives with the module registry card; for now every
        # installed module is listed.
        mods: dict[str, ConfluoModule] = request.app.state.modules
        return {
            "modules": [
                {"key": m.key, "version": m.version, **asdict(m.dashboard_manifest())}
                for m in mods.values()
            ]
        }

    for module in modules.values():
        for router in module.routers():
            app.include_router(router, prefix=f"/api/{module.key}")

    return app


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "confluo_api.main:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.env == "local",
    )
