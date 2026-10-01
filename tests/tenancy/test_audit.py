"""Audit trail and AI action log."""

import importlib.util
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from confluo_api.main import create_app
from confluo_core.ai_trace import AIRun, ai_node, current_trace
from confluo_core.auth import TokenVerifier
from confluo_core.settings import Settings
from confluo_core.tenancy import tenant_transaction
from tests.conftest import MakeToken, StaticJWKS
from tests.tenancy.conftest import ROOT, World
from tests.tenancy.test_rbac import FakeAuthAdmin


def _not_audited() -> set[str]:
    """Union of NOT_AUDITED from every migration that declares one."""
    skip: set[str] = set()
    for path in ROOT.glob("**/migrations/*.py"):
        if ".venv" in path.parts or path.name == "env.py":
            continue
        spec = importlib.util.spec_from_file_location(f"mig_{path.stem}", path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        skip |= set(getattr(mod, "NOT_AUDITED", ()))
    return skip


def test_every_table_has_an_audit_trigger(world: World) -> None:
    """New tables must get the trigger (or be listed in NOT_AUDITED with a reason)."""
    with psycopg.connect(world.owner_url) as conn:
        rows = conn.execute("""
            select c.relname,
                   exists (select from pg_trigger t
                           where t.tgrelid = c.oid and t.tgname = c.relname || '_audit')
            from pg_class c join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind = 'r'
        """).fetchall()
    skip = _not_audited()
    assert [t for t, has in rows if not has and t not in skip] == []


# --- Every write through the API leaves an audit row ---------------------------------


@pytest.fixture
def api(world: World, signing_key: ec.EllipticCurvePrivateKey) -> Iterator[TestClient]:
    settings = Settings(database_url=world.app_url)
    verifier = TokenVerifier(settings, jwks_client=StaticJWKS(signing_key.public_key()))
    app = create_app(settings, token_verifier=verifier, auth_admin=FakeAuthAdmin())
    with TestClient(app) as c:
        yield c


def _audit_count(world: World, tenant: str) -> int:
    with psycopg.connect(world.owner_url) as conn:
        row = conn.execute(
            "select count(*) from audit_log where tenant_id = %s", (tenant,)
        ).fetchone()
    assert row is not None
    return int(row[0])


# Write endpoints whose audit row is checked in another test (they need extra setup).
AUDITED_ELSEWHERE = {
    (
        "POST",
        "/api/system/jobs/{job_id}/retry",
    ): "test_jobs.py::test_manual_retry_runs_the_job_again",
}
# Writes that happen before any tenant or user is known; they only touch the ledger.
NO_TENANT_WRITE = {
    ("POST", "/webhooks/{provider}"): "records inbound_event; the job's writes are audited",
}


def test_every_mutating_route_writes_an_audit_row(
    api: TestClient, world: World, make_token: MakeToken
) -> None:
    owner, invitee = uuid.uuid4(), uuid.uuid4()
    owner_h = {"Authorization": f"Bearer {make_token(sub=str(owner), email='o@x.be')}"}
    invitee_h = {"Authorization": f"Bearer {make_token(sub=str(invitee), email='i@x.be')}"}
    state: dict[str, Any] = {}

    def in_tenant(h: dict[str, str]) -> dict[str, str]:
        return {**h, "X-Tenant-Id": state["tenant"]}

    def create_tenant() -> Any:
        res = api.post(
            "/api/tenants",
            headers=owner_h,
            json={"name": "Audited", "slug": f"aud-{owner.hex[:8]}"},
        )
        state["tenant"] = res.json()["id"]
        return res

    def invite() -> Any:
        res = api.post("/api/invitations", headers=in_tenant(owner_h), json={"email": "i@x.be"})
        state["invitation"] = res.json()["id"]
        return res

    def accept() -> Any:
        return api.post(f"/api/me/invitations/{state['invitation']}/accept", headers=invitee_h)

    def revoke() -> Any:
        res = api.post("/api/invitations", headers=in_tenant(owner_h), json={"email": "r@x.be"})
        return api.delete(f"/api/invitations/{res.json()['id']}", headers=in_tenant(owner_h))

    def post(url: str, key: str, body: dict[str, Any]) -> Any:
        res = api.post(url, headers=in_tenant(owner_h), json=body)
        state[key] = res.json().get("id")
        return res

    def call(method: str, url: str, **kw: Any) -> Callable[[], Any]:
        return lambda: api.request(method, url.format(**state), headers=in_tenant(owner_h), **kw)

    service = {"name_i18n": {"en": "Cut"}, "duration_min": 30}
    scenarios: dict[tuple[str, str], Callable[[], Any]] = {
        ("POST", "/api/tenants"): create_tenant,
        ("POST", "/api/locations"): lambda: post("/api/locations", "location", {"name": "Main"}),
        ("PATCH", "/api/locations/{location_id}"): call(
            "PATCH", "/api/locations/{location}", json={"address": "Kouter 1"}
        ),
        ("POST", "/api/fields"): lambda: post(
            "/api/fields",
            "field",
            {"entity": "appointment", "key": "note", "type": "text", "label_i18n": {"en": "Note"}},
        ),
        ("PATCH", "/api/fields/{field_id}"): call(
            "PATCH", "/api/fields/{field}", json={"label_i18n": {"en": "Notes"}}
        ),
        ("DELETE", "/api/fields/{field_id}"): call("DELETE", "/api/fields/{field}"),
        ("POST", "/api/crm/resources"): lambda: post(
            "/api/crm/resources", "resource", {"kind": "staff", "name": "Eva"}
        ),
        ("PUT", "/api/crm/resources/{resource_id}"): call(
            "PUT", "/api/crm/resources/{resource}", json={"kind": "staff", "name": "Eva M."}
        ),
        ("PUT", "/api/crm/resources/{resource_id}/schedule"): call(
            "PUT",
            "/api/crm/resources/{resource}/schedule",
            json={"rules": [{"weekday": 1, "start": "09:00", "end": "17:00"}]},
        ),
        ("POST", "/api/crm/resources/{resource_id}/exceptions"): lambda: post(
            f"/api/crm/resources/{state['resource']}/exceptions",
            "exception",
            {"first_day": "2030-01-02"},
        ),
        ("DELETE", "/api/crm/resources/{resource_id}/exceptions/{exception_id}"): call(
            "DELETE", "/api/crm/resources/{resource}/exceptions/{exception}"
        ),
        ("POST", "/api/crm/services"): lambda: post("/api/crm/services", "service", service),
        ("PUT", "/api/crm/services/{service_id}"): call(
            "PUT", "/api/crm/services/{service}", json=service | {"duration_min": 45}
        ),
        ("DELETE", "/api/crm/services/{service_id}"): call("DELETE", "/api/crm/services/{service}"),
        ("DELETE", "/api/locations/{location_id}"): call("DELETE", "/api/locations/{location}"),
        ("POST", "/api/crm/knowledge"): lambda: post(
            "/api/crm/knowledge",
            "kb",
            {"kind": "faq", "title": "Parking", "body": "Free parking.", "language": "en"},
        ),
        ("PUT", "/api/crm/knowledge/{item_id}"): call(
            "PUT",
            "/api/crm/knowledge/{kb}",
            json={"kind": "faq", "title": "Parking", "body": "Paid parking.", "language": "en"},
        ),
        ("POST", "/api/crm/knowledge/{item_id}/publish"): call(
            "POST", "/api/crm/knowledge/{kb}/publish"
        ),
        ("POST", "/api/crm/knowledge/{item_id}/unpublish"): call(
            "POST", "/api/crm/knowledge/{kb}/unpublish"
        ),
        ("DELETE", "/api/crm/knowledge/{item_id}"): call("DELETE", "/api/crm/knowledge/{kb}"),
        ("PUT", "/api/crm/channels/web"): call(
            "PUT", "/api/crm/channels/web", json={"enabled": True, "settings": {"color": "#123456"}}
        ),
        ("PUT", "/api/modules/{key}"): lambda: api.put(
            "/api/modules/crm",
            headers=in_tenant(owner_h),
            json={"config": {"booking_mode": "auto"}},
        ),
        ("POST", "/api/invitations"): invite,
        ("POST", "/api/me/invitations/{invitation_id}/accept"): accept,
        ("PATCH", "/api/members/{user_id}"): lambda: api.patch(
            f"/api/members/{invitee}", headers=in_tenant(owner_h), json={"role": "admin"}
        ),
        ("DELETE", "/api/invitations/{invitation_id}"): revoke,
        ("DELETE", "/api/members/{user_id}"): lambda: api.delete(
            f"/api/members/{invitee}", headers=in_tenant(owner_h)
        ),
    }

    # The OpenAPI schema lists every operation, including those of included routers.
    paths = api.get("/openapi.json").json()["paths"]
    mutating = {
        (method.upper(), path)
        for path, ops in paths.items()
        for method in ops
        if method in {"post", "put", "patch", "delete"}
    }
    mutating -= set(AUDITED_ELSEWHERE) | set(NO_TENANT_WRITE)
    assert mutating == set(scenarios), "add a scenario for every new write endpoint"

    for (method, path), run in scenarios.items():
        before = _audit_count(world, state["tenant"]) if "tenant" in state else 0
        res = run()
        assert res.status_code < 300, (method, path, res.status_code, res.text)
        assert _audit_count(world, state["tenant"]) > before, f"no audit row for {method} {path}"


def test_audit_rows_name_the_actor_ip_and_change(
    api: TestClient, world: World, make_token: MakeToken
) -> None:
    h = {
        "Authorization": f"Bearer {make_token(sub=str(world.user_a))}",
        "X-Tenant-Id": str(world.tenant_a),
    }
    loc = api.post("/api/locations", headers=h, json={"name": "Audit street"}).json()
    entries = api.get(
        "/api/audit", headers=h, params={"entity": "location", "entity_id": loc["id"]}
    )
    [entry] = entries.json()
    assert entry["action"] == "insert"
    assert entry["actor_type"] == "user"
    assert entry["actor_id"] == str(world.user_a)
    assert entry["actor_email"] == "a@example.com"
    assert entry["ip"] is None  # the test client has no IP; real requests log it
    assert entry["diff"]["new"]["name"] == "Audit street"
    assert "created_at" not in entry["diff"]["new"]


async def test_update_logs_only_changed_columns_and_system_actor(
    pool: AsyncConnectionPool, world: World
) -> None:
    async with tenant_transaction(pool, world.tenant_a) as conn:  # a worker job
        await conn.execute(
            "update location set address = 'Kouter 1' where id = %s", (world.location_a,)
        )
    with psycopg.connect(world.owner_url) as conn:
        row = conn.execute(
            "select actor_type, actor_id, action, diff from audit_log"
            " where entity = 'location' and entity_id = %s order by id desc limit 1",
            (str(world.location_a),),
        ).fetchone()
    assert row is not None
    actor, actor_id, action, diff = row
    assert (actor, actor_id, action) == ("system", None, "update")
    assert diff["new"] == {"address": "Kouter 1"}
    assert set(diff["old"]) == {"address"}


# --- Append-only ------------------------------------------------------------------------


async def test_app_role_cannot_forge_or_change_audit_rows(
    pool: AsyncConnectionPool, world: World
) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
            await conn.execute(
                "insert into audit_log (actor_type, action, entity) values ('user', 'x', 'y')"
            )
    for sql in ("update audit_log set action = 'x'", "delete from audit_log"):
        with pytest.raises(errors.InsufficientPrivilege):
            async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
                await conn.execute(sql)


def test_even_the_owner_role_cannot_change_audit_rows(world: World) -> None:
    for sql in (
        "update audit_log set action = 'x'",
        "delete from audit_log",
        "truncate audit_log",
    ):
        with psycopg.connect(world.owner_url) as conn, pytest.raises(errors.InsufficientPrivilege):
            conn.execute(sql)


def test_deleting_a_tenant_still_works(world: World) -> None:
    tid = uuid.uuid4()
    with psycopg.connect(world.owner_url, autocommit=True) as conn:
        conn.execute(
            "insert into tenant (id, name, slug) values (%s, 'Gone', %s)",
            (tid, f"gone-{tid.hex[:8]}"),
        )
        conn.execute("insert into location (tenant_id, name) values (%s, 'x')", (tid,))
        assert conn.execute(
            "select count(*) from audit_log where tenant_id = %s", (tid,)
        ).fetchone() == (2,)
        conn.execute("delete from tenant where id = %s", (tid,))
        assert conn.execute(
            "select count(*) from audit_log where tenant_id = %s", (tid,)
        ).fetchone() == (0,)


# --- AI action log --------------------------------------------------------------------------


async def test_node_decorator_records_steps(pool: AsyncConnectionPool, world: World) -> None:
    run = AIRun(pool, world.tenant_a, conversation_id=None)

    @ai_node("understand")
    async def understand(state: dict[str, Any], run: AIRun) -> dict[str, Any]:
        trace = current_trace()
        trace.add_usage("claude-fast", 120, 15)
        trace.rationale = "asks for a haircut"
        trace.confidence = 0.9
        trace.sources = [{"kb_item": "hours"}]
        return {"intent": "booking"}

    @ai_node("book")
    async def book(state: dict[str, Any], run: AIRun) -> dict[str, Any]:
        current_trace().tool = "create_appointment"
        raise RuntimeError("slot taken")

    assert await understand({"text": "Can I get a haircut Friday?"}, run) == {"intent": "booking"}
    with pytest.raises(RuntimeError):
        await book({"slot": "fri 10:00"}, run)

    async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
        cur = await conn.execute(
            "select node, tool, input, output, rationale, sources, model, input_tokens,"
            " output_tokens, confidence, outcome, latency_ms, approval_status"
            " from ai_action where run_id = %s order by created_at",
            (run.run_id,),
        )
        rows = await cur.fetchall()
    assert len(rows) == 2
    u, b = rows
    assert u[:4] == (
        "understand",
        None,
        {"text": "Can I get a haircut Friday?"},
        {"intent": "booking"},
    )
    assert u[4:11] == (
        "asks for a haircut",
        [{"kb_item": "hours"}],
        "claude-fast",
        120,
        15,
        pytest.approx(0.9),
        "ok",
    )
    assert u[11] >= 0 and u[12] == "not_required"
    assert (b[0], b[1], b[10]) == ("book", "create_appointment", "error")
    assert b[3] == {"error": "RuntimeError", "message": "slot taken"}


async def test_ai_actions_only_change_their_approval(
    pool: AsyncConnectionPool, world: World
) -> None:
    run = AIRun(pool, world.tenant_a)

    @ai_node("create_appointment")
    async def propose(state: dict[str, Any], run: AIRun) -> dict[str, Any]:
        current_trace().approval_required = True
        return {"proposed": True}

    await propose({}, run)
    async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
        cur = await conn.execute(
            "update ai_action set approval_status = 'approved', approved_by = %s where run_id = %s",
            (world.user_a, run.run_id),
        )
        assert cur.rowcount == 1
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
            await conn.execute(
                "update ai_action set output = '{}' where run_id = %s", (run.run_id,)
            )
    with pytest.raises(errors.InsufficientPrivilege):
        async with tenant_transaction(pool, world.tenant_a, world.user_a) as conn:
            await conn.execute("delete from ai_action where run_id = %s", (run.run_id,))


def test_ai_actions_endpoint(api: TestClient, world: World, make_token: MakeToken) -> None:
    h = {
        "Authorization": f"Bearer {make_token(sub=str(world.user_a))}",
        "X-Tenant-Id": str(world.tenant_a),
    }
    assert api.get("/api/ai-actions", headers=h).status_code == 400
    with psycopg.connect(world.owner_url) as conn:
        row = conn.execute(
            "select run_id from ai_action where tenant_id = %s limit 1", (world.tenant_a,)
        ).fetchone()
    if row is None:
        pytest.skip("no ai_action rows yet")
    res = api.get("/api/ai-actions", headers=h, params={"run_id": str(row[0])})
    assert res.status_code == 200 and res.json()
