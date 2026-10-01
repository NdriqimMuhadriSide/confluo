"""Tenant A cannot read or write tenant B's data, enforced by Postgres RLS.

Everything here connects as `confluo_app`, the role the API and worker use.
"""

import uuid

import psycopg
import pytest
from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from confluo_core.tenancy import (
    NotAMember,
    require_membership,
    tenant_transaction,
    user_transaction,
)
from tests.tenancy.conftest import World

pytestmark = pytest.mark.usefixtures("world")


async def _location_ids(conn: psycopg.AsyncConnection) -> set[uuid.UUID]:
    cur = await conn.execute("select id from location")
    return {r[0] for r in await cur.fetchall()}


async def _visible_tenants(conn: psycopg.AsyncConnection) -> set[uuid.UUID]:
    """Tenant ids of all location rows this transaction can see."""
    cur = await conn.execute("select distinct tenant_id from location")
    return {r[0] for r in await cur.fetchall()}


# --- The guarantees the rest relies on ------------------------------------------


def test_app_role_cannot_bypass_rls(world: World) -> None:
    with psycopg.connect(world.owner_url) as conn:
        row = conn.execute(
            "select rolsuper, rolbypassrls from pg_roles where rolname = 'confluo_app'"
        ).fetchone()
        owned = conn.execute(
            "select count(*) from pg_class c join pg_roles r on r.oid = c.relowner"
            " where r.rolname = 'confluo_app'"
        ).fetchone()
    assert row == (False, False)
    assert owned == (0,)


def test_rls_enabled_on_every_table(world: World) -> None:
    """Every table in public has RLS on and at least one policy. New tables that
    forget it fail here, which is the point."""
    with psycopg.connect(world.owner_url) as conn:
        rows = conn.execute("""
            select c.relname, c.relrowsecurity,
                   (select count(*) from pg_policy p where p.polrelid = c.oid)
            from pg_class c join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind = 'r' and c.relname <> 'alembic_version'
        """).fetchall()
    assert rows, "no tables found"
    missing = [name for name, rls, policies in rows if not rls or policies == 0]
    assert missing == []


# Tables written before the tenant is known (webhooks arrive unassigned).
NULLABLE_TENANT = {"inbound_event"}


def test_every_tenant_id_column_is_not_null(world: World) -> None:
    with psycopg.connect(world.owner_url) as conn:
        rows = conn.execute("""
            select table_name, is_nullable from information_schema.columns
            where table_schema = 'public' and column_name = 'tenant_id'
        """).fetchall()
    assert rows
    nullable = {t for t, n in rows if n == "YES"}
    assert nullable == NULLABLE_TENANT


# --- Reads ------------------------------------------------------------------------


async def test_without_tenant_context_nothing_is_visible(pool: AsyncConnectionPool) -> None:
    async with pool.connection() as conn, conn.transaction():
        assert await _location_ids(conn) == set()
        cur = await conn.execute("select count(*) from tenant_member")
        assert await cur.fetchone() == (0,)


async def test_tenant_sees_only_its_own_rows(pool: AsyncConnectionPool, world: World) -> None:
    # Other tests add rows too, so check ownership of everything visible.
    async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
        assert await _visible_tenants(conn) == {world.tenant_a}
        assert world.location_a in await _location_ids(conn)
    async with tenant_transaction(pool, world.tenant_b, world.user_b) as conn:
        assert await _visible_tenants(conn) == {world.tenant_b}
        assert world.location_b in await _location_ids(conn)


async def test_cannot_read_other_tenant_row_by_id(pool: AsyncConnectionPool, world: World) -> None:
    async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
        cur = await conn.execute("select * from location where id = %s", (world.location_b,))
        assert await cur.fetchall() == []


async def test_members_and_tenants_of_other_tenant_are_hidden(
    pool: AsyncConnectionPool, world: World
) -> None:
    async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
        cur = await conn.execute("select tenant_id, user_id from tenant_member")
        assert await cur.fetchall() == [(world.tenant_a, world.user_a)]
        cur = await conn.execute("select id from tenant")
        assert await cur.fetchall() == [(world.tenant_a,)]
        cur = await conn.execute("select id from app_user")
        assert await cur.fetchall() == [(world.user_a,)]


# --- Writes -----------------------------------------------------------------------


