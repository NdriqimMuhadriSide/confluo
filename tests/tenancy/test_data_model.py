"""Data model guarantees: clean migrations, no cross-tenant references, no double
booking, vector search works."""

import uuid
from argparse import Namespace

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from confluo_core.tenancy import tenant_transaction
from tests.tenancy.conftest import ADMIN_URL, ROOT, World, _url, _with_db


def test_migrations_apply_from_empty_and_roll_back(world: World) -> None:
    """A second, throwaway database: upgrade all heads, downgrade to base, upgrade again."""
    assert ADMIN_URL
    name = "confluo_migrations_test"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"drop database if exists {name} with (force)")
        conn.execute(f"create database {name}")
    try:
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.cmd_opts = Namespace(x=[f"url={_url(_with_db(ADMIN_URL, name))}"])
        command.upgrade(cfg, "heads")
        command.downgrade(cfg, "base")
        with psycopg.connect(_with_db(ADMIN_URL, name)) as conn:
            left = conn.execute(
                "select tablename from pg_tables where schemaname = 'public'"
                " and tablename <> 'alembic_version'"
            ).fetchall()
        assert left == []
        command.upgrade(cfg, "heads")
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute(f"drop database if exists {name} with (force)")


def test_erd_matches_the_schema(world: World) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "generate_erd", ROOT / "scripts" / "generate_erd.py"
    )
    assert spec and spec.loader
    erd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(erd)
    with psycopg.connect(world.owner_url) as conn:
        expected = erd.render(conn)
    assert (ROOT / "docs" / "ERD.md").read_text() == expected, "run `make erd` and commit"


async def test_seed_creates_the_demo_tenant_and_is_repeatable(world: World) -> None:
    """Run on seven different "today"s: repeatable, and shifting appointments off
    closed days never double-books a stylist (the exclusion constraint would fail)."""
    import importlib.util
    from datetime import date, timedelta

    from psycopg import AsyncConnection

    spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts" / "seed_demo.py")
    assert spec and spec.loader
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    for offset in range(7):
        today = date(2027, 3, 1) + timedelta(days=offset)
        async with await AsyncConnection.connect(world.owner_url) as aconn, aconn.transaction():
            tenant = await seed.seed(aconn, None, today=today)
    with psycopg.connect(world.owner_url) as conn:
        counts = conn.execute(
            "select (select count(*) from tenant where slug = 'demo'),"
            " (select count(*) from crm_service where tenant_id = %(t)s),"
            " (select count(*) from crm_resource where tenant_id = %(t)s),"
            " (select count(*) from crm_availability_rule where tenant_id = %(t)s),"
            " (select count(*) from customer where tenant_id = %(t)s),"
            " (select count(*) from crm_appointment where tenant_id = %(t)s),"
            " (select count(*) from crm_conversation where tenant_id = %(t)s),"
            " (select count(*) from crm_message where tenant_id = %(t)s),"
            " (select count(*) from crm_knowledge_item where tenant_id = %(t)s and published),"
            " (select count(*) from crm_knowledge_item where tenant_id = %(t)s and body like '%%[%%')",
            {"t": tenant},
        ).fetchone()
    # tenant, services, staff, schedule rules, customers, appointments, conversations,
    # messages, published KB items, KB items with unfilled [placeholders]
    assert counts == (1, 4, 3, 13, 8, 10, 3, 8, 7, 0)


def _seed_booking_basics(conn: psycopg.Connection, tenant: uuid.UUID) -> dict[str, uuid.UUID]:
    ids = {k: uuid.uuid4() for k in ("customer", "service", "resource")}
    conn.execute(
        "insert into customer (id, tenant_id, display_name) values (%s, %s, 'Anna')",
        (ids["customer"], tenant),
    )
    conn.execute(
        "insert into crm_service (id, tenant_id, name_i18n, duration_min)"
        ' values (%s, %s, \'{"en": "Cut"}\', 30)',
        (ids["service"], tenant),
    )
    conn.execute(
        "insert into crm_resource (id, tenant_id, kind, name) values (%s, %s, 'staff', 'Eva')",
        (ids["resource"], tenant),
    )
    return ids


