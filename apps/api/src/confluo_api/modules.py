"""Module manifest for the dashboard and per-tenant module settings."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from confluo_core.deps import Tenant, TenantContext, requires
from confluo_core.module_state import ModuleState, ModuleStateError, load_states, save_state
from confluo_core.modules import ConfluoModule

router = APIRouter()

ManageModules = Annotated[TenantContext, Depends(requires("core.modules.manage"))]


class NavItemOut(BaseModel):
    module: str
    key: str
    label: str
    href: str


class Manifest(BaseModel):
    modules: list[str]
    nav: list[NavItemOut]
    permissions: list[str]


class ModuleOut(BaseModel):
    key: str
    version: str
    depends_on: list[str]
    enabled: bool
    config: dict[str, Any]
    config_schema: dict[str, Any]


class ModuleUpdate(BaseModel):
    enabled: bool | None = None
    config: dict[str, Any] | None = None


def _installed(request: Request) -> dict[str, ConfluoModule]:
    modules: dict[str, ConfluoModule] = request.app.state.modules
    return modules


def _out(state: ModuleState) -> ModuleOut:
    m = state.module
    return ModuleOut(
        key=m.key,
        version=m.version,
        depends_on=[d for d in m.depends_on if d != "core"],
        enabled=state.enabled,
        config=state.config.model_dump(mode="json"),
        config_schema=m.config_schema.model_json_schema(),
    )


@router.get("/api/me/manifest", tags=["modules"], operation_id="getManifest")
async def manifest(tenant: Tenant, request: Request) -> Manifest:
    """Enabled modules and the nav items the caller may see, in the current tenant."""
    states = await load_states(tenant.conn, _installed(request))
    enabled = [s for s in states.values() if s.enabled]
    return Manifest(
        modules=[s.module.key for s in enabled],
        nav=[
            NavItemOut(module=s.module.key, key=n.key, label=n.label, href=n.href)
            for s in enabled
            for n in s.module.dashboard_manifest().nav
            if n.permission is None or tenant.can(n.permission)
        ],
        permissions=sorted(tenant.permissions),
    )


@router.get("/api/modules", tags=["modules"], operation_id="listModules")
async def list_modules(tenant: ManageModules, request: Request) -> list[ModuleOut]:
    states = await load_states(tenant.conn, _installed(request))
    return [_out(s) for s in states.values()]


@router.put("/api/modules/{key}", tags=["modules"], operation_id="updateModule")
async def update_module(
    key: str, body: ModuleUpdate, tenant: ManageModules, request: Request
) -> ModuleOut:
    modules = _installed(request)
    if key not in modules:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such module")
    try:
        state = await save_state(
            tenant.conn, modules, key, enabled=body.enabled, config=body.config
        )
    except ValidationError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [{"loc": ["config", *err["loc"]], "msg": err["msg"]} for err in e.errors()],
        ) from None
    except ModuleStateError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    return _out(state)
