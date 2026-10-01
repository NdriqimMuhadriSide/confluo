"""A fresh, migrated `confluo_test` database with two tenants, A and B.

Needs CONFLUO_TEST_DATABASE_URL: an owner connection to any database on the server
(local Supabase via `make test`, a Postgres service container in CI). The tests
themselves connect as `confluo_app`, exactly like the API and worker.
"""

import os
import uuid
from argparse import Namespace
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from confluo_core.db import make_pool
from confluo_core.job_app import build_job_app
from confluo_core.jobs import LLM_KEY, run_jobs_once, worker_context
from confluo_core.llm import LLMGateway
from confluo_core.llm.fake_provider import FakeProvider
from confluo_core.modules import discover_modules
from confluo_core.settings import Settings
from confluo_core.webhooks import PROVIDERS_KEY, default_providers
from confluo_crm.channels.web import CHECKPOINTER_KEY

ADMIN_URL = os.environ.get("CONFLUO_TEST_DATABASE_URL")
TEST_DB = "confluo_test"
APP_PASSWORD = "confluo_app"
ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class World:
    owner_url: str
    app_url: str
    tenant_a: uuid.UUID
    tenant_b: uuid.UUID
    user_a: uuid.UUID
    user_b: uuid.UUID
    location_a: uuid.UUID
    location_b: uuid.UUID


def _with_db(url: str, dbname: str, **overrides: str) -> str:
    params = {k: str(v) for k, v in conninfo_to_dict(url).items() if v is not None}
    params.update(dbname=dbname, **overrides)
    return make_conninfo("", **params)


def _url(conninfo: str) -> str:
    p = conninfo_to_dict(conninfo)
    return (
        f"postgresql://{p['user']}:{p.get('password', '')}@{p.get('host', 'localhost')}"
        f":{p.get('port', 5432)}/{p['dbname']}"
    )


@pytest.fixture(scope="session")
def world() -> Iterator[World]:
    if not ADMIN_URL:
        pytest.skip("CONFLUO_TEST_DATABASE_URL not set")
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"drop database if exists {TEST_DB} with (force)")
        conn.execute(f"create database {TEST_DB}")
    owner_url = _url(_with_db(ADMIN_URL, TEST_DB))

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.cmd_opts = Namespace(x=[f"url={owner_url}"])
    command.upgrade(cfg, "heads")

    ids = {k: uuid.uuid4() for k in ("ta", "tb", "ua", "ub", "la", "lb")}
    with psycopg.connect(owner_url, autocommit=True) as conn:
        conn.execute(f"alter role confluo_app login password '{APP_PASSWORD}'")
        conn.execute(
            "insert into tenant (id, name, slug) values (%s, 'Salon A', 'salon-a'),"
            " (%s, 'Salon B', 'salon-b')",
            (ids["ta"], ids["tb"]),
        )
        conn.execute(
            "insert into app_user (id, email) values (%s, 'a@example.com'), (%s, 'b@example.com')",
            (ids["ua"], ids["ub"]),
        )
        conn.execute(
            "insert into tenant_member (tenant_id, user_id, role)"
            " values (%s, %s, 'owner'), (%s, %s, 'owner')",
            (ids["ta"], ids["ua"], ids["tb"], ids["ub"]),
        )
        conn.execute(
            "insert into location (id, tenant_id, name) values (%s, %s, 'A Main street'),"
            " (%s, %s, 'B Station road')",
            (ids["la"], ids["ta"], ids["lb"], ids["tb"]),
        )

    yield World(
        owner_url=owner_url,
        app_url=_url(_with_db(ADMIN_URL, TEST_DB, user="confluo_app", password=APP_PASSWORD)),
        tenant_a=ids["ta"],
        tenant_b=ids["tb"],
        user_a=ids["ua"],
        user_b=ids["ub"],
        location_a=ids["la"],
        location_b=ids["lb"],
    )


@pytest.fixture
async def pool(world: World) -> AsyncIterator[AsyncConnectionPool]:
    # max_size=1 so consecutive transactions reuse the same physical connection,
    # which is what the "no leaking between requests" test relies on.
    p = make_pool(world.app_url, max_size=1)
    await p.open()
    yield p
    await p.close()


# --- Web chat / intake graph fixtures --------------------------------------------------


def fake_settings(world: World) -> Settings:
    return Settings(
        database_url=world.app_url,
        llm_fast="fake:claude-haiku-4-5",
        llm_dialogue="fake:claude-opus-5",
        llm_embedding="fake:voyage-3.5",
    )


async def make_checkpointer(world: World) -> tuple[AsyncPostgresSaver, AsyncConnectionPool]:
    pool = AsyncConnectionPool(
        world.app_url,
        min_size=1,
        max_size=2,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()
    return AsyncPostgresSaver(pool), pool  # type: ignore[arg-type]


@pytest.fixture
def chat_shop(world: World) -> dict[str, uuid.UUID]:
    """A fresh tenant with an owner and published knowledge (not yet embedded)."""
    ids = {k: uuid.uuid4() for k in ("tenant", "owner")}
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "insert into tenant (id, name, slug) values (%s, 'Salon Zon', %s)",
            (ids["tenant"], f"zon-{ids['tenant'].hex[:10]}"),
        )
        conn.execute(
            "insert into app_user (id, email) values (%s, %s)",
            (ids["owner"], f"o{ids['owner'].hex[:6]}@x.be"),
        )
        conn.execute(
            "insert into tenant_member (tenant_id, user_id, role) values (%s, %s, 'owner')",
            (ids["tenant"], ids["owner"]),
        )
    return ids


@pytest.fixture
async def chat_worker(world: World) -> AsyncIterator[Any]:
    """Runs due jobs like the worker process, with the fake LLM and a checkpointer."""
    pool = make_pool(world.app_url, max_size=6)
    await pool.open()
    settings = fake_settings(world)
    app = build_job_app(discover_modules())
    llm = LLMGateway(settings, pool, {"fake": FakeProvider()})
    saver, saver_pool = await make_checkpointer(world)
    async with app.open_async(pool):
        ctx = worker_context(
            pool,
            **{
                PROVIDERS_KEY: default_providers(settings, discover_modules()),
                LLM_KEY: llm,
                CHECKPOINTER_KEY: saver,
            },
        )

        class Worker:
            db = pool
            gateway = llm
            checkpointer = saver

            async def run(self) -> None:
                await run_jobs_once(app, ctx)

        yield Worker()
    await saver_pool.close()
    await pool.close()
