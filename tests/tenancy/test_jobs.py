"""Webhook ledger, job queue, retries, dead letters, manual retry, tenant context."""

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from confluo_api.main import create_app
from confluo_core.auth import TokenVerifier
from confluo_core.db import make_pool
from confluo_core.job_app import build_job_app
from confluo_core.jobs import TaskSet, defer_in, run_jobs_once, tenant_task, worker_context
from confluo_core.modules import discover_modules
from confluo_core.settings import Settings, get_settings
from confluo_core.tenancy import tenant_transaction
from confluo_core.webhooks import PROVIDERS_KEY, TestProvider
from tests.conftest import MakeToken, StaticJWKS
from tests.tenancy.conftest import World

SECRET = "webhook-test-secret"
MAX_ATTEMPTS = 3

# A tenant job that fails until `flaky_state["ok"]` is set (for manual retry).
test_tasks = TaskSet()
flaky_state = {"ok": False}


@tenant_task(test_tasks, name="flaky")
async def flaky(conn: psycopg.AsyncConnection, label: str) -> None:
    if not flaky_state["ok"]:
        raise RuntimeError("upstream unavailable")
    await conn.execute("insert into location (name) values (%s)", (label,))


@tenant_task(test_tasks, name="count_locations")
async def count_locations(conn: psycopg.AsyncConnection, out: str) -> None:
    cur = await conn.execute("select distinct tenant_id from location")
    seen = [str(r[0]) for r in await cur.fetchall()]
    await conn.execute("insert into location (name) values (%s)", (f"{out}:{','.join(seen)}",))


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CONFLUO_JOB_MAX_ATTEMPTS", str(MAX_ATTEMPTS))
    monkeypatch.setenv("CONFLUO_JOB_RETRY_BASE_SECONDS", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def provider() -> TestProvider:
    return TestProvider(SECRET)


@pytest.fixture
def api(
    world: World, signing_key: ec.EllipticCurvePrivateKey, provider: TestProvider
) -> Iterator[TestClient]:
    settings = Settings(database_url=world.app_url)
    verifier = TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))
    app = create_app(settings, token_verifier=verifier, webhook_providers={"test": provider})
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def worker(world: World, provider: TestProvider) -> AsyncIterator[Any]:
    """Runs due jobs on demand, like the worker process."""
    pool = make_pool(world.app_url, max_size=4)
    await pool.open()
    app = build_job_app(discover_modules(), extra={"test": test_tasks})
    async with app.open_async(pool):
        context = worker_context(pool, **{PROVIDERS_KEY: {"test": provider}})

        class Worker:
            jobs = app
            db = pool

            async def run(self) -> None:
                await run_jobs_once(app, context)

        yield Worker()
    await pool.close()


def send(api: TestClient, provider: TestProvider, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode()
    return api.post(
        "/webhooks/test",
        content=body,
        headers={"X-Confluo-Signature": provider.sign(body), "Content-Type": "application/json"},
    )


def owner_headers(make_token: MakeToken, world: World) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {make_token(sub=str(world.user_a))}",
        "X-Tenant-Id": str(world.tenant_a),
    }


def event_row(world: World, external_id: str) -> tuple[Any, ...]:
    with psycopg.connect(world.owner_url) as conn:
        row = conn.execute(
            "select id, status, attempts, tenant_id, last_error from inbound_event"
            " where provider = 'test' and external_id = %s",
            (external_id,),
        ).fetchone()
    assert row is not None
    return tuple(row)


def jobs_for_event(world: World, event_id: uuid.UUID) -> list[tuple[Any, ...]]:
    with psycopg.connect(world.owner_url) as conn:
        return [
            tuple(r)
            for r in conn.execute(
                "select id, status::text, attempts from procrastinate.procrastinate_jobs"
                " where args->>'event_id' = %s",
                (str(event_id),),
            ).fetchall()
        ]


# --- Ingress ------------------------------------------------------------------------------


def test_duplicate_delivery_is_a_no_op(
    api: TestClient, provider: TestProvider, world: World
) -> None:
    ext = f"evt-{uuid.uuid4().hex[:8]}"
    payload = {"id": ext, "tenant_id": str(world.tenant_a), "location": "Dup street"}
    assert send(api, provider, payload).json() == {"status": "accepted"}
    assert send(api, provider, payload).json() == {"status": "duplicate"}
    assert send(api, provider, payload).json() == {"status": "duplicate"}
    event_id = event_row(world, ext)[0]
    assert len(jobs_for_event(world, event_id)) == 1


def test_signature_provider_and_payload_are_checked(
    api: TestClient, provider: TestProvider
) -> None:
    body = json.dumps({"id": "x"}).encode()
    bad = api.post("/webhooks/test", content=body, headers={"X-Confluo-Signature": "sha256=00"})
    assert bad.status_code == 401
    assert api.post("/webhooks/nope", content=body).status_code == 404
    garbage = b"not json"
    res = api.post(
        "/webhooks/test", content=garbage, headers={"X-Confluo-Signature": provider.sign(garbage)}
    )
    assert res.status_code == 400


async def test_job_deferred_in_a_rolled_back_transaction_never_exists(
    worker: Any, world: World
) -> None:
    task = worker.jobs.tasks["test:count_locations"]
    with pytest.raises(RuntimeError):
        async with tenant_transaction(worker.db, world.tenant_a) as conn:
            job_id = await defer_in(conn, task, tenant_id=str(world.tenant_a), out="never")
            raise RuntimeError("roll back")
    with psycopg.connect(world.owner_url) as conn:
        assert conn.execute(
            "select count(*) from procrastinate.procrastinate_jobs where id = %s", (job_id,)
        ).fetchone() == (0,)