async def test_cannot_update_or_delete_other_tenant_rows(
    pool: AsyncConnectionPool, world: World
) -> None:
    async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
        cur = await conn.execute(
            "update location set name = 'hacked' where id = %s", (world.location_b,)
        )
        assert cur.rowcount == 0
        cur = await conn.execute("delete from location where id = %s", (world.location_b,))
        assert cur.rowcount == 0
        cur = await conn.execute(
            "update tenant set name = 'hacked' where id = %s", (world.tenant_b,)
        )
        assert cur.rowcount == 0
    with psycopg.connect(world.owner_url) as owner:
        assert owner.execute(
            "select name from location where id = %s", (world.location_b,)
        ).fetchone() == ("B Station road",)
        assert owner.execute(
            "select name from tenant where id = %s", (world.tenant_b,)
        ).fetchone() == ("Salon B",)


async def test_cannot_insert_into_other_tenant(pool: AsyncConnectionPool, world: World) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
            await conn.execute(
                "insert into location (tenant_id, name) values (%s, 'sneaky')", (world.tenant_b,)
            )


async def test_cannot_move_row_to_other_tenant(pool: AsyncConnectionPool, world: World) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
            await conn.execute(
                "update location set tenant_id = %s where id = %s",
                (world.tenant_b, world.location_a),
            )


async def test_cannot_add_self_to_other_tenant(pool: AsyncConnectionPool, world: World) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
            await conn.execute(
                "insert into tenant_member (tenant_id, user_id, role) values (%s, %s, 'owner')",
                (world.tenant_b, world.user_a),
            )


async def test_insert_without_tenant_context_is_rejected(pool: AsyncConnectionPool) -> None:
    with pytest.raises((errors.InsufficientPrivilege, errors.NotNullViolation)):
        async with pool.connection() as conn, conn.transaction():
            await conn.execute("insert into location (name) values ('orphan')")


async def test_app_role_cannot_create_tenants_directly(pool: AsyncConnectionPool) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        async with pool.connection() as conn, conn.transaction():
            await conn.execute("insert into tenant (name, slug) values ('x', 'xx-direct')")


# --- Context handling ---------------------------------------------------------------


async def test_membership_check_refuses_other_tenant(
    pool: AsyncConnectionPool, world: World
) -> None:
    async with user_transaction(pool, world.user_a) as conn:
        assert await require_membership(conn, world.tenant_a, world.user_a) == "owner"
    with pytest.raises(NotAMember):
        async with user_transaction(pool, world.user_a) as conn:
            await require_membership(conn, world.tenant_b, world.user_a)
    with pytest.raises(NotAMember):
        async with user_transaction(pool, world.user_a) as conn:
            await require_membership(conn, uuid.uuid4(), world.user_a)


async def test_context_does_not_leak_to_next_transaction(
    pool: AsyncConnectionPool, world: World
) -> None:
    async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
        assert await _visible_tenants(conn) == {world.tenant_a}
    # Same pool of size 1, so the same physical connection.
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute("select current_setting('app.tenant_id', true)")
        assert await cur.fetchone() in (("",), (None,))
        assert await _location_ids(conn) == set()


async def test_worker_job_runs_scoped_to_its_tenant(
    pool: AsyncConnectionPool, world: World
) -> None:
    # Worker jobs have no user, only the tenant from the job payload.
    async with tenant_transaction(pool, world.tenant_b) as conn:
        assert await _visible_tenants(conn) == {world.tenant_b}
        await conn.execute("insert into location (name) values ('Added by a job')")
        cur = await conn.execute("select tenant_id from location where name = 'Added by a job'")
        assert await cur.fetchall() == [(world.tenant_b,)]
    async with tenant_transaction(pool, world.tenant_a) as conn:
        cur = await conn.execute("select count(*) from location where name = 'Added by a job'")
        assert await cur.fetchone() == (0,)


async def test_create_tenant_makes_caller_owner(pool: AsyncConnectionPool, world: World) -> None:
    async with user_transaction(pool, world.user_a) as conn:
        cur = await conn.execute(
            "select app.create_tenant('Salon A2', %s)", (f"a2-{uuid.uuid4().hex[:8]}",)
        )
        row = await cur.fetchone()
        assert row is not None
        assert await require_membership(conn, row[0], world.user_a) == "owner"


async def test_create_tenant_requires_a_user(pool: AsyncConnectionPool) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        async with pool.connection() as conn, conn.transaction():
            await conn.execute("select app.create_tenant('Nobody', 'nobody-co')")
