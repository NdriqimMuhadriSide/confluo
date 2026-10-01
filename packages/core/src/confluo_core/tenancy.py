"""Tenant context for database work (ARCHITECTURE.md §1, "Tenancy").

Every query against tenant data runs inside a transaction that has set
`app.user_id` and/or `app.tenant_id` with `set_config(..., is_local => true)`, so
the values vanish at commit and can never leak to the next user of a pooled
connection. RLS policies in the database read them; the code here only decides
which values are allowed.

- API requests: `user_transaction` sets the user, `require_membership` checks the
  user belongs to the requested tenant, and only then is the tenant set.
- Worker jobs: `tenant_transaction(pool, tenant_id)` with no user — the job was
  enqueued by trusted code that already resolved the tenant.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool


class NotAMember(Exception):
    pass


async def _set(conn: AsyncConnection, name: str, value: UUID | None) -> None:
    await conn.execute(
        "select set_config(%s, %s, true)", (name, str(value) if value is not None else "")
    )


@asynccontextmanager
async def user_transaction(
    pool: AsyncConnectionPool, user_id: UUID, email: str | None = None
) -> AsyncIterator[AsyncConnection]:
    """A transaction acting as `user_id`, before any tenant is chosen.

    `email` must come from the verified token; invitations are matched on it.
    """
    async with pool.connection() as conn, conn.transaction():
        await _set(conn, "app.user_id", user_id)
        await conn.execute(
            "select set_config('app.user_email', %s, true)", ((email or "").lower(),)
        )
        yield conn


async def require_membership(conn: AsyncConnection, tenant_id: UUID, user_id: UUID) -> str:
    """Enter `tenant_id` on this transaction if `user_id` is an active member.

    Returns the member's role. Raises NotAMember otherwise (also when the tenant
    doesn't exist, so callers can't probe for tenant ids).
    """
    cur = await conn.execute(
        "select role from tenant_member"
        " where tenant_id = %s and user_id = %s and status = 'active'",
        (tenant_id, user_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotAMember(str(tenant_id))
    await _set(conn, "app.tenant_id", tenant_id)
    return str(row[0])


@asynccontextmanager
async def tenant_transaction(
    pool: AsyncConnectionPool, tenant_id: UUID, user_id: UUID | None = None
) -> AsyncIterator[AsyncConnection]:
    """A transaction scoped to `tenant_id`, for trusted callers such as worker jobs.

    No membership check: use it only where the tenant comes from our own data
    (a job payload, a webhook routed by channel connection), never from a request.
    """
    async with pool.connection() as conn, conn.transaction():
        await _set(conn, "app.user_id", user_id)
        await _set(conn, "app.tenant_id", tenant_id)
        yield conn