@pytest.fixture
def basics(world: World) -> dict[str, dict[str, uuid.UUID]]:
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        return {
            "a": _seed_booking_basics(conn, world.tenant_a),
            "b": _seed_booking_basics(conn, world.tenant_b),
        }


async def test_cannot_reference_another_tenants_rows(
    pool: AsyncConnectionPool, world: World, basics: dict[str, dict[str, uuid.UUID]]
) -> None:
    """Even with a valid id from tenant B, tenant A can't attach it: the composite
    (tenant_id, id) foreign key fails although FK checks bypass RLS."""
    a, b = basics["a"], basics["b"]
    with pytest.raises(errors.ForeignKeyViolation):
        async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
            await conn.execute(
                "insert into crm_appointment (customer_id, service_id, resource_id, during, source)"
                " values (%s, %s, %s, tstzrange(now(), now() + interval '30 min'), 'staff')",
                (b["customer"], a["service"], a["resource"]),
            )
    with pytest.raises(errors.ForeignKeyViolation):
        async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
            await conn.execute(
                "insert into customer_identity (customer_id, type, value_normalized)"
                " values (%s, 'phone', '+32470000000')",
                (b["customer"],),
            )


async def test_double_booking_is_impossible(
    pool: AsyncConnectionPool, world: World, basics: dict[str, dict[str, uuid.UUID]]
) -> None:
    a = basics["a"]
    insert = (
        "insert into crm_appointment (customer_id, service_id, resource_id, during, status, source)"
        " values (%s, %s, %s, tstzrange(%s::timestamptz, %s::timestamptz), %s, 'staff')"
    )
    slot = ("2027-03-01 10:00+01", "2027-03-01 10:30+01")
    async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
        await conn.execute(insert, (a["customer"], a["service"], a["resource"], *slot, "confirmed"))
    with pytest.raises(errors.ExclusionViolation):
        async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
            await conn.execute(
                insert,
                (
                    a["customer"],
                    a["service"],
                    a["resource"],
                    "2027-03-01 10:15+01",
                    "2027-03-01 10:45+01",
                    "pending_approval",
                ),
            )
    # Back-to-back is fine (ranges are [start, end)), and cancelled ones don't block.
    async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
        await conn.execute(
            insert,
            (
                a["customer"],
                a["service"],
                a["resource"],
                "2027-03-01 10:30+01",
                "2027-03-01 11:00+01",
                "confirmed",
            ),
        )
        await conn.execute(insert, (a["customer"], a["service"], a["resource"], *slot, "cancelled"))


async def test_vector_and_full_text_search(pool: AsyncConnectionPool, world: World) -> None:
    async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
        cur = await conn.execute(
            "insert into crm_knowledge_item (kind, title, body, language)"
            " values ('faq', 'Parking', 'Free parking behind the salon', 'en') returning id"
        )
        row = await cur.fetchone()
        assert row is not None
        near = "[" + ",".join(["1"] + ["0"] * 1023) + "]"
        far = "[" + ",".join(["0", "1"] + ["0"] * 1022) + "]"
        for pos, (content, vec) in enumerate(
            [("Free parking behind the salon", near), ("We accept cards", far)]
        ):
            await conn.execute(
                "insert into crm_knowledge_chunk"
                " (knowledge_item_id, language, position, content, embedding)"
                " values (%s, 'en', %s, %s, %s::extensions.vector)",
                (row[0], pos, content, vec),
            )
        cur = await conn.execute(
            "select content from crm_knowledge_chunk"
            " order by embedding operator(extensions.<=>) %s::extensions.vector limit 1",
            (near,),
        )
        assert await cur.fetchone() == ("Free parking behind the salon",)
        cur = await conn.execute(
            "select content from crm_knowledge_chunk"
            " where tsv @@ plainto_tsquery('simple', 'cards')"
        )
        assert await cur.fetchall() == [("We accept cards",)]
