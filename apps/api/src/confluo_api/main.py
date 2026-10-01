"""FastAPI app. Thin by design: it wires core services and the enabled modules."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from confluo_api import members, tenants
from confluo_core.auth import CurrentUser, TokenVerifier, current_user
from confluo_core.auth_admin import AuthAdmin, SupabaseAuthAdmin
from confluo_core.db import create_pool, ping
from confluo_core.deps import REQUIRED_PERMISSIONS
from confluo_core.logging import configure_logging
from confluo_core.modules import ConfluoModule, discover_modules
from confluo_core.permissions import CORE_PERMISSIONS, PermissionRegistry
from confluo_core.settings import Settings, get_settings


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool


class NavItemOut(BaseModel):
    key: str
    label: str
    href: str


class ModuleOut(BaseModel):
    key: str
    version: str
    nav: list[NavItemOut]


class Manifest(BaseModel):
    modules: list[ModuleOut]


def create_app(
    settings: Settings | None = None,
    token_verifier: TokenVerifier | None = None,
    auth_admin: AuthAdmin | None = None,
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
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(title="Confluo API", version="0.1.0", lifespan=lifespan)
    app.state.modules = modules
    app.state.token_verifier = token_verifier or TokenVerifier(settings)
    app.state.auth_admin = auth_admin or SupabaseAuthAdmin(settings)
    app.state.permissions = permissions
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

    @app.get("/api/me/manifest", tags=["system"], operation_id="getManifest")
    async def manifest(request: Request, user: CurrentUser) -> Manifest:
        # Per-tenant enablement arrives with the module registry card; for now every
        # installed module is listed.
        mods: dict[str, ConfluoModule] = request.app.state.modules
        return Manifest(
            modules=[
                ModuleOut(
                    key=m.key,
                    version=m.version,
                    nav=[
                        NavItemOut(key=n.key, label=n.label, href=n.href)
                        for n in m.dashboard_manifest().nav
                    ],
                )
                for m in mods.values()
            ]
        )

    app.include_router(tenants.router)
    app.include_router(members.router)

    for module in modules.values():
        for router in module.routers():
            # Module endpoints are staff-only; public ones (webhooks, widget) will get
            # their own routers with provider-specific verification.
            app.include_router(
                router, prefix=f"/api/{module.key}", dependencies=[Depends(current_user)]
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