# --- Processing ---------------------------------------------------------------------------


async def test_event_is_processed_in_its_tenant(
    api: TestClient, provider: TestProvider, worker: Any, world: World
) -> None:
    ext = f"evt-{uuid.uuid4().hex[:8]}"
    name = f"Webhook street {ext}"
    send(api, provider, {"id": ext, "tenant_id": str(world.tenant_a), "location": name})
    await worker.run()
    _, status, attempts, tenant, error = event_row(world, ext)
    assert (status, attempts, tenant, error) == ("processed", 1, world.tenant_a, None)
    with psycopg.connect(world.owner_url) as conn:
        assert conn.execute(
            "select tenant_id from location where name = %s", (name,)
        ).fetchone() == (world.tenant_a,)


async def test_failing_event_retries_then_is_dead_and_listed(
    api: TestClient, provider: TestProvider, worker: Any, world: World, make_token: MakeToken
) -> None:
    ext = f"evt-{uuid.uuid4().hex[:8]}"
    send(api, provider, {"id": ext, "tenant_id": str(world.tenant_a), "fail": "boom"})
    await worker.run()
    event_id, status, attempts, tenant, error = event_row(world, ext)
    assert (status, attempts, tenant) == ("dead", MAX_ATTEMPTS, world.tenant_a)
    assert error == "RuntimeError: boom"
    [(job_id, job_status, job_attempts)] = jobs_for_event(world, event_id)
    assert (job_status, job_attempts) == ("failed", MAX_ATTEMPTS)

    listed = api.get("/api/system/jobs", headers=owner_headers(make_token, world)).json()
    [entry] = [j for j in listed if j["job_id"] == job_id]
    assert entry["task_name"] == "core:process_inbound_event"
    assert entry["attempts"] == MAX_ATTEMPTS and entry["error"] == "RuntimeError: boom"

    # Another tenant doesn't see it and can't retry it.
    other = {
        "Authorization": f"Bearer {make_token(sub=str(world.user_b))}",
        "X-Tenant-Id": str(world.tenant_b),
    }
    assert job_id not in [j["job_id"] for j in api.get("/api/system/jobs", headers=other).json()]
    assert api.post(f"/api/system/jobs/{job_id}/retry", headers=other).status_code == 404


async def test_manual_retry_runs_the_job_again(
    api: TestClient, worker: Any, world: World, make_token: MakeToken
) -> None:
    flaky_state["ok"] = False
    label = f"after-retry-{uuid.uuid4().hex[:8]}"
    job_id = await worker.jobs.tasks["test:flaky"].defer_async(
        tenant_id=str(world.tenant_a), label=label
    )
    await worker.run()
    headers = owner_headers(make_token, world)
    assert job_id in [j["job_id"] for j in api.get("/api/system/jobs", headers=headers).json()]

    flaky_state["ok"] = True
    assert api.post(f"/api/system/jobs/{job_id}/retry", headers=headers).status_code == 202
    audit = api.get(
        "/api/audit", headers=headers, params={"entity": "job", "entity_id": str(job_id)}
    )
    [entry] = audit.json()
    assert (entry["action"], entry["actor_id"]) == ("retry", str(world.user_a))
    await worker.run()
    with psycopg.connect(world.owner_url) as conn:
        status = conn.execute(
            "select status::text from procrastinate.procrastinate_jobs where id = %s", (job_id,)
        ).fetchone()
        created = conn.execute(
            "select tenant_id from location where name = %s", (label,)
        ).fetchone()
    assert status == ("succeeded",)
    assert created == (world.tenant_a,)
    assert job_id not in [j["job_id"] for j in api.get("/api/system/jobs", headers=headers).json()]
    # Retrying something that isn't failed is a 404.
    assert api.post(f"/api/system/jobs/{job_id}/retry", headers=headers).status_code == 404


async def test_worker_jobs_run_with_tenant_context(worker: Any, world: World) -> None:
    out = f"ctx-{uuid.uuid4().hex[:8]}"
    await worker.jobs.tasks["test:count_locations"].defer_async(
        tenant_id=str(world.tenant_b), out=out
    )
    await worker.run()
    with psycopg.connect(world.owner_url) as conn:
        row = conn.execute(
            "select tenant_id, name from location where name like %s", (f"{out}:%",)
        ).fetchone()
    assert row is not None
    tenant, name = row
    assert tenant == world.tenant_b
    # The job saw only tenant B's rows.
    assert name.split(":", 1)[1] == str(world.tenant_b)


def test_staff_cannot_see_system_health(
    api: TestClient, world: World, make_token: MakeToken
) -> None:
    staff = uuid.uuid4()
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute("insert into app_user (id, email) values (%s, 'st@x.be')", (staff,))
        conn.execute(
            "insert into tenant_member (tenant_id, user_id, role) values (%s, %s, 'staff')",
            (world.tenant_a, staff),
        )
    h = {
        "Authorization": f"Bearer {make_token(sub=str(staff))}",
        "X-Tenant-Id": str(world.tenant_a),
    }
    assert api.get("/api/system/jobs", headers=h).status_code == 403
