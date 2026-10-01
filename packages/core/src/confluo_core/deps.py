"""FastAPI dependencies for tenant-scoped endpoints, shared by the API and modules."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from confluo_core.auth import AuthUser, CurrentUser
from confluo_core.tenancy import NotAMember, require_membership, user_transaction

TENANT_HEADER = "X-Tenant-Id"


def get_pool(request: Request) -> AsyncConnectionPool:
    pool: AsyncConnectionPool = request.app.state.pool
    return pool


Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]


@dataclass(frozen=True)
class TenantContext:
    conn: AsyncConnection
    tenant_id: UUID
    user: AuthUser
    role: str


async def tenant_context(
    user: CurrentUser,
    pool: Pool,
    tenant_id: Annotated[UUID, Header(alias=TENANT_HEADER)],
) -> AsyncIterator[TenantContext]:
    """One transaction per request, scoped to the tenant in the X-Tenant-Id header.

    403 when the user isn't an active member of that tenant (or it doesn't exist).
    """
    async with user_transaction(pool, user.id) as conn:
        try:
            role = await require_membership(conn, tenant_id, user.id)
        except NotAMember:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of this tenant") from None
        yield TenantContext(conn=conn, tenant_id=tenant_id, user=user, role=role)


Tenant = Annotated[TenantContext, Depends(tenant_context)]
