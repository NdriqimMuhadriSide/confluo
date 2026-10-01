"""FastAPI dependencies for tenant-scoped endpoints, shared by the API and modules."""

import ipaddress
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from confluo_core.auth import AuthUser, CurrentUser
from confluo_core.llm import LLMGateway
from confluo_core.permissions import PermissionRegistry
from confluo_core.tenancy import NotAMember, require_membership, user_transaction

TENANT_HEADER = "X-Tenant-Id"


def get_pool(request: Request) -> AsyncConnectionPool:
    pool: AsyncConnectionPool = request.app.state.pool
    return pool


Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]


def get_llm(request: Request) -> LLMGateway:
    llm: LLMGateway = request.app.state.llm
    return llm


LLM = Annotated[LLMGateway, Depends(get_llm)]


def get_permissions(request: Request) -> PermissionRegistry:
    registry: PermissionRegistry = request.app.state.permissions
    return registry


Permissions = Annotated[PermissionRegistry, Depends(get_permissions)]


@dataclass(frozen=True)
class TenantContext:
    conn: AsyncConnection
    tenant_id: UUID
    user: AuthUser
    role: str
    permissions: frozenset[str]

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def client_ip(request: Request) -> str | None:
    """The caller's IP for the audit log, or None if the peer isn't an IP address
    (unix socket, test client). Behind a proxy, set uvicorn's --forwarded-allow-ips
    so request.client reflects X-Forwarded-For."""
    host = request.client.host if request.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None


async def tenant_context(
    request: Request,
    user: CurrentUser,
    pool: Pool,
    registry: Permissions,
    tenant_id: Annotated[UUID, Header(alias=TENANT_HEADER)],
) -> AsyncIterator[TenantContext]:
    """One transaction per request, scoped to the tenant in the X-Tenant-Id header.

    403 when the user isn't an active member of that tenant (or it doesn't exist).
    """
    async with user_transaction(pool, user.id, user.email, client_ip(request)) as conn:
        try:
            role = await require_membership(conn, tenant_id, user.id)
        except NotAMember:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this tenant") from None
        yield TenantContext(
            conn=conn,
            tenant_id=tenant_id,
            user=user,
            role=role,
            permissions=registry.for_role(role),
        )


Tenant = Annotated[TenantContext, Depends(tenant_context)]


# Every permission passed to requires(); create_app checks they all exist, so a typo
# fails at startup instead of silently refusing everyone.
REQUIRED_PERMISSIONS: set[str] = set()


def requires(
    permission: str,
) -> Callable[[TenantContext], Coroutine[Any, Any, TenantContext]]:
    """Dependency factory: the tenant context, or 403 without `permission`.

    @router.post("/x")
    async def x(tenant: Annotated[TenantContext, Depends(requires("crm.kb.edit"))]): ...
    """

    REQUIRED_PERMISSIONS.add(permission)

    async def check(tenant: Tenant) -> TenantContext:
        if not tenant.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission {permission}")
        return tenant

    return check


def module_enabled(module_key: str) -> Callable[..., Coroutine[Any, Any, TenantContext]]:
    """Dependency for a module's routes: the tenant context, or 404 when the tenant
    has the module switched off (to the tenant, a disabled module doesn't exist)."""

    async def check(tenant: Tenant, request: Request) -> TenantContext:
        from confluo_core.module_state import is_enabled

        module = request.app.state.modules[module_key]
        if not await is_enabled(tenant.conn, module):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
        return tenant

    return check
