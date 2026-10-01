"""FastAPI app. Thin by design: it wires core services and the enabled modules."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from confluo_api import audit, members, system, tenants, webhooks
from confluo_api import modules as modules_api
from confluo_core.auth import TokenVerifier
from confluo_core.auth_admin import AuthAdmin, SupabaseAuthAdmin
from confluo_core.db import create_pool, ping
from confluo_core.deps import REQUIRED_PERMISSIONS, module_enabled
from confluo_core.job_app import build_job_app
from confluo_core.logging import configure_logging
from confluo_core.modules import discover_modules
from confluo_core.permissions import CORE_PERMISSIONS, PermissionRegistry
from confluo_core.settings import Settings, get_settings
from confluo_core.webhooks import WebhookProvider, default_providers


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool


def create_app(
    settings: Settings | None = None,
    token_verifier: TokenVerifier | None = None,
    auth_admin: AuthAdmin | None = None,
    webhook_providers: dict[str, WebhookProvider] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    modules = discover_modules()
    permissions = PermissionRegistry(
        [*CORE_PERMISSIONS, *(p for m in modules.values() for p in m.permissions)]
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = create_pool(settings)
        await pool.open(wait=False)
        app.state.pool = pool
        # Procrastinate shares the pool; the API only defers and retries jobs.
        await app.state.job_app.open_async(pool)
        try:
            yield
        finally:
            await app.state.job_app.close_async()
            await pool.close()

    app = FastAPI(title="Confluo API", version="0.1.0", lifespan=lifespan)
    app.state.modules = modules
    app.state.token_verifier = token_verifier or TokenVerifier(settings)
    app.state.auth_admin = auth_admin or SupabaseAuthAdmin(settings)
    app.state.permissions = permissions
    app.state.job_app = build_job_app(modules)
    app.state.webhook_providers = (
        default_providers(settings) if webhook_providers is None else webhook_providers
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["system"], operation_id="health")
    async def health(request: Request) -> Health:
        db_ok = await ping(request.app.state.pool)
        return Health(status="ok" if db_ok else "degraded", database=db_ok)

    app.include_router(tenants.router)
    app.include_router(members.router)
    app.include_router(modules_api.router)
    app.include_router(audit.router)
    app.include_router(system.router)
    app.include_router(webhooks.router)

    for module in modules.values():
        for router in module.routers():
            # Module endpoints are staff-only and tenant-scoped, and 404 when the
            # tenant has the module off. Public ones (webhooks, widget) will get their
            # own routers with provider-specific verification.
            app.include_router(
                router,
                prefix=f"/api/{module.key}",
                dependencies=[Depends(module_enabled(module.key))],
            )

    unknown = sorted(p for p in REQUIRED_PERMISSIONS if p not in permissions)
    if unknown:
        raise RuntimeError(f"routes require undeclared permissions: {unknown}")
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
